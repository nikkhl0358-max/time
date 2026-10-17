import os
import json
import threading
import shutil
import subprocess
import uuid
import time
import gzip
import hashlib
import tempfile
import select
from collections import OrderedDict
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

# В контейнере путь задаётся явно через DATA_DIR=/data. Локально без переменной
# сохраняем данные рядом с приложением.
DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if "AMVERA" in os.environ else os.path.join(os.path.dirname(__file__), "data"))
DATA_FILE = os.path.join(DATA_DIR, "storage.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
MAX_BACKUPS = 50

_lock = threading.Lock()
_presence_lock = threading.Lock()
_presence = {}  # session_id -> user info + last_seen
PRESENCE_TTL_SECONDS = 75

_generation_jobs = {}
_generation_jobs_lock = threading.Lock()
_generation_run_lock = threading.Lock()
GENERATION_JOB_TTL_SECONDS = 3600
GENERATION_FINISHED_TTL_SECONDS = 300
GENERATION_CONSUMED_TTL_SECONDS = 60
GENERATION_TIMEOUT_SECONDS = int(os.environ.get("GENERATION_TIMEOUT_SECONDS", "900"))
SCHEDULER_SCRIPT = os.path.join(os.path.dirname(__file__), "server_scheduler.mjs")
GRAPH_SCHEDULER_SCRIPT = os.path.join(os.path.dirname(__file__), "server_graph_scheduler.mjs")


# v1575: persistent Node workers. They are computation caches only: canonical data
# remains storage.json and every generation progress patch is still committed by Python.
# Killing/restarting a worker cannot erase schedule data.
class _PersistentSchedulerWorker:
    def __init__(self, script_path, name):
        self.script_path = script_path
        self.name = name
        self.lock = threading.Lock()
        self.proc = None
        self.loaded_cache_key = None

    def _stop_unlocked(self):
        proc = self.proc
        self.proc = None
        self.loaded_cache_key = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def stop(self):
        # Cancellation must be able to kill a long-running request from another
        # thread. Do not wait for request()'s serialization lock here.
        proc = self.proc
        self.proc = None
        self.loaded_cache_key = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def _ensure_unlocked(self):
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        self._stop_unlocked()
        self.proc = subprocess.Popen(
            ["node", self.script_path, "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.loaded_cache_key = None
        return self.proc

    def request(self, payload, cache_key, data, timeout):
        request_id = uuid.uuid4().hex
        with self.lock:
            proc = self._ensure_unlocked()
            msg = {"requestId": request_id, "cacheKey": cache_key, "payload": payload}
            # Send the full canonical snapshot only when this worker has not already
            # normalized the exact same storage fingerprint.
            if self.loaded_cache_key != cache_key:
                msg["data"] = data
            try:
                proc.stdin.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
                proc.stdin.flush()
            except Exception:
                self._stop_unlocked()
                raise RuntimeError(f"{self.name}: не удалось отправить задачу worker")
            deadline = time.time() + max(1, float(timeout))
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._stop_unlocked()
                    raise subprocess.TimeoutExpired(["node", self.script_path, "--worker"], timeout)
                ready, _, _ = select.select([proc.stdout], [], [], min(0.25, remaining))
                if not ready:
                    if proc.poll() is not None:
                        self._stop_unlocked()
                        raise RuntimeError(f"{self.name}: worker неожиданно завершился")
                    continue
                line = proc.stdout.readline()
                if not line:
                    self._stop_unlocked()
                    raise RuntimeError(f"{self.name}: worker не вернул ответ")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._stop_unlocked()
                    raise RuntimeError(f"{self.name}: повреждённый ответ worker: {exc}")
                if str(response.get("requestId") or "") != request_id:
                    continue
                if not response.get("ok"):
                    # A cache miss after an external restart is retried once with data.
                    err = str(response.get("error") or "scheduler worker error")
                    self.loaded_cache_key = None
                    raise RuntimeError(err[-4000:])
                self.loaded_cache_key = cache_key
                return response

_scheduler_generate_worker = _PersistentSchedulerWorker(SCHEDULER_SCRIPT, "scheduler-generate")
_scheduler_options_worker = _PersistentSchedulerWorker(SCHEDULER_SCRIPT, "scheduler-options")

# v1564: маленький LRU-кэш серверных вариантов ручной постановки.
# Ключ содержит отпечаток актуального storage, поэтому любое реальное изменение
# расписания/графика/ограничений автоматически делает старый результат неприменимым.
_schedule_options_cache = OrderedDict()
_schedule_options_cache_lock = threading.Lock()
SCHEDULE_OPTIONS_CACHE_MAX = int(os.environ.get("SCHEDULE_OPTIONS_CACHE_MAX", "256"))

def _schedule_options_cache_get(key):
    with _schedule_options_cache_lock:
        value = _schedule_options_cache.get(key)
        if value is None:
            return None
        _schedule_options_cache.move_to_end(key)
        return value

def _schedule_options_cache_put(key, value):
    with _schedule_options_cache_lock:
        _schedule_options_cache[key] = value
        _schedule_options_cache.move_to_end(key)
        while len(_schedule_options_cache) > SCHEDULE_OPTIONS_CACHE_MAX:
            _schedule_options_cache.popitem(last=False)



def load_store():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            # Повреждённое хранилище нельзя трактовать как пустое: иначе следующий POST
            # мог бы затереть рабочие данные. Останавливаем запись и требуем восстановление.
            raise RuntimeError(f"storage.json is corrupted: {exc}")


def _is_valid_working_value(value):
    """Рабочее расписание не может быть null/пустой строкой/пустым объектом."""
    if value is None:
        return False
    if isinstance(value, str):
        if not value.strip():
            return False
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(parsed, dict) and bool(parsed)
    return isinstance(value, dict) and bool(value)


def _latest_valid_backup_for_key(key):
    if not os.path.isdir(BACKUP_DIR):
        return None, None
    names = sorted(
        (name for name in os.listdir(BACKUP_DIR) if name.startswith("storage_") and name.endswith(".json")),
        reverse=True,
    )
    for name in names:
        path = os.path.join(BACKUP_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                store = json.load(f)
            value = store.get(key)
            if _is_valid_working_value(value):
                return name, store
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None, None


def _backup_current_store():
    if not os.path.exists(DATA_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%fZ")
    target = os.path.join(BACKUP_DIR, f"storage_{stamp}.json")
    shutil.copy2(DATA_FILE, target)
    backups = sorted(
        (os.path.join(BACKUP_DIR, name) for name in os.listdir(BACKUP_DIR) if name.startswith("storage_") and name.endswith(".json")),
        key=os.path.getmtime,
        reverse=True,
    )
    for old_path in backups[MAX_BACKUPS:]:
        try:
            os.remove(old_path)
        except OSError:
            pass


def save_store(store, backup=True):
    os.makedirs(DATA_DIR, exist_ok=True)
    if backup:
        _backup_current_store()
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


@app.get("/api/storage/<key>")
def get_key(key):
    with _lock:
        store = load_store()
        if key not in store:
            return jsonify({"error": "not found"}), 404

        value = store[key]
        # schedule-data-v2 никогда не должен быть пустым. Если основной ключ повреждён,
        # безопасно восстанавливаем последнюю ПРОВЕРЕННУЮ копию. Текущий файл сначала
        # сохраняется в backup, поэтому даже это восстановление обратимо.
        if key == "schedule-data-v2" and not _is_valid_working_value(value):
            backup_name, backup_store = _latest_valid_backup_for_key(key)
            if backup_store is not None:
                _backup_current_store()
                save_tmp = DATA_FILE + ".restore.tmp"
                with open(save_tmp, "w", encoding="utf-8") as f:
                    json.dump(backup_store, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(save_tmp, DATA_FILE)
                return jsonify({
                    "key": key,
                    "value": backup_store[key],
                    "recovered": True,
                    "backup": backup_name,
                })
            return jsonify({
                "error": "working key is empty and no valid backup was found",
                "empty": True,
            }), 409

    return jsonify({"key": key, "value": value})


@app.get("/api/storage-meta/<key>")
def get_key_meta(key):
    """Lightweight change check: never sends the full semester payload."""
    with _lock:
        store = load_store()
        if key not in store:
            return jsonify({"error": "not found"}), 404
        value = store[key]
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except Exception:
        parsed = None
    meta = parsed.get("_syncMeta", {}) if isinstance(parsed, dict) else {}
    token = str(meta.get("saveId") or meta.get("at") or "")
    try:
        size = len(value.encode("utf-8")) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except Exception:
        size = 0
    return jsonify({"key": key, "token": token, "meta": meta, "size": size})




MIN_SCHEDULE_CLIENT_BUILD = 1756

def _schedule_client_build_ok():
    raw = str(request.headers.get("X-Client-Build") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 0
    return value >= MIN_SCHEDULE_CLIENT_BUILD

def _stale_schedule_client_response():
    return jsonify({
        "error": "Открыта устаревшая версия редактора расписания. Страница будет обновлена; повторите действие после обновления.",
        "staleClient": True,
        "requiredBuild": MIN_SCHEDULE_CLIENT_BUILD,
    }), 409

@app.post("/api/storage/<key>")
def set_key(key):
    allow_create = request.args.get("allow_create") == "1"
    raw_mode = request.args.get("raw") == "1"
    if raw_mode:
        raw_bytes = request.get_data(cache=False)
        if request.headers.get("Content-Encoding", "").lower() == "gzip" and raw_bytes[:2] == b"\x1f\x8b":
            try:
                raw_bytes = gzip.decompress(raw_bytes)
            except (OSError, EOFError) as exc:
                return jsonify({"error": f"Не удалось распаковать данные сохранения: {exc}"}), 400
        try:
            incoming_value = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "Данные сохранения имеют неверную кодировку"}), 400
    else:
        body = request.get_json(force=True, silent=True) or {}
        incoming_value = body.get("value")
    if key == "schedule-data-v2" and not _is_valid_working_value(incoming_value):
        return jsonify({
            "error": "Пустое или повреждённое значение рабочего расписания отклонено. Существующие данные не изменены."
        }), 422
    with _lock:
        # Пересборка/перезапуск приложения НИКОГДА не должна молча создавать новое
        # storage.json. Если постоянный диск почему-то не примонтирован или файл пропал,
        # обычное автосохранение блокируется. Создание разрешается только явным действием
        # пользователя на действительно новом экземпляре.
        if not os.path.exists(DATA_FILE) and not allow_create:
            return jsonify({"error": "Хранилище отсутствует. Автосохранение заблокировано, чтобы не затереть данные после пересборки."}), 409
        store = load_store()

        # v145.4: optimistic concurrency control. Клиент передаёт токен версии,
        # от которой он редактировал. Если за это время другой сотрудник уже сохранил
        # новую версию, старый полный снимок НЕ имеет права молча её затереть.
        expected_token = str(request.headers.get("X-Base-Token") or request.args.get("base_token") or "")
        if expected_token and key in store:
            current_value = store.get(key)
            try:
                current_parsed = json.loads(current_value) if isinstance(current_value, str) else current_value
            except Exception:
                current_parsed = None
            current_meta = current_parsed.get("_syncMeta", {}) if isinstance(current_parsed, dict) else {}
            current_token = str(current_meta.get("saveId") or current_meta.get("at") or "")
            if current_token and current_token != expected_token:
                return jsonify({
                    "error": "Данные на сервере изменились другим пользователем. Требуется объединение версий.",
                    "conflict": True,
                    "token": current_token,
                    "meta": current_meta,
                }), 409

        # v1756: generic full-project saves are never allowed to replace the
        # working timetable. Old tabs and non-schedule pages can carry a stale
        # schedule snapshot for many minutes; accepting it is what made lessons
        # disappear/reappear in waves between administrators. Schedule mutations
        # must go through the atomic schedule endpoints below. Explicit first-time
        # creation is the only generic-write exception.
        if key == "schedule-data-v2" and key in store and not allow_create:
            try:
                remote_existing = json.loads(store[key]) if isinstance(store[key], str) else store[key]
            except Exception:
                remote_existing = None
            if isinstance(remote_existing, dict) and isinstance(incoming_value, dict) and "schedule" in remote_existing:
                incoming_value = dict(incoming_value)
                incoming_value["schedule"] = remote_existing.get("schedule")
        store[key] = incoming_value
        save_store(store)
    if raw_mode:
        try:
            parsed = json.loads(incoming_value) if isinstance(incoming_value, str) else incoming_value
            meta = parsed.get("_syncMeta", {}) if isinstance(parsed, dict) else {}
        except Exception:
            meta = {}
        return jsonify({"key": key, "ok": True, "token": str(meta.get("saveId") or meta.get("at") or "")})
    return jsonify({"key": key, "value": store[key]})



@app.get("/api/public-bootstrap")
def public_bootstrap():
    """Lightweight payload for the public landing page.

    Do not send editor-only graph/history/version/user records to every public visitor.
    The full project is loaded only after an employee opens the admin login flow.
    """
    try:
        store = load_store()
        raw = store.get("schedule-data-v2")
        project = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(project, dict):
            return jsonify({"error": "Рабочие данные отсутствуют"}), 404
        published = project.get("publishedSnapshot")
        if isinstance(published, dict) and published:
            public_data = dict(published)
        else:
            keys = [
                "config", "specialties", "groups", "teachers", "departments", "subjects",
                "lessonTypes", "rooms", "schedule", "loads", "practices", "substitutions",
                "retakes", "interimAssessments", "attestation", "consultations", "announcements",
            ]
            public_data = {k: project.get(k) for k in keys if k in project}
        public_data["publicIntroText"] = project.get("publicIntroText", "")
        public_data["announcements"] = project.get("announcements", public_data.get("announcements", [])) or []
        public_data["consultations"] = project.get("consultations", public_data.get("consultations", [])) or []
        return jsonify({
            "data": public_data,
            "userCount": len(project.get("users") or []),
            "publishedAt": (published or {}).get("_syncMeta", {}).get("at") if isinstance(published, dict) else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

def _active_presence():
    import time
    now = time.time()
    with _presence_lock:
        stale = [sid for sid, item in _presence.items() if now - float(item.get("last_seen", 0)) > PRESENCE_TTL_SECONDS]
        for sid in stale:
            _presence.pop(sid, None)
        sessions = list(_presence.values())

    # Один пользователь может открыть несколько вкладок. В интерфейсе показываем его один раз,
    # но сохраняем число активных сессий для возможной диагностики.
    by_user = {}
    counts = {}
    for item in sessions:
        user_id = item.get("userId") or item.get("login") or item.get("sessionId")
        counts[user_id] = counts.get(user_id, 0) + 1
        current = by_user.get(user_id)
        if current is None or item.get("last_seen", 0) > current.get("last_seen", 0):
            by_user[user_id] = dict(item)
    result = []
    for user_id, item in by_user.items():
        item.pop("sessionId", None)
        item["lastSeen"] = item.pop("last_seen", now)
        editing_at = float(item.pop("editing_at", 0) or 0)
        if item.get("editing") and (not editing_at or now - editing_at > 8):
            item["editing"] = None
        item["sessions"] = counts.get(user_id, 1)
        result.append(item)
    result.sort(key=lambda x: (x.get("name") or x.get("login") or "").lower())
    return result


@app.post("/api/presence/heartbeat")
def presence_heartbeat():
    import time
    body = request.get_json(force=True, silent=True) or {}
    session_id = str(body.get("sessionId") or "").strip()
    user_id = str(body.get("userId") or "").strip()
    if not session_id or not user_id:
        return jsonify({"error": "sessionId and userId are required"}), 400
    now = time.time()
    editing = body.get("editing") if isinstance(body.get("editing"), dict) else None
    item = {
        "sessionId": session_id,
        "userId": user_id,
        "name": str(body.get("name") or "").strip(),
        "login": str(body.get("login") or "").strip(),
        "role": str(body.get("role") or "admin").strip(),
        "tab": str(body.get("tab") or "").strip(),
        "editing": editing,
        "editing_at": now if editing else 0,
        "last_seen": now,
    }
    with _presence_lock:
        _presence[session_id] = item
    return jsonify({"ok": True, "active": _active_presence()})


@app.get("/api/presence")
def presence_list():
    return jsonify({"active": _active_presence(), "ttl": PRESENCE_TTL_SECONDS})


@app.post("/api/presence/leave")
def presence_leave():
    body = request.get_json(force=True, silent=True) or {}
    session_id = str(body.get("sessionId") or "").strip()
    if session_id:
        with _presence_lock:
            _presence.pop(session_id, None)
    return jsonify({"ok": True})



def _cleanup_generation_jobs():
    """Keep active jobs, but release large finished results quickly."""
    now = time.time()
    with _generation_jobs_lock:
        stale = []
        for job_id, job in _generation_jobs.items():
            created = float(job.get("created_at", now))
            finished = float(job.get("finished_at", 0) or 0)
            consumed = float(job.get("consumed_at", 0) or 0)
            if consumed and now - consumed > GENERATION_CONSUMED_TTL_SECONDS:
                stale.append(job_id)
            elif finished and now - finished > GENERATION_FINISHED_TTL_SECONDS:
                stale.append(job_id)
            elif not finished and now - created > GENERATION_JOB_TTL_SECONDS:
                stale.append(job_id)
        for job_id in stale:
            job = _generation_jobs.pop(job_id, None)
            progress_file = job.get("progress_file") if isinstance(job, dict) else None
            if progress_file:
                try:
                    os.remove(progress_file)
                except OSError:
                    pass



def _apply_generation_group_progress(storage_key, progress, backup=False):
    """Atomically persist the latest cumulative group batch snapshot.

    The progress patch contains only the target group's exclusive instances.
    Other groups, graph data and all other project sections are left untouched.

    v1576: `backup` defaults to False here. This function can be called
    hundreds of times over a single long full-schedule recalculation (roughly
    every ~1-3 seconds); backing up (a full file copy of storage.json plus a
    backups-directory listing) on every one of those internal checkpoints was
    turning `_lock` into a near-permanently-held lock for the whole duration
    of a full recalculation, starving every other request that touches
    storage and producing HTTP 502 / "Failed to fetch" for other users. A
    single backup is taken once up front in `_run_generation_job` instead.
    """
    if not isinstance(progress, dict):
        return False
    patch = progress.get("patch") or {}
    if not isinstance(patch, dict):
        return False
    full_scope = str(patch.get("scope") or "").strip().lower() == "full"
    replace_ids = {str(x) for x in (patch.get("replaceIds") or []) if str(x)}
    instances_set = patch.get("instancesSet") or {}
    assignment_set = patch.get("assignmentSet") or {}
    unplaced_target = [str(x) for x in (patch.get("unplacedTarget") or []) if str(x)]
    locked_target = [str(x) for x in (patch.get("lockedTarget") or []) if str(x)]
    instances_remove = {str(x) for x in (patch.get("instancesRemove") or []) if str(x)}
    assignment_remove = {str(x) for x in (patch.get("assignmentRemove") or []) if str(x)}
    unplaced_set = [str(x) for x in (patch.get("unplacedSet") or []) if str(x)]
    unplaced_remove = {str(x) for x in (patch.get("unplacedRemove") or []) if str(x)}
    locked_set = [str(x) for x in (patch.get("lockedSet") or []) if str(x)]
    locked_remove = {str(x) for x in (patch.get("lockedRemove") or []) if str(x)}
    if not isinstance(instances_set, dict) or not isinstance(assignment_set, dict):
        return False

    with _lock:
        store = load_store()
        raw = store.get(storage_key)
        if raw is None:
            return False
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return False
        if not isinstance(remote, dict):
            return False
        schedule = dict(remote.get("schedule") or {})

        existing_instances = [dict(x) for x in (schedule.get("instances") or []) if isinstance(x, dict)]
        by_id = {}
        order = []
        for inst in existing_instances:
            sid = str(inst.get("instId") or "")
            if not sid or sid in replace_ids or (full_scope and sid in instances_remove):
                continue
            by_id[sid] = inst
            order.append(sid)
        for sid, inst in instances_set.items():
            sid = str(sid)
            if not sid or not isinstance(inst, dict):
                continue
            if sid not in by_id:
                order.append(sid)
            by_id[sid] = dict(inst)
        schedule["instances"] = [by_id[sid] for sid in order if sid in by_id]

        assignment = {str(k): v for k, v in (schedule.get("assignment") or {}).items() if str(k) not in replace_ids and (not full_scope or str(k) not in assignment_remove)}
        for sid, value in assignment_set.items():
            if sid and isinstance(value, dict):
                assignment[str(sid)] = value
        schedule["assignment"] = assignment

        if full_scope:
            schedule["unplaced"] = [str(x) for x in (schedule.get("unplaced") or []) if str(x) not in unplaced_remove]
            schedule["unplaced"].extend([x for x in unplaced_set if x not in schedule["unplaced"]])
            schedule["locked"] = [str(x) for x in (schedule.get("locked") or []) if str(x) not in locked_remove]
            schedule["locked"].extend([x for x in locked_set if x not in schedule["locked"]])
        else:
            schedule["unplaced"] = [str(x) for x in (schedule.get("unplaced") or []) if str(x) not in replace_ids]
            schedule["unplaced"].extend([x for x in unplaced_target if x not in schedule["unplaced"]])
            schedule["locked"] = [str(x) for x in (schedule.get("locked") or []) if str(x) not in replace_ids]
            schedule["locked"].extend([x for x in locked_target if x not in schedule["locked"]])
        if isinstance(patch.get("stats"), dict):
            schedule["stats"] = {**(schedule.get("stats") or {}), **patch.get("stats")}

        remote["schedule"] = schedule
        remote["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-generate-progress-" + uuid.uuid4().hex,
            "userId": "",
            "by": "Авторасчёт",
            "scope": "schedule",
        }
        store[storage_key] = json.dumps(remote, ensure_ascii=False)
        save_store(store, backup=backup)
    return True

def _run_generation_job(job_id, payload, script_path=SCHEDULER_SCRIPT):
    with _generation_run_lock:
        with _generation_jobs_lock:
            job = _generation_jobs.get(job_id)
            if not job:
                return
            if job.get("cancel_requested") or job.get("status") == "canceled":
                job.update({"status": "canceled", "phase": "canceled", "finished_at": time.time()})
                return
            job.update({"status": "running", "phase": "calculating", "started_at": time.time()})
        started = time.time()
        progress_file = None
        run_payload = dict(payload or {})
        storage_key = str(run_payload.get("storageKey") or "schedule-data-v2")
        if script_path == SCHEDULER_SCRIPT:
            progress_file = os.path.join("/tmp", f"timetable-generation-progress-{job_id}.json")
            run_payload["progressFile"] = progress_file
            try:
                os.remove(progress_file)
            except OSError:
                pass
            with _generation_jobs_lock:
                job = _generation_jobs.get(job_id)
                if job is not None:
                    job["progress_file"] = progress_file
                    job["progress_history"] = []
            # v1576: take exactly one backup of the pre-recalculation state here,
            # instead of on every progress-patch commit (see
            # _apply_generation_group_progress). This still gives a rollback
            # point for the whole job without repeatedly copying the full
            # storage.json file while holding the global storage lock.
            with _lock:
                try:
                    _backup_current_store()
                except Exception:
                    pass
        try:
            if progress_file:
                # v1575: schedule calculations run in a warm Node worker. The worker
                # receives a full canonical snapshot only when its storage fingerprint
                # changes; module startup and normalization are reused between requests.
                data_snapshot = run_payload.get("data") if isinstance(run_payload.get("data"), dict) else {}
                cache_key = str(run_payload.pop("_cacheKey", "") or "")
                if not cache_key:
                    cache_key = hashlib.blake2b(
                        json.dumps(data_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                        digest_size=16,
                    ).hexdigest()
                worker_payload = {k: v for k, v in run_payload.items() if k not in {"data", "prior"}}
                last_progress_seq = 0
                progress_offset = 0
                progress_remainder = ""

                def consume_progress_stream():
                    nonlocal last_progress_seq, progress_offset, progress_remainder
                    if not os.path.exists(progress_file):
                        return
                    try:
                        with open(progress_file, "r", encoding="utf-8") as f:
                            f.seek(progress_offset)
                            chunk = f.read()
                            progress_offset = f.tell()
                    except OSError:
                        return
                    if not chunk:
                        return
                    text = progress_remainder + chunk
                    lines = text.split("\n")
                    progress_remainder = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            progress = json.loads(line)
                            seq = int(progress.get("seq") or 0) if isinstance(progress, dict) else 0
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
                        if seq <= last_progress_seq:
                            continue
                        if not _apply_generation_group_progress(storage_key, progress):
                            continue
                        last_progress_seq = seq
                        with _generation_jobs_lock:
                            current = _generation_jobs.get(job_id)
                            if current is not None:
                                current["progress_seq"] = seq
                                current["progress_saved_at"] = time.time()
                                current["progress"] = progress
                                history = list(current.get("progress_history") or [])
                                history.append(progress)
                                current["progress_history"] = history[-32:]

                result_holder = {}
                result_event = threading.Event()

                def run_worker_request():
                    try:
                        result_holder["parsed"] = _scheduler_generate_worker.request(
                            worker_payload, cache_key, data_snapshot, GENERATION_TIMEOUT_SECONDS
                        )
                    except Exception as exc:
                        result_holder["error"] = exc
                    finally:
                        result_event.set()

                request_thread = threading.Thread(target=run_worker_request, daemon=True)
                request_thread.start()
                deadline = started + GENERATION_TIMEOUT_SECONDS
                canceled = False
                while not result_event.is_set():
                    consume_progress_stream()
                    with _generation_jobs_lock:
                        current_job = _generation_jobs.get(job_id)
                        cancel_requested = bool(current_job and current_job.get("cancel_requested"))
                    if cancel_requested:
                        canceled = True
                        _scheduler_generate_worker.stop()
                        break
                    if time.time() >= deadline:
                        _scheduler_generate_worker.stop()
                        raise subprocess.TimeoutExpired(["node", script_path, "--worker"], GENERATION_TIMEOUT_SECONDS)
                    result_event.wait(0.10)
                consume_progress_stream()
                if canceled:
                    with _generation_jobs_lock:
                        job = _generation_jobs.get(job_id)
                        if job is not None:
                            job.update({"status": "canceled", "phase": "canceled", "finished_at": time.time(),
                                        "elapsed_ms": int(round((time.time()-started)*1000)),
                                        "message": "Расчёт остановлен пользователем. Уже готовые пакеты сохранены."})
                    return
                if "error" in result_holder:
                    raise result_holder["error"]
                parsed = result_holder.get("parsed") or {}
                final_patch = parsed.get("finalPatch")
                if isinstance(final_patch, dict):
                    _apply_generation_group_progress(storage_key, final_patch)
            else:
                proc = subprocess.run(
                    ["node", script_path],
                    input=json.dumps(run_payload, ensure_ascii=False),
                    text=True, capture_output=True, timeout=GENERATION_TIMEOUT_SECONDS, check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError((proc.stderr or proc.stdout or "Scheduler process failed").strip()[-4000:])
                parsed = json.loads(proc.stdout or "{}")

            if not parsed.get("ok") or "result" not in parsed:
                raise RuntimeError("Scheduler returned an invalid response")
            with _generation_jobs_lock:
                job = _generation_jobs.get(job_id)
                if job is not None:
                    job.pop("process", None)
                    job.update({
                        "status": "done", "phase": "done", "finished_at": time.time(),
                        "elapsed_ms": int(parsed.get("elapsedMs") or round((time.time()-started)*1000)),
                        "result": parsed["result"], "finalPatch": parsed.get("finalPatch"),
                    })
        except subprocess.TimeoutExpired:
            with _generation_jobs_lock:
                job = _generation_jobs.get(job_id)
                if job is not None:
                    job.pop("process", None)
                    job.update({"status": "error", "phase": "error", "finished_at": time.time(), "error": f"Серверный расчёт превысил лимит {GENERATION_TIMEOUT_SECONDS} сек. Уже готовые пакеты сохранены."})
        except Exception as exc:
            with _generation_jobs_lock:
                job = _generation_jobs.get(job_id)
                if job is not None:
                    job.pop("process", None)
                    if job.get("cancel_requested"):
                        job.update({"status": "canceled", "phase": "canceled", "finished_at": time.time(), "message": "Расчёт остановлен пользователем. Уже готовые пакеты сохранены."})
                    else:
                        job.update({"status": "error", "phase": "error", "finished_at": time.time(), "error": str(exc)})


@app.post("/api/schedule/generate")
def schedule_generate_start():
    """Start generation from the already persisted project state.

    Older builds posted the complete project JSON, the schedule again as ``prior``,
    and a second JSON copy as ``sourceToken``.  On a real semester that request
    could easily exceed the reverse-proxy body limit and fail with HTTP 413 before
    Flask was reached.  The browser already persists the working project, so the
    generator now reads that canonical snapshot from storage and the start request
    stays tiny regardless of project size.
    """
    _cleanup_generation_jobs()
    body = request.get_json(silent=True) or {}
    storage_key = str(body.get("storageKey") or "schedule-data-v2")
    group_id = str(body.get("groupId") or "").strip()
    fast_mode = bool(body.get("fastMode"))
    with _lock:
        store = load_store()
        raw = store.get(storage_key)
    if raw is None:
        return jsonify({"error": "Сохранённые данные проекта не найдены"}), 404
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return jsonify({"error": f"Не удалось прочитать сохранённые данные: {exc}"}), 422
    if not isinstance(data, dict):
        return jsonify({"error": "Сохранённые данные проекта имеют неверный формат"}), 422

    job_id = uuid.uuid4().hex
    with _generation_jobs_lock:
        queued = sum(1 for j in _generation_jobs.values() if j.get("status") in {"queued", "running"})
        _generation_jobs[job_id] = {
            "id": job_id, "status": "queued", "phase": "queued", "created_at": time.time(),
            "queue_position": queued + 1, "storage_key": storage_key, "group_id": group_id,
        }
    raw_for_hash = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    storage_fingerprint = hashlib.blake2b(raw_for_hash.encode("utf-8"), digest_size=16).hexdigest()
    payload = {"data": data, "prior": data.get("schedule"), "groupId": group_id, "storageKey": storage_key, "fastMode": fast_mode, "_cacheKey": storage_fingerprint}
    thread = threading.Thread(target=_run_generation_job, args=(job_id, payload), daemon=True)
    thread.start()
    return jsonify({"jobId": job_id, "status": "queued", "queuePosition": queued + 1})


@app.post("/api/schedule/generate/<job_id>/cancel")
def schedule_generate_cancel(job_id):
    """Request cooperative cancellation of an active/queued schedule job.

    Progress already committed by the batch stream is intentionally preserved.
    """
    _cleanup_generation_jobs()
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        if job.get("kind") == "graph":
            return jsonify({"error": "wrong job type"}), 400
        status = str(job.get("status") or "")
        if status in {"done", "error", "canceled"}:
            return jsonify({"ok": True, "status": status})
        job["cancel_requested"] = True
        job["phase"] = "canceling"
        if status == "queued":
            job.update({"status": "canceled", "phase": "canceled", "finished_at": time.time(),
                        "message": "Расчёт отменён до запуска."})
            return jsonify({"ok": True, "status": "canceled"})
    return jsonify({"ok": True, "status": "canceling"})


@app.post("/api/schedule/options")
def schedule_placement_options():
    """Calculate manual placement variants entirely on the server.

    The browser sends only ids/weeks; canonical project and schedule are read from
    persisted storage so heavy conflict scans never run in the UI thread.
    """
    body = request.get_json(silent=True) or {}
    storage_key = str(body.get("storageKey") or "schedule-data-v2")
    with _lock:
        store = load_store()
        raw = store.get(storage_key)
    if raw is None:
        return jsonify({"error": "Сохранённые данные проекта не найдены"}), 404
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return jsonify({"error": f"Не удалось прочитать сохранённые данные: {exc}"}), 422
    raw_for_hash = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    storage_fingerprint = hashlib.blake2b(raw_for_hash.encode("utf-8"), digest_size=12).hexdigest()
    request_fingerprint = json.dumps({
        "instIds": body.get("instIds") or [], "weeks": body.get("weeks") or [],
        "targetDay": body.get("targetDay"), "targetPeriod": body.get("targetPeriod"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cache_key = f"{storage_key}|{storage_fingerprint}|{request_fingerprint}"
    cached = _schedule_options_cache_get(cache_key)
    if cached is not None:
        return jsonify({"result": cached, "elapsed_ms": 0, "cached": True})
    payload = {
        "mode": "options",
        "data": data,
        "prior": data.get("schedule") if isinstance(data, dict) else None,
        "instIds": body.get("instIds") or [],
        "weeks": body.get("weeks") or [],
        "targetDay": body.get("targetDay"),
        "targetPeriod": body.get("targetPeriod"),
    }
    started = time.time()
    try:
        worker_payload = {k: v for k, v in payload.items() if k not in {"data", "prior"}}
        parsed = _scheduler_options_worker.request(worker_payload, storage_fingerprint, data, 60)
        if not parsed.get("ok"):
            raise RuntimeError("Сервер вариантов вернул неверный ответ")
        result = parsed.get("result") or {}
        _schedule_options_cache_put(cache_key, result)
        return jsonify({"result": result, "elapsed_ms": parsed.get("elapsedMs") or int((time.time()-started)*1000), "cached": False, "worker": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/schedule/generate/<job_id>")
def schedule_generate_status(job_id):
    _cleanup_generation_jobs()
    try:
        after_progress = max(0, int(request.args.get("after_progress") or 0))
    except (TypeError, ValueError):
        after_progress = 0
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        # v1572: progress exposed to the browser comes only from the exact
        # snapshot that _apply_generation_group_progress has already committed
        # to canonical storage. Never reread the Node progress file here: it can
        # legitimately be one or more sequences ahead of the persisted state.
        committed_progress = job.get("progress") if isinstance(job.get("progress"), dict) else None
        committed_seq = int(job.get("progress_seq") or 0)
        progress_history = [p for p in (job.get("progress_history") or []) if isinstance(p, dict)]
        public = {k: v for k, v in job.items() if k not in {
            "created_at", "started_at", "finished_at", "progress_file", "progress", "progress_history", "process"
        }}
        public["progressSeq"] = committed_seq
        fresh_progress = [p for p in progress_history if int(p.get("seq") or 0) > after_progress]
        if fresh_progress:
            public["progresses"] = fresh_progress
            public["progress"] = fresh_progress[-1]
        elif committed_progress is not None and committed_seq > after_progress:
            public["progress"] = committed_progress
        if job.get("started_at"):
            public["startedAt"] = job["started_at"]
        if job.get("finished_at"):
            public["finishedAt"] = job["finished_at"]
        if job.get("status") in {"done", "error", "canceled"}:
            job["consumed_at"] = time.time()
    return jsonify(public)


@app.post("/api/graphs/auto-group")
def graph_auto_group_start():
    """Run whole-group graph distribution on the server from persisted project data."""
    _cleanup_generation_jobs()
    body = request.get_json(silent=True) or {}
    storage_key = str(body.get("storageKey") or "schedule-data-v2")
    group_id = str(body.get("groupId") or "").strip()
    if not group_id:
        return jsonify({"error": "Не выбрана группа"}), 400
    with _lock:
        store = load_store()
        raw = store.get(storage_key)
    if raw is None:
        return jsonify({"error": "Сохранённые данные проекта не найдены"}), 404
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return jsonify({"error": f"Не удалось прочитать сохранённые данные: {exc}"}), 422
    if not isinstance(data, dict):
        return jsonify({"error": "Сохранённые данные проекта имеют неверный формат"}), 422

    payload = {
        "data": data,
        "groupId": group_id,
        "overloadMode": str(body.get("overloadMode") or "strict"),
        "overloadTolerance": max(0, float(body.get("overloadTolerance") or 0)),
    }
    job_id = uuid.uuid4().hex
    with _generation_jobs_lock:
        queued = sum(1 for j in _generation_jobs.values() if j.get("status") in {"queued", "running"})
        _generation_jobs[job_id] = {
            "id": job_id, "kind": "graph", "status": "queued", "phase": "queued", "created_at": time.time(),
            "queue_position": queued + 1, "storage_key": storage_key, "group_id": group_id,
        }
    thread = threading.Thread(target=_run_generation_job, args=(job_id, payload, GRAPH_SCHEDULER_SCRIPT), daemon=True)
    thread.start()
    return jsonify({"jobId": job_id, "status": "queued", "queuePosition": queued + 1})


@app.get("/api/graphs/auto-group/<job_id>")
def graph_auto_group_status(job_id):
    _cleanup_generation_jobs()
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        if not job or job.get("kind") != "graph":
            return jsonify({"error": "job not found"}), 404
        public = {k: v for k, v in job.items() if k not in {"created_at", "started_at", "finished_at"}}
        if job.get("started_at"):
            public["startedAt"] = job["started_at"]
        if job.get("finished_at"):
            public["finishedAt"] = job["finished_at"]
        if job.get("status") in {"done", "error", "canceled"}:
            job["consumed_at"] = time.time()
        return jsonify(public)

# v1478: atomic field-level merge for simultaneous work in «Графики».
# Unlike the legacy optimistic full-snapshot save, this endpoint performs the
# three-way merge under the server storage lock, so two employees saving at
# nearly the same time cannot overwrite one another merely because their
# debounce timers fired in a different order.
_MISSING = object()

def _json_same(a, b):
    return a == b

def _list_identity_key(values):
    if not isinstance(values, list):
        return None
    rows = [x for x in values if isinstance(x, dict)]
    if len(rows) != len(values) or not rows:
        return None
    for key in ("id", "instId", "loadId"):
        ids = [str(x.get(key) or "") for x in rows]
        if all(ids) and len(set(ids)) == len(ids):
            return key
    return None


def _graph_state_from_loads(loads):
    """Canonical recovery mirror for graph cells; loads remain the source of truth."""
    out = {}
    for load in loads if isinstance(loads, list) else []:
        if not isinstance(load, dict) or not load.get("id"):
            continue
        entry = {
            "weeklyPairs": dict(load.get("weeklyPairs") or {}),
            "weekPattern": "perWeek",
            "graphMode": load.get("graphMode") or "manual",
            "graphLocked": bool(load.get("graphLocked")),
        }
        out[str(load.get("id"))] = entry
        sig = "|".join([
            str(load.get("groupId") or ""),
            str(int(load.get("subgroup") or 0)),
            str(load.get("subjectId") or ""),
            str(load.get("teacherId") or ""),
            str(load.get("typeId") or ""),
            str(load.get("format") or "inperson"),
        ])
        if sig:
            out["sig:" + sig] = entry
    return out

def _merge_three_way(base, local, remote):
    # Local did not touch this value -> keep the freshest server value.
    if _json_same(local, base):
        return remote
    # Server did not touch it -> take the local edit.
    if _json_same(remote, base):
        return local
    if _json_same(local, remote):
        return local

    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        out = dict(remote)
        keys = set(base) | set(local) | set(remote)
        for key in keys:
            b = base.get(key, _MISSING)
            l = local.get(key, _MISSING)
            r = remote.get(key, _MISSING)
            if l is _MISSING:
                # Explicit local deletion wins only if this key existed in base.
                if b is not _MISSING:
                    out.pop(key, None)
                continue
            if b is _MISSING:
                # Locally added field/row. Preserve a concurrent remote addition
                # when identical, otherwise the local user's explicit edit wins.
                out[key] = l
                continue
            if r is _MISSING:
                out[key] = l
                continue
            out[key] = _merge_three_way(b, l, r)
        return out

    if isinstance(base, list) and isinstance(local, list) and isinstance(remote, list):
        key = _list_identity_key(base) or _list_identity_key(local) or _list_identity_key(remote)
        if key:
            bm = {str(x.get(key)): x for x in base if isinstance(x, dict) and x.get(key) is not None}
            lm = {str(x.get(key)): x for x in local if isinstance(x, dict) and x.get(key) is not None}
            rm = {str(x.get(key)): x for x in remote if isinstance(x, dict) and x.get(key) is not None}
            order = []
            for seq in (remote, local, base):
                for x in seq:
                    if isinstance(x, dict) and x.get(key) is not None:
                        ident = str(x.get(key))
                        if ident not in order:
                            order.append(ident)
            out = []
            for ident in order:
                b, l, r = bm.get(ident, _MISSING), lm.get(ident, _MISSING), rm.get(ident, _MISSING)
                if l is _MISSING:
                    if b is _MISSING and r is not _MISSING:
                        out.append(r)
                    elif b is not _MISSING and r is not _MISSING and not _json_same(r, b):
                        # Another user edited a row that this client deleted; do not
                        # silently destroy their fresh edit. Keep it.
                        out.append(r)
                    continue
                if b is _MISSING:
                    if r is _MISSING:
                        out.append(l)
                    else:
                        out.append(_merge_three_way({}, l, r) if isinstance(l, dict) and isinstance(r, dict) else l)
                    continue
                if r is _MISSING:
                    out.append(l)
                    continue
                out.append(_merge_three_way(b, l, r))
            return out

    # Same exact scalar/opaque array edited by both: last explicit local action
    # wins. Different rows/weeks are already merged recursively above.
    return local


@app.post("/api/storage-section-merge/<key>")
def storage_section_merge(key):
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных страницы"}), 400
    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(remote, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        merged = dict(remote)
        # Клиент передаёт только разделы текущей страницы. Каждый раздел
        # объединяется отдельно под единым lock, включая списки строк по id.
        for section, local_value in local.items():
            if section == "_syncMeta":
                continue
            merged[section] = _merge_three_way(base.get(section), local_value, remote.get(section))
        merged["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "section-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": str(meta.get("scope") or "page"),
        }
        encoded = json.dumps(merged, ensure_ascii=False)
        store[key] = encoded
        save_store(store)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})


@app.post("/api/storage-graph-patch/<key>")
def storage_graph_patch(key):
    """Lightweight atomic save for Graphs.

    The client sends only graph-owned fields of rows that actually changed.
    This avoids transferring/serializing the complete loads array on every cell edit.
    """
    body = request.get_json(force=True, silent=True) or {}
    changes = body.get("changes") or []
    history_append = body.get("historyAppend") or []
    meta = body.get("meta") or {}
    if not isinstance(changes, list) or not isinstance(history_append, list):
        return jsonify({"error": "Неверный формат быстрого сохранения графика"}), 400
    graph_fields = ("weeklyPairs", "weekPattern", "customWeeks", "graphMode", "graphLocked")
    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(remote, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        merged = dict(remote)
        loads = list(remote.get("loads") or [])
        index = {str(row.get("id") or ""): i for i, row in enumerate(loads) if isinstance(row, dict) and row.get("id")}
        returned_rows = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            load_id = str(change.get("id") or "")
            if not load_id or load_id not in index:
                continue
            i = index[load_id]
            remote_row = dict(loads[i] or {})
            base_graph = change.get("base") or {}
            local_graph = change.get("local") or {}
            out = dict(remote_row)
            for field in graph_fields:
                if field not in local_graph:
                    continue
                out[field] = _merge_three_way(base_graph.get(field), local_graph.get(field), remote_row.get(field))
            loads[i] = out
            returned_rows.append({"id": load_id, **{field: out.get(field) for field in graph_fields}})

        if history_append:
            existing = list(remote.get("changeHistory") or [])
            by_id = {}
            for item in existing + [x for x in history_append if isinstance(x, dict)]:
                ident = str(item.get("id") or f"{item.get('at','')}|{item.get('user','')}|{item.get('section','')}|{item.get('action','')}")
                by_id[ident] = item
            merged["changeHistory"] = list(by_id.values())[-1000:]
        merged["loads"] = loads
        merged["graphState"] = _graph_state_from_loads(loads)
        sync_meta = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "graph-patch-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "graphs",
        }
        merged["_syncMeta"] = sync_meta
        store[key] = json.dumps(merged, ensure_ascii=False)
        save_store(store)
    return jsonify({"ok": True, "token": sync_meta["saveId"], "rows": returned_rows, "_syncMeta": sync_meta})


@app.post("/api/storage-graph-merge/<key>")
def storage_graph_merge(key):
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных графика"}), 400
    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(remote, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        merged = dict(remote)
        # v1522: `loads[].weeklyPairs` is the ONLY merge source for graph cells.
        # `graphState` used to be merged independently and could lag one request
        # behind, then normalizeStoredData restored the stale mirror and visually
        # erased freshly entered numbers. Merge loads atomically and rebuild the
        # mirror from the merged result in the same server lock.
        for section in ("loads", "changeHistory"):
            if section in local:
                merged[section] = _merge_three_way(base.get(section), local.get(section), remote.get(section))
        merged["graphState"] = _graph_state_from_loads(merged.get("loads") or [])
        merged["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "graph-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "graphs",
        }
        encoded = json.dumps(merged, ensure_ascii=False)
        store[key] = encoded
        save_store(store)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})


# v1565: immediate atomic timetable-cell patch.  This endpoint intentionally
# updates only explicitly named instance/assignment/locked/unplaced entries under the
# storage lock.  It never replaces the whole schedule object.
@app.post("/api/storage-schedule-patch/<key>")
def storage_schedule_patch(key):
    body = request.get_json(force=True, silent=True) or {}
    patch = body.get("patch") or {}
    meta = body.get("meta") or {}
    if not isinstance(patch, dict):
        return jsonify({"error": "Неверный формат быстрого сохранения ячейки"}), 400

    assignment_set = patch.get("assignmentSet") or {}
    assignment_remove = patch.get("assignmentRemove") or []
    instances_set = patch.get("instancesSet") or {}
    instances_remove = patch.get("instancesRemove") or []
    locked_add = patch.get("lockedAdd") or []
    locked_remove = patch.get("lockedRemove") or []
    unplaced_add = patch.get("unplacedAdd") or []
    unplaced_remove = patch.get("unplacedRemove") or []
    if (not isinstance(assignment_set, dict) or not isinstance(instances_set, dict)
            or not all(isinstance(x, list) for x in (assignment_remove, instances_remove, locked_add, locked_remove, unplaced_add, unplaced_remove))):
        return jsonify({"error": "Неверный формат изменений ячейки"}), 400

    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(remote, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        schedule = dict(remote.get("schedule") or {})

        # v1566: grouped recurring drag&drop can change the timetable-only
        # instance structure. Persist that small structural delta in the same
        # storage lock as assignment so a later reload cannot reconstruct an old
        # instance set and erase a successfully saved placement.
        instances = [dict(x) for x in (schedule.get("instances") or []) if isinstance(x, dict)]
        remove_instance_ids = {str(x) for x in instances_remove}
        instances_by_id = {str(x.get("instId") or ""): x for x in instances if str(x.get("instId") or "") and str(x.get("instId") or "") not in remove_instance_ids}
        instance_order = [str(x.get("instId") or "") for x in instances if str(x.get("instId") or "") and str(x.get("instId") or "") not in remove_instance_ids]
        for inst_id, value in instances_set.items():
            sid = str(inst_id)
            if not sid or not isinstance(value, dict):
                continue
            if sid not in instances_by_id:
                instance_order.append(sid)
            instances_by_id[sid] = dict(value)
        schedule["instances"] = [instances_by_id[sid] for sid in instance_order if sid in instances_by_id]

        assignment = dict(schedule.get("assignment") or {})
        for inst_id, value in assignment_set.items():
            if inst_id and isinstance(value, dict):
                assignment[str(inst_id)] = value
        for inst_id in assignment_remove:
            assignment.pop(str(inst_id), None)

        def _apply_id_delta(existing, adds, removes):
            remove_set = {str(x) for x in removes}
            out = [str(x) for x in (existing or []) if str(x) not in remove_set]
            seen = set(out)
            for x in adds:
                sx = str(x)
                if sx and sx not in seen:
                    out.append(sx)
                    seen.add(sx)
            return out

        schedule["assignment"] = assignment
        schedule["locked"] = _apply_id_delta(schedule.get("locked") or [], locked_add, locked_remove)
        schedule["unplaced"] = _apply_id_delta(schedule.get("unplaced") or [], unplaced_add, unplaced_remove)
        if isinstance(patch.get("stats"), dict):
            schedule["stats"] = dict(patch.get("stats") or {})
        remote["schedule"] = schedule
        sync_meta = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-patch-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "schedule-cell",
        }
        remote["_syncMeta"] = sync_meta
        store[key] = json.dumps(remote, ensure_ascii=False)
        save_store(store)
    return jsonify({"ok": True, "token": sync_meta["saveId"], "schedule": schedule, "_syncMeta": sync_meta})


# v1489: atomic field/group/slot merge for simultaneous work in «Расписание».
# It uses the same recursive three-way merge as graphs, but owns timetable sections only.
# Different groups/instances/assignment keys survive regardless of save timing.
@app.post("/api/storage-schedule-merge/<key>")
def storage_schedule_merge(key):
    if not _schedule_client_build_ok():
        return _stale_schedule_client_response()
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных расписания"}), 400
    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            remote = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(remote, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        merged = dict(remote)
        for section in (
            "schedule", "loads", "groupDayBlocks", "groupSlotBlocks",
            "groupDayFreezes", "changeHistory"
        ):
            if section in local:
                merged[section] = _merge_three_way(base.get(section), local.get(section), remote.get(section))
        merged["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "schedule",
        }
        encoded = json.dumps(merged, ensure_ascii=False)
        store[key] = encoded
        save_store(store)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})


@app.get("/api/storage-backups")
def storage_backups():
    if not os.path.isdir(BACKUP_DIR):
        return jsonify({"backups": []})
    items = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not (name.startswith("storage_") and name.endswith(".json")):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            valid = False
            try:
                with open(path, "r", encoding="utf-8") as f:
                    backup_store = json.load(f)
                valid = _is_valid_working_value(backup_store.get("schedule-data-v2"))
            except (OSError, json.JSONDecodeError, TypeError):
                valid = False
            items.append({"name": name, "size": os.path.getsize(path), "modified": os.path.getmtime(path), "valid": valid})
        except OSError:
            pass
    return jsonify({"backups": items[:MAX_BACKUPS]})


@app.post("/api/storage-backups/<name>/restore")
def storage_backup_restore(name):
    if "/" in name or "\\" in name or not name.startswith("storage_") or not name.endswith(".json"):
        return jsonify({"error": "invalid backup name"}), 400
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            candidate = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"backup is invalid: {exc}"}), 422
    if not _is_valid_working_value(candidate.get("schedule-data-v2")):
        return jsonify({"error": "backup does not contain valid working data"}), 422
    with _lock:
        _backup_current_store()
        shutil.copy2(path, DATA_FILE)
    return jsonify({"ok": True, "restored": name})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


# Отдаём собранный фронтенд (папка static/, см. build.sh) как статику,
# с фолбэком на index.html для клиентского роутинга.
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    full = os.path.join(STATIC_DIR, path)
    if path and os.path.exists(full) and os.path.isfile(full):
        return send_from_directory(STATIC_DIR, path)
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
