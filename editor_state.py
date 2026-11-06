"""Отделяет неполную проекцию редактора от данных, разрешённых для записи.

Отсутствующий раздел нельзя считать удалённым: ранний вход намеренно не загружает
расписание и публикацию. Полная замена доступна только после их загрузки.
"""
from flask import has_request_context, jsonify, request
from werkzeug.exceptions import BadRequest

# Только эти тяжёлые разделы допускают отложенную загрузку.
DEFERRED_FIELDS = frozenset({
    "schedule", "loads", "graphState", "publishedSnapshot", "_groupRestoreUndo",
})


def preserve_deferred_sections(incoming, existing):
    """
    Восстанавливает отсутствующие тяжёлые секции из существующего состояния.

    Предотвращает потерю нагрузок (loads) и графов при сохранении лёгких разделов.
    Возвращает объединённый словарь проекта.
    """
    if not isinstance(incoming, dict) or not isinstance(existing, dict):
        return incoming
    merged = dict(incoming)
    for field in DEFERRED_FIELDS:
        if field in existing and (field not in merged or merged[field] is None):
            merged[field] = existing[field]
    return merged


def merge_users_preserve_hashes(incoming_users, existing_users):
    """
    Сохраняет хэши паролей существующих пользователей при записи из клиента.

    Клиент не получает хэши паролей, поэтому восстанавливаем их из базы.
    """
    if not isinstance(incoming_users, list):
        return existing_users if isinstance(existing_users, list) else []
    existing_map = {str(u.get("id")): u for u in (existing_users or []) if isinstance(u, dict)}
    login_map = {str(u.get("login", "")).casefold(): u for u in (existing_users or []) if isinstance(u, dict)}
    result = []
    for user in incoming_users:
        if not isinstance(user, dict):
            continue
        uid = str(user.get("id") or "")
        login = str(user.get("login") or "").casefold()
        prev = existing_map.get(uid) or login_map.get(login) or {}
        item = dict(user)
        if not item.get("passwordHash") and prev.get("passwordHash"):
            item["passwordHash"] = prev["passwordHash"]
        result.append(item)
    return result


def requested_deferred():
    """Разбирает явный список пропусков; None означает старый полный протокол."""
    if not has_request_context() or "X-Editor-Deferred" not in request.headers:
        return None
    fields = {field.strip() for field in request.headers["X-Editor-Deferred"].split(",") if field.strip()}
    if not fields <= DEFERRED_FIELDS:
        raise BadRequest("Неизвестный отложенный раздел")
    return fields


def project_for_editor(project):
    """Удаляет только явно отложенные поля из ответа, не изменяя оригинал."""
    fields = requested_deferred()
    if fields is None or not isinstance(project, dict):
        return project
    result = {key: value for key, value in project.items() if key not in fields}
    result["_editorDeferred"] = sorted(fields)
    return result


def partial_write_error():
    """Запрещает замену полного проекта либо запись незагруженного раздела."""
    if request.method != "POST" or not request.path.startswith("/api/"):
        return None
    deferred = requested_deferred()
    if deferred is None:
        return None
    if request.endpoint == "set_key" and deferred:
        return jsonify(error="Сначала загрузите все разделы проекта", partialProject=True), 409
    if request.endpoint == "storage_section_merge":
        body = request.get_json(silent=True) or {}
        local = body.get("local") if isinstance(body, dict) else None
        if isinstance(local, dict) and (set(local) & (deferred | {"_editorDeferred"})):
            return jsonify(error="Незагруженный раздел нельзя сохранять", partialProject=True), 409
    return None


def complete_project(project):
    """Проверяет маркер даже без заголовка, затем удаляет транспортные метаданные."""
    if not isinstance(project, dict):
        return project
    if project.get("_editorDeferred"):
        from werkzeug.exceptions import Conflict
        raise Conflict("Неполный проект нельзя сохранить целиком")
    return {key: value for key, value in project.items() if key != "_editorDeferred"}
