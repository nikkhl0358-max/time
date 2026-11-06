"""Загружает рабочий проект частями, чтобы вход в редактор не передавал весь семестр.

Версию сообщает хранилище, а не identity объекта: горячие записи могут менять
вложенные данные. Публичные снимки никогда не подменяют рабочий источник.
"""
import gzip
import hashlib
import json

from flask import Response, abort, request

# Большие разделы запрашиваются после справочников и метаданных интерфейса.
DEFERRED_SECTIONS = ("loads", "schedule", "graphState", "publishedSnapshot", "_groupRestoreUndo")
SECTION_DEFAULTS = {"loads": [], "schedule": None, "graphState": {},
    "publishedSnapshot": None, "_groupRestoreUndo": None}


def revision_token(key, revision):
    """Связывает маленькую ревизию БД с ключом без хеширования всего проекта."""
    raw = json.dumps([key, revision], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def json_response(payload, status=200):
    """Формирует ETag из выбранной части, сохраняя условные GET без тела."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    etag = hashlib.sha256(body).hexdigest()
    response = Response(body, status=status, content_type="application/json; charset=utf-8")
    response.set_etag(etag, weak=True)
    if status == 200 and request.if_none_match.contains_weak(etag):
        response.status_code = 304
        response.set_data(b"")
    return response


def sanitize_users(users):
    """Удаляет хэши паролей из списка пользователей перед выдачей клиенту."""
    if not isinstance(users, list):
        return users
    fields = ("id", "name", "login", "role")
    return [{k: u.get(k, "") for k in fields} for u in users if isinstance(u, dict)]


def selected_section(project, section):
    """Возвращает bootstrap или раздел без хэшей паролей и с сохранением оригинала."""
    if section == "bootstrap":
        data = {key: value for key, value in project.items() if key not in DEFERRED_SECTIONS}
        if "users" in data:
            data["users"] = sanitize_users(data["users"])
        return data
    if section not in DEFERRED_SECTIONS:
        abort(400, description="Unknown editor section")
    return project.get(section, SECTION_DEFAULTS[section])


def snapshot(read_project, key):
    """Проверяет существование и ревизию до выдачи следующей части проекта."""
    result = read_project(key)
    if result is None:
        return None, json_response({"error": "Проект не найден"}, 404)
    project, revision = result
    if not isinstance(project, dict):
        return None, json_response({"error": "Неверный формат рабочего проекта"}, 422)
    token = revision_token(key, revision)
    if request.args.get("token") and request.args["token"] != token:
        return None, json_response({"error": "Проект изменился; повторите загрузку", "staleSnapshot": True, "token": token}, 409)
    return (project, token), None


def section_response(key, read_project, lock, cell_revisions):
    """Выдаёт одну согласованную часть рабочего проекта под блокировкой записи."""
    section = request.args.get("section", "bootstrap")
    with lock:
        state, error = snapshot(read_project, key)
        if error is not None:
            return error
        project, token = state
        payload = {"key": key, "value": selected_section(project, section), "token": token}
        if section == "bootstrap":
            payload["deferredSections"] = list(DEFERRED_SECTIONS)
            payload["value"]["_editorDeferred"] = list(DEFERRED_SECTIONS)
        else:
            payload["section"] = section
            if section == "schedule" and cell_revisions:
                payload["cellRevisions"] = cell_revisions(key)
        return json_response(payload)


def default_scope(project, instance, group_id, week):
    """Поддерживает простые fixtures; production передаёт свой authoritative matcher."""
    groups = {str(instance.get("groupId") or "")}
    groups.update(str(value) for value in instance.get("groupIds") or [])
    participants = instance.get("streamParticipants")
    if participants:
        groups.update(str(row.get("groupId") or "") for row in participants if isinstance(row, dict))
    else:
        groups.update(str(value) for value in instance.get("streamGroupIds") or [])
    return (not group_id or group_id in groups) and (not week or default_week(project, instance, week))


def default_week(project, instance, week):
    """Отбирает недели без трактовки perWeek как всего семестра."""
    pattern = instance.get("weekPattern", "all")
    if pattern == "custom":
        return str(week) in {str(value) for value in instance.get("customWeeks") or []}
    if pattern == "perWeek":
        source = instance.get("weeklyPairs") or {}
        if not source:
            load = next((row for row in project.get("loads") or [] if row.get("id") == instance.get("loadId")), {})
            source = load.get("weeklyPairs") or {}
        try:
            return float(source.get(str(week), source.get(week, 0)) or 0) > 0
        except (ValueError, TypeError):
            return False
    parity = (project.get("config") or {}).get("weekParityOverrides") or {}
    return pattern not in {"odd", "even"} or pattern == parity.get(str(week), "odd" if week % 2 else "even")


def filtered_schedule(project, group_id, week, matches_scope):
    """Фильтрует связанные индексы только по сохранившимся occurrences."""
    source = project.get("schedule")
    if not isinstance(source, dict) or not (group_id or week):
        return source
    instances = [row for row in source.get("instances") or []
        if isinstance(row, dict) and matches_scope(project, row, group_id, week)]
    identifiers = {str(row.get("instId") or "") for row in instances}
    result = dict(source)
    result["instances"] = instances
    result["assignment"] = {key: value for key, value in (source.get("assignment") or {}).items() if str(key) in identifiers}
    for field in ("locked", "unplaced"):
        result[field] = [value for value in source.get(field) or [] if str(value) in identifiers]
    return result


def scoped_revisions(revisions, schedule):
    """Не раскрывает revision IDs чужих групп в scoped ответе."""
    identifiers = {str(row.get("instId") or "") for row in (schedule or {}).get("instances") or []}
    return {key: value for key, value in revisions.items() if str(key) in identifiers}


def schedule_response(key, read_project, lock, matches_scope, cell_revisions):
    """Загружает рабочие occurrences выбранной группы/недели с общей ревизией."""
    raw_week = request.args.get("week", "")
    try:
        week = int(raw_week) if raw_week else 0
    except ValueError:
        return json_response({"error": "week должен быть положительным числом"}, 400)
    if week < 0 or (raw_week and week == 0):
        return json_response({"error": "week должен быть положительным числом"}, 400)
    with lock:
        state, error = snapshot(read_project, key)
        if error is not None:
            return error
        project, token = state
        value = filtered_schedule(project, request.args.get("groupId", ""), week, matches_scope)
        payload = {"key": key, "section": "schedule", "value": value, "token": token}
        if cell_revisions:
            payload["cellRevisions"] = scoped_revisions(cell_revisions(key), value)
        return json_response(payload)


def compress_editor_response(response):
    """Сжимает только части редактора; ETag обозначает общий JSON representation."""
    if not request.path.startswith("/api/editor/"):
        return response
    response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
    response.vary.add("Accept-Encoding")
    if response.is_streamed or response.headers.get("Content-Disposition", "").lower().startswith("attachment"):
        return response
    if response.status_code != 200 or not response.is_json or response.headers.get("Content-Encoding"):
        return response
    body = response.get_data()
    if len(body) > 1024 and request.accept_encodings["gzip"] > 0:
        compressed = gzip.compress(body, compresslevel=5, mtime=0)
        if len(compressed) < len(body):
            response.set_data(compressed)
            response.headers["Content-Encoding"] = "gzip"
    return response


def install_editor_transport(app, read_project, lock, matches_scope=default_scope, cell_revisions=None):
    """Подключает private GET; read_project(key) возвращает (project, revision) либо None.

    Callback вызывается под общей блокировкой. Production matcher обязан повторять
    правила групп/недель существующего редактора; auth guard остаётся deny-default.
    """
    app.add_url_rule("/api/editor/<key>", "editor_section",
        lambda key: section_response(key, read_project, lock, cell_revisions), methods=["GET"])
    app.add_url_rule("/api/editor/<key>/schedule", "editor_schedule",
        lambda key: schedule_response(key, read_project, lock, matches_scope, cell_revisions), methods=["GET"])
    app.after_request(compress_editor_response)
