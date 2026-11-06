"""Проверяет личность на сервере, чтобы клиентский интерфейс не был границей доступа.

Сессия подписана постоянным ключом вне Git; изменение пароля отзывает старые cookie.
Legacy SHA256 поддерживается только для входа и заменяется scrypt при успешной проверке.
"""
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import abort, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

# Публичные GET не содержат рабочего проекта, учётных записей и резервных копий.
PUBLIC_GET = frozenset({"public_control_status", "public_index", "public_status",
    "public_room_availability", "public_schedule_shard", "public_bootstrap", "app_build"})
AUTH_ENDPOINTS = frozenset({"auth_session", "auth_login", "auth_logout", "auth_setup"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOGIN_WINDOW = 900  # Окно в секундах; ограничитель рассчитан на один production worker.
LOGIN_ATTEMPTS = 10
_attempts = {}
_attempt_lock = threading.Lock()
_DUMMY_HASH = generate_password_hash("unused-constant-for-missing-user")
_REVOKED_SESSIONS = {}
_REVOKED_LOCK = threading.Lock()
SESSION_LIFETIME_SECONDS = 12 * 3600


def revoke_session(sid):
    """Добавляет идентификатор сессии в список отозванных до истечения её TTL."""
    if not sid:
        return
    with _REVOKED_LOCK:
        now = time.monotonic()
        for s, exp in list(_REVOKED_SESSIONS.items()):
            if now > exp:
                _REVOKED_SESSIONS.pop(s, None)
        _REVOKED_SESSIONS[sid] = now + SESSION_LIFETIME_SECONDS


def is_session_revoked(sid):
    """Проверяет, была ли сессия отозвана сервером при выходе."""
    if not sid:
        return True
    with _REVOKED_LOCK:
        exp = _REVOKED_SESSIONS.get(sid)
        if exp is None:
            return False
        if time.monotonic() > exp:
            _REVOKED_SESSIONS.pop(sid, None)
            return False
        return True


def session_key(data_dir):
    """Читает постоянный ключ или создаёт его с доступом только владельцу."""
    explicit = os.environ.get("AUTH_SECRET_KEY", "")
    if explicit:
        if len(explicit) < 32:
            raise RuntimeError("AUTH_SECRET_KEY must contain at least 32 characters")
        return explicit
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    path = os.path.join(data_dir, ".auth-session-key")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="ascii") as source:
            key = source.read().strip()
        if len(key) < 64:
            raise RuntimeError("Invalid persistent authentication key")
        return key
    key = secrets.token_hex(32)
    with os.fdopen(fd, "w", encoding="ascii") as target:
        target.write(key)
        target.flush()
        os.fsync(target.fileno())
    return key


def public_user(user):
    """Возвращает только поля личности, не раскрывая пароль или его хеш."""
    return {key: user.get(key, "") for key in ("id", "name", "login", "role")}


def password_stamp(user):
    """Связывает cookie с текущим хешем пароля для отзыва старых сессий."""
    return hashlib.sha256(str(user.get("passwordHash") or "").encode()).hexdigest()


def password_matches(user, password):
    """Проверяет scrypt либо старый SHA256 без сравнения секретов через ==."""
    encoded = str(user.get("passwordHash") or "")
    if re.fullmatch(r"[a-fA-F0-9]{64}", encoded):
        candidate = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(encoded.lower(), candidate)
    try:
        return check_password_hash(encoded or _DUMMY_HASH, password)
    except (ValueError, TypeError):
        return False


def establish_session(user):
    """Меняет личность целиком после входа, создавая уникальный session_id."""
    session.clear()
    session.permanent = True
    sid = secrets.token_hex(16)
    session.update(user_id=str(user["id"]), password_stamp=password_stamp(user), session_id=sid)
    reset_login_attempts()


def current_user(users):
    """На каждом запросе сверяет cookie с актуальной ролью, паролем и отзывом."""
    identifier = session.get("user_id")
    sid = session.get("session_id")
    if not identifier or (sid and is_session_revoked(sid)):
        return None
    for user in users:
        if str(user.get("id")) != identifier:
            continue
        if user.get("role") not in {"admin", "superadmin"}:
            break
        if hmac.compare_digest(str(session.get("password_stamp", "")), password_stamp(user)):
            if not sid:
                session["session_id"] = secrets.token_hex(16)
            return user
    return None


def is_local_dev(origin, expected):
    """Разрешает связку локального dev-сервера фронтенда и API без HTTPS."""
    if current_app.config["SESSION_COOKIE_SECURE"]:
        return False
    hosts = {"127.0.0.1", "localhost"}
    return origin.hostname in hosts and expected.hostname in hosts


def same_origin():
    """Отсекает браузерные cross-site изменения, включая login CSRF.

    CLI без Origin допустим: cookie не прикрепляется к нему браузером автоматически.
    Браузер без Origin обязан подтвердить тот же origin через Referer/Fetch Metadata.
    """
    if request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
        return False
    expected = urlsplit(request.host_url)
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if value:
            origin = urlsplit(value)
            # TLS завершается на reverse proxy: HTTPS допустим при Secure cookie.
            scheme_ok = origin.scheme == expected.scheme or (
                origin.scheme == "https" and current_app.config["SESSION_COOKIE_SECURE"]
            )
            netloc_ok = origin.netloc == expected.netloc or is_local_dev(origin, expected)
            return origin.scheme in {"http", "https"} and netloc_ok and scheme_ok
    return request.headers.get("Sec-Fetch-Site") in {None, "same-origin", "none"}


def _prune_login_attempts(now):
    """Удаляет устаревшие попытки входа и защищает память от переполнения."""
    for address in list(_attempts):
        _attempts[address] = [t for t in _attempts[address] if now - t < LOGIN_WINDOW]
        if not _attempts[address]:
            _attempts.pop(address, None)
    if len(_attempts) > 2048:
        oldest = sorted(_attempts.keys(), key=lambda k: min(_attempts[k]))[:len(_attempts) - 2048]
        for k in oldest:
            _attempts.pop(k, None)


def login_allowed():
    """Ограничивает вход по адресу соединения без риска глобального отказа."""
    now = time.monotonic()
    key = request.remote_addr or "unknown"
    with _attempt_lock:
        _prune_login_attempts(now)
        if len(_attempts.get(key, [])) >= LOGIN_ATTEMPTS:
            return False
        _attempts.setdefault(key, []).append(now)
    return True


def reset_login_attempts():
    """Не блокирует коллег за общим reverse proxy после успешного входа."""
    with _attempt_lock:
        _attempts.pop(request.remote_addr or "unknown", None)


def json_body():
    """Принимает только объект JSON и короткие текстовые значения авторизации."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="JSON object required")
    if any(not isinstance(value, str) or len(value) > 1024 for value in body.values()):
        abort(400, description="Invalid authentication fields")
    return body


def validate_new_password(password):
    """Применяет минимальную длину к новым паролям, сохраняя вход старым."""
    if len(password) < 8:
        abort(400, description="Пароль должен содержать минимум 8 символов")


def guard_api(read_users):
    """Закрывает неизвестные API по умолчанию и проверяет источник изменений."""
    if not request.path.startswith("/api/"):
        return None
    if request.method not in SAFE_METHODS and not same_origin():
        return jsonify(error="Запрос с другого источника запрещён"), 403
    if request.endpoint in AUTH_ENDPOINTS:
        return None
    if request.method in {"GET", "HEAD"} and request.endpoint in PUBLIC_GET:
        return None
    users = read_users()
    user = current_user(users)
    if user is None:
        return jsonify(error="Требуется вход сотрудника"), 401
    g.auth_user = user
    g.auth_users = copy.deepcopy(users)
    superadmin_endpoints = {"storage_backup_restore", "storage_backup_download", "storage_backup_create"}
    if request.endpoint in superadmin_endpoints and user.get("role") != "superadmin":
        return jsonify(error="Требуются права главного администратора"), 403
    return None


def install_auth(app, data_dir, read_users, update_users, lock):
    """Настраивает cookie и маршруты с callbacks для атомарного хранилища."""
    app.secret_key = session_key(data_dir)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_REQUEST_BYTES", 128 * 1024 * 1024))
    app.config.update(SESSION_COOKIE_NAME="timetable_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=os.environ.get("AUTH_COOKIE_SECURE", "1") != "0",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12), SESSION_REFRESH_EACH_REQUEST=False)
    app.before_request(lambda: guard_api(read_users))
    app.add_url_rule("/api/auth/session", "auth_session", lambda: session_response(read_users))
    app.add_url_rule("/api/auth/login", "auth_login", lambda: login_response(read_users, update_users, lock), methods=["POST"])
    app.add_url_rule("/api/auth/logout", "auth_logout", logout_response, methods=["POST"])
    app.add_url_rule("/api/auth/setup", "auth_setup", lambda: setup_response(read_users, update_users, lock), methods=["POST"])
    app.add_url_rule("/api/auth/password", "auth_password", lambda: password_response(read_users, update_users, lock), methods=["POST"])


def session_response(read_users):
    """Сообщает состояние входа без выдачи списка пользователей до входа."""
    users = read_users()
    user = current_user(users)
    response = jsonify(user=public_user(user) if user else None, setupRequired=not users)
    response.headers["Cache-Control"] = "no-store"
    return response


def login_response(read_users, update_users, lock):
    """Проверяет пароль на сервере и обновляет устаревший SHA256 атомарно."""
    if not login_allowed():
        return jsonify(error="Слишком много попыток входа; повторите через 15 минут"), 429
    body = json_body()
    login, password = body.get("login", "").strip().casefold(), body.get("password", "")
    with lock:
        users = read_users()
        user = next((u for u in users if str(u.get("login", "")).casefold() == login), None)
        if not password_matches(user or {}, password) or not user or user.get("role") not in {"admin", "superadmin"}:
            return jsonify(error="Неверный логин или пароль"), 401
        if re.fullmatch(r"[a-fA-F0-9]{64}", str(user.get("passwordHash", ""))):
            user["passwordHash"] = generate_password_hash(password)
            update_users(users)
        establish_session(user)
    return jsonify(user=public_user(user))


def logout_response():
    """Отзывает серверную сессию и удаляет cookie в браузере."""
    sid = session.get("session_id")
    if sid:
        revoke_session(sid)
    session.clear()
    return jsonify(ok=True)


def setup_response(read_users, update_users, lock):
    """Создаёт первого superadmin только по секрету оператора и пустой базе."""
    if not login_allowed():
        return jsonify(error="Too many attempts"), 429
    body = json_body()
    token = os.environ.get("AUTH_SETUP_TOKEN", "")
    if not token or not hmac.compare_digest(body.get("setupToken", "").encode(), token.encode()):
        return jsonify(error="Первичная настройка требует AUTH_SETUP_TOKEN"), 403
    validate_new_password(body.get("password", ""))
    if not body.get("name", "").strip() or not body.get("login", "").strip():
        return jsonify(error="Укажите имя и логин"), 400
    with lock:
        if read_users():
            return jsonify(error="Учётная запись уже настроена"), 409
        user = dict(id=secrets.token_hex(16), name=body["name"].strip(), login=body["login"].strip(),
            role="superadmin", passwordHash=generate_password_hash(body["password"]))
        update_users([user])
        establish_session(user)
    return jsonify(user=public_user(user))


def password_response(read_users, update_users, lock):
    """Меняет собственный пароль после повторной проверки и отзывает старые cookie."""
    body = json_body()
    validate_new_password(body.get("password", ""))
    if not login_allowed():
        return jsonify(error="Too many attempts"), 429
    with lock:
        users = read_users()
        user = current_user(users)
        if user is None or not password_matches(user, body.get("oldPassword", "")):
            return jsonify(error="Текущий пароль неверен"), 403
        user["passwordHash"] = generate_password_hash(body["password"])
        update_users(users)
        establish_session(user)
    return jsonify(user=public_user(user))
