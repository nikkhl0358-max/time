import os
import json
import threading
import copy
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
from flask import Flask, request, jsonify, send_from_directory, Response

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

# v1609: periodic safety-net backup for the frequent atomic per-edit save
# endpoints (section/graph/schedule merge and patch), which now pass
# backup=False to save_store to avoid paying a full file-copy on every single
# manual edit (see save_store's docstring comment). Without this, a long
# editing session with no other save path touched would never get a single
# backup. Called under `_lock` (all callers already hold it), so no separate
# lock is needed for the timestamp.
_last_periodic_backup_at = 0.0
_PERIODIC_BACKUP_INTERVAL_SECONDS = 900  # v1686: 15 minutes; hot cell saves never copy backups under the storage lock
_periodic_backup_thread_running = False
_periodic_backup_thread_lock = threading.Lock()

def _maybe_periodic_backup_async():
    """Schedule a consistent backup without blocking timetable cell writes.

    storage.json is replaced atomically, so copying the currently visible inode
    outside `_lock` is safe: the copy is either the previous or the next complete
    file, never a half-written JSON.
    """
    global _last_periodic_backup_at, _periodic_backup_thread_running
    now = time.time()
    if now - _last_periodic_backup_at < _PERIODIC_BACKUP_INTERVAL_SECONDS:
        return
    with _periodic_backup_thread_lock:
        if _periodic_backup_thread_running or now - _last_periodic_backup_at < _PERIODIC_BACKUP_INTERVAL_SECONDS:
            return
        _periodic_backup_thread_running = True
        _last_periodic_backup_at = now
    def run():
        global _periodic_backup_thread_running
        try:
            _backup_current_store()
        except Exception:
            pass
        finally:
            with _periodic_backup_thread_lock:
                _periodic_backup_thread_running = False
    threading.Thread(target=run, name="storage-backup", daemon=True).start()

def _maybe_periodic_backup():
    global _last_periodic_backup_at
    now = time.time()
    if now - _last_periodic_backup_at >= _PERIODIC_BACKUP_INTERVAL_SECONDS:
        _last_periodic_backup_at = now
        try:
            _backup_current_store()
        except Exception:
            pass

# v1670: in-process cache for the parsed storage.json. Every single request
# that touches storage (which, with `--workers 1 --threads 4`, is nearly all
# of them — this app runs as one process) called `load_store()`, which reads
# and fully JSON-parses the whole file from disk from scratch every time, even
# when nothing had changed since the previous request a moment earlier. For a
# large multi-group project this is a real, cumulative, repeated cost spread
# across essentially every admin action — consistent with "всё прогружается
# плохо". `save_store()` already builds the up-to-date dict in memory right
# before writing it, so it updates this cache directly instead of load_store()
# needing to re-read the file it just wrote. `load_store()` only re-reads from
# disk when the file's mtime no longer matches what's cached (e.g. after an
# external restore, or on first request after a process restart). All access
# happens under the shared `_lock`, so no separate cache lock is needed.
_store_cache = {"mtime": None, "store": None}

# v1681: high-concurrency fast path for manual timetable edits.
# Multiple users can click within the same few milliseconds. Previously every
# click serialized and rewrote the complete multi-megabyte storage.json. Batch
# cell patches for a very small window and commit all independent deltas in one
# atomic file replacement. Each request still receives its own success/error;
# only the physical disk write is coalesced.
_schedule_patch_queue_lock = threading.Lock()
_schedule_patch_queue_event = threading.Event()
_schedule_patch_queue = []
_schedule_patch_worker_started = False
SCHEDULE_PATCH_BATCH_WINDOW_SECONDS = float(os.environ.get("SCHEDULE_PATCH_BATCH_WINDOW_SECONDS", "0.035"))
SCHEDULE_PATCH_BATCH_MAX = int(os.environ.get("SCHEDULE_PATCH_BATCH_MAX", "24"))

def _invalidate_store_cache():
    _store_cache["mtime"] = None
    _store_cache["store"] = None

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
            ["node", "--max-old-space-size=" + str(int(os.environ.get("NODE_WORKER_MAX_OLD_SPACE_MB", "192"))), self.script_path, "--worker"],
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
                    raise subprocess.TimeoutExpired(["node", "--max-old-space-size=" + str(int(os.environ.get("NODE_WORKER_MAX_OLD_SPACE_MB", "192"))), self.script_path, "--worker"], timeout)
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

_scheduler_shared_worker = _PersistentSchedulerWorker(SCHEDULER_SCRIPT, "scheduler-shared")
_scheduler_generate_worker = _scheduler_shared_worker
_scheduler_options_worker = _scheduler_shared_worker

# v1564: маленький LRU-кэш серверных вариантов ручной постановки.
# Ключ содержит отпечаток актуального storage, поэтому любое реальное изменение
# расписания/графика/ограничений автоматически делает старый результат неприменимым.
_schedule_options_cache = OrderedDict()
_schedule_options_cache_lock = threading.Lock()
SCHEDULE_OPTIONS_CACHE_MAX = int(os.environ.get("SCHEDULE_OPTIONS_CACHE_MAX", "24"))
SCHEDULE_OPTIONS_CACHE_MAX_BYTES = int(os.environ.get("SCHEDULE_OPTIONS_CACHE_MAX_BYTES", str(8 * 1024 * 1024)))
_schedule_options_cache_bytes = 0

# v1637: публичный контур обслуживается из отдельного компактного sidecar-файла.
# Раньше cache-key зависел от mtime общего storage.json, поэтому ЛЮБОЕ автосохранение
# редактора инвалидировало публичный кэш и следующий посетитель снова читал и парсил
# многомегабайтный рабочий проект. Теперь опубликованный снимок сериализуется отдельно
# и меняется только при реальном изменении публикации/публичных объявлений.
_public_bootstrap_cache_lock = threading.Lock()
_public_bootstrap_cache = {"key": None, "plain": None, "gzip": None, "etag": None}
PUBLIC_BOOTSTRAP_FILE = os.path.join(DATA_DIR, "public-bootstrap.json")
PUBLIC_BOOTSTRAP_GZIP_FILE = os.path.join(DATA_DIR, "public-bootstrap.json.gz")
PUBLIC_BOOTSTRAP_META_FILE = os.path.join(DATA_DIR, "public-bootstrap.meta.json")

# v1643: the working storage.json remains the only source of truth. The public
# site gets a tiny common index plus lazy per-group/per-teacher schedule shards.
# This avoids sending/parsing the entire semester schedule just to open the
# public landing page or one group's timetable.
PUBLIC_INDEX_FILE = os.path.join(DATA_DIR, "public-index.json")
PUBLIC_INDEX_GZIP_FILE = os.path.join(DATA_DIR, "public-index.json.gz")
PUBLIC_INDEX_META_FILE = os.path.join(DATA_DIR, "public-index.meta.json")
PUBLIC_STATUS_FILE = os.path.join(DATA_DIR, "public-status.json")
PUBLIC_SHARD_DIR = os.path.join(DATA_DIR, "public-shards")
PUBLIC_SHARD_SNAPSHOT_FILE = os.path.join(DATA_DIR, "public-shards.current.json")
_public_index_cache_lock = threading.Lock()
_public_index_cache = {"key": None, "plain": None, "gzip": None, "etag": None, "project": None}
_public_shard_cache_lock = threading.Lock()
_public_shard_cache = OrderedDict()
_public_status_cache = None
PUBLIC_SHARD_CACHE_MAX = int(os.environ.get("PUBLIC_SHARD_CACHE_MAX", "16"))


def _public_project_from_store(store):
    raw = (store or {}).get("schedule-data-v2") if isinstance(store, dict) else None
    try:
        project = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        project = None
    return project if isinstance(project, dict) else None

def _public_source_project(project):
    """Stable heavy source for the public contour.

    Ordinary editor saves must not invalidate every public shard. The last
    published snapshot is therefore used for schedule/dictionaries; tiny
    public metadata is overlaid from the working project.
    """
    published = project.get("publishedSnapshot")
    source = dict(published) if isinstance(published, dict) else dict(project)
    for key in (
        "publishedWeeks", "publicWeekSelectionEnabled", "publishedAt", "publishedBy",
        "publicIntroText", "announcements", "consultations",
    ):
        if key in project:
            source[key] = project.get(key)
    return source


def _public_marker(project):
    # v1682: only PUBLIC changes invalidate public caches. Cell edits stay out
    # of the marker and are exposed only via the tiny changed/published status.
    published = project.get("publishedSnapshot")
    published_meta = published.get("_syncMeta", {}) if isinstance(published, dict) else {}
    token = str(project.get("publishedAt") or published_meta.get("saveId") or published_meta.get("at") or "unpublished")
    public_aux = {
        "publicIntroText": project.get("publicIntroText", ""),
        "announcements": project.get("announcements", []) or [],
        "consultations": project.get("consultations", []) or [],
        "publishedWeeks": project.get("publishedWeeks", []) or [],
        "publicWeekSelectionEnabled": project.get("publicWeekSelectionEnabled") is True,
    }
    aux_hash = hashlib.sha1(json.dumps(public_aux, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"published:{token}:{aux_hash}"


def _filter_schedule_for_public_weeks(project, schedule):
    if not isinstance(schedule, dict) or project.get("publicWeekSelectionEnabled") is not True:
        return schedule
    instances = schedule.get("instances") if isinstance(schedule.get("instances"), list) else []
    chosen = []
    for inst in instances:
        trimmed = _trim_instance_to_public_weeks(project, inst)
        if trimmed is not None:
            chosen.append(trimmed)
    ids = {str(inst.get("instId")) for inst in chosen if isinstance(inst, dict) and inst.get("instId")}
    assignment = schedule.get("assignment") if isinstance(schedule.get("assignment"), dict) else {}
    locked_raw = schedule.get("locked") or []
    out = {k: v for k, v in schedule.items() if k not in {"instances", "assignment", "locked", "unplaced"}}
    out["instances"] = chosen
    out["assignment"] = {iid: assignment[iid] for iid in ids if iid in assignment}
    if isinstance(locked_raw, dict):
        out["locked"] = {iid: locked_raw[iid] for iid in ids if iid in locked_raw}
    else:
        out["locked"] = [iid for iid in locked_raw if str(iid) in ids]
    out["unplaced"] = []
    return out




def _public_publication_status(project):
    """Cheap public publication state derived from canonical save metadata.

    red: nothing has ever been published;
    green: the latest canonical save is the publication itself;
    yellow: canonical working data changed after the latest publication.
    This deliberately avoids hashing the multi-megabyte project on every public request.
    """
    published_at = str(project.get("publishedAt") or "").strip()
    if not published_at:
        return "unpublished"
    meta = project.get("_syncMeta") if isinstance(project.get("_syncMeta"), dict) else {}
    scope = str(meta.get("scope") or "")
    # Publication endpoints write _syncMeta atomically with publishedAt.
    if scope in {"publication", "publish-weeks"}:
        return "published"
    changed_at = str(meta.get("at") or "").strip()
    if changed_at and changed_at > published_at:
        return "changed"
    return "published"

def _write_public_status_sidecar(project):
    """Tiny status file; after first post-publication edit it stops writing."""
    global _public_status_cache
    if not isinstance(project, dict):
        return
    payload = {
        "publicationStatus": _public_publication_status(project),
        "publishedAt": project.get("publishedAt") or "",
        "publishedBy": project.get("publishedBy") or "",
    }
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if _public_status_cache == plain and os.path.exists(PUBLIC_STATUS_FILE):
        return
    _atomic_write_bytes(PUBLIC_STATUS_FILE, plain)
    _public_status_cache = plain


def _build_public_payload(project):
    published = project.get("publishedSnapshot")
    public_keys = [
        "config", "specialties", "groups", "teachers", "departments", "subjects",
        "lessonTypes", "rooms", "schedule", "loads", "practices", "substitutions",
        "retakes", "interimAssessments", "attestation", "consultations", "announcements",
        "publishedWeeks", "publicWeekSelectionEnabled", "calendarExceptions",
    ]
    # v1637: source of truth for public contour is the current saved project.
    # publishedSnapshot is retained for history/manual publication workflows,
    # but it must not make the public site lag behind the editor.
    source = _public_source_project(project)
    public_data = {k: source.get(k) for k in public_keys if k in source}
    if "schedule" in public_data:
        public_data["schedule"] = _filter_schedule_for_public_weeks(project, public_data.get("schedule"))
    public_data["publicIntroText"] = project.get("publicIntroText", "")
    public_data["announcements"] = project.get("announcements", public_data.get("announcements", [])) or []
    public_data["consultations"] = project.get("consultations", public_data.get("consultations", [])) or []
    return {
        "data": public_data,
        "userCount": len(project.get("users") or []),
        "publishedAt": project.get("publishedAt") or ((published or {}).get("_syncMeta", {}).get("at") if isinstance(published, dict) else None),
        "publicationStatus": _public_publication_status(project),
    }

def _build_public_index_payload(project):
    """Shared public dictionaries without the heavyweight schedule itself."""
    published = project.get("publishedSnapshot")
    # v1683: the public landing/index contains only navigation + genuinely shared
    # dictionaries. Heavy per-lesson loads and substitutions live inside the
    # selected group/teacher shard, so a visitor opening the landing page never
    # downloads them for the whole college.
    public_keys = [
        "config", "specialties", "groups", "teachers", "departments", "subjects",
        "lessonTypes", "rooms", "practices", "retakes", "interimAssessments",
        "attestation", "consultations", "announcements", "publishedWeeks",
        "publicWeekSelectionEnabled", "calendarExceptions",
    ]
    source = _public_source_project(project)
    public_data = {k: source.get(k) for k in public_keys if k in source}
    # normalizePublicStoredData on the client expects a schedule object to exist;
    # keep a deliberately empty shell until the selected shard arrives.
    public_data["schedule"] = {"instances": [], "assignment": {}, "locked": {}, "unplaced": []}
    # Empty shells are replaced by the selected entity shard. Keeping them here
    # preserves the client data shape without shipping the global arrays.
    public_data["loads"] = []
    public_data["substitutions"] = []
    public_data["publicIntroText"] = project.get("publicIntroText", "")
    public_data["announcements"] = project.get("announcements", public_data.get("announcements", [])) or []
    public_data["consultations"] = project.get("consultations", public_data.get("consultations", [])) or []
    return {
        "data": public_data,
        "userCount": len(project.get("users") or []),
        "publishedAt": project.get("publishedAt") or ((published or {}).get("_syncMeta", {}).get("at") if isinstance(published, dict) else None),
        "publicationStatus": _public_publication_status(project),
        "shardedSchedule": True,
    }


def _refresh_public_index_from_store(store, force=False):
    project = _public_project_from_store(store)
    if not project:
        return False
    marker = _public_marker(project)
    if not force and os.path.exists(PUBLIC_INDEX_META_FILE) and os.path.exists(PUBLIC_INDEX_FILE):
        try:
            with open(PUBLIC_INDEX_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("marker") == marker:
                with _public_index_cache_lock:
                    _public_index_cache["project"] = _public_source_project(project)
                    _public_index_cache["key"] = marker
                return False
        except Exception:
            pass
    payload = _build_public_index_payload(project)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(plain, compresslevel=1)
    etag = hashlib.sha1(plain).hexdigest()
    _atomic_write_bytes(PUBLIC_INDEX_FILE, plain)
    _atomic_write_bytes(PUBLIC_INDEX_GZIP_FILE, gz)
    meta_plain = json.dumps({"marker": marker, "etag": etag, "size": len(plain), "gzipSize": len(gz)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(PUBLIC_INDEX_META_FILE, meta_plain)
    with _public_index_cache_lock:
        _public_index_cache.update({"key": marker, "plain": plain, "gzip": gz, "etag": etag, "project": _public_source_project(project)})
    return True


def _instance_group_ids(inst):
    if not isinstance(inst, dict):
        return []
    explicit = inst.get("streamParticipants") if isinstance(inst.get("streamParticipants"), list) else []
    out = []
    base_gid = inst.get("groupId")
    if base_gid:
        out.append(str(base_gid))
    if explicit:
        for part in explicit:
            if isinstance(part, dict) and part.get("groupId"):
                out.append(str(part.get("groupId")))
    else:
        for gid in inst.get("streamGroupIds") or []:
            if gid:
                out.append(str(gid))
    return list(dict.fromkeys(out))


def _total_semester_weeks_py(config):
    from datetime import date, timedelta
    try:
        start = date.fromisoformat(str((config or {}).get("semesterStart") or ""))
        end = date.fromisoformat(str((config or {}).get("semesterEnd") or ""))
    except Exception:
        return 0
    monday = start - timedelta(days=start.weekday())
    return max(0, ((end - monday).days // 7) + 1)


def _instance_weeks_py(config, inst):
    total = _total_semester_weeks_py(config)
    if total <= 0 or not isinstance(inst, dict):
        return []
    pattern = str(inst.get("weekPattern") or "all")
    if pattern == "custom":
        out = []
        for value in inst.get("customWeeks") or []:
            try:
                n = int(value)
            except Exception:
                continue
            if 1 <= n <= total:
                out.append(n)
        return sorted(set(out))
    if pattern == "all" or pattern == "perWeek":
        return list(range(1, total + 1))
    # Client parity is semester-week parity; odd/even are enough for public trimming.
    if pattern == "odd":
        return [n for n in range(1, total + 1) if n % 2 == 1]
    if pattern == "even":
        return [n for n in range(1, total + 1) if n % 2 == 0]
    return list(range(1, total + 1))


def _trim_instance_to_public_weeks(project, inst):
    if not isinstance(inst, dict):
        return None
    if project.get("publicWeekSelectionEnabled") is not True:
        return inst
    allowed = {int(w) for w in (project.get("publishedWeeks") or []) if str(w).isdigit() and int(w) > 0}
    if not allowed:
        return None
    weeks = [w for w in _instance_weeks_py(project.get("config") or {}, inst) if w in allowed]
    if not weeks:
        return None
    out = dict(inst)
    out["weekPattern"] = "custom"
    out["customWeeks"] = weeks
    return out


def _build_public_schedule_shard(project, kind, entity_id):
    schedule = project.get("schedule") if isinstance(project.get("schedule"), dict) else {}
    instances = schedule.get("instances") if isinstance(schedule.get("instances"), list) else []
    entity_id = str(entity_id or "")
    if kind == "group":
        raw_chosen = [inst for inst in instances if entity_id in _instance_group_ids(inst)]
    elif kind == "teacher":
        # Original teacher's instances plus source instances needed to render
        # moved/substitute lessons assigned to this teacher.
        extra_inst_ids = {
            str(sub.get("instId"))
            for sub in (project.get("substitutions") or [])
            if isinstance(sub, dict)
            and str(sub.get("teacherId") or sub.get("newTeacherId") or "") == entity_id
            and sub.get("instId")
        }
        raw_chosen = [
            inst for inst in instances
            if str((inst or {}).get("teacherId") or "") == entity_id
            or str((inst or {}).get("instId") or "") in extra_inst_ids
        ]
    else:
        raw_chosen = []
    chosen = []
    for inst in raw_chosen:
        trimmed = _trim_instance_to_public_weeks(project, inst)
        if trimmed is not None:
            chosen.append(trimmed)
    ids = {str(inst.get("instId")) for inst in chosen if isinstance(inst, dict) and inst.get("instId")}
    assignment = schedule.get("assignment") if isinstance(schedule.get("assignment"), dict) else {}
    locked = schedule.get("locked") or []
    # Public rendering only consumes these schedule fields. Keep any harmless
    # scalar metadata, but never ship other groups' instances/assignments.
    out = {k: v for k, v in schedule.items() if k not in {"instances", "assignment", "locked", "unplaced"}}
    out["instances"] = chosen
    out["assignment"] = {iid: assignment[iid] for iid in ids if iid in assignment}
    out["locked"] = ({iid: locked[iid] for iid in ids if iid in locked} if isinstance(locked, dict) else [iid for iid in locked if str(iid) in ids])
    out["unplaced"] = []
    return out



def _public_entity_data_patch(project, chosen_instances):
    """Only heavy dictionaries referenced by one public entity."""
    chosen_instances = [x for x in (chosen_instances or []) if isinstance(x, dict)]
    inst_ids = {str(x.get("instId")) for x in chosen_instances if x.get("instId")}
    load_ids = set()
    group_ids = set()
    teacher_ids = set()
    for inst in chosen_instances:
        if inst.get("loadId"):
            load_ids.add(str(inst.get("loadId")))
        if inst.get("groupId"):
            group_ids.add(str(inst.get("groupId")))
        if inst.get("teacherId"):
            teacher_ids.add(str(inst.get("teacherId")))
        for part in (inst.get("streamParticipants") or []):
            if isinstance(part, dict):
                if part.get("loadId"):
                    load_ids.add(str(part.get("loadId")))
                if part.get("groupId"):
                    group_ids.add(str(part.get("groupId")))
                if part.get("teacherId"):
                    teacher_ids.add(str(part.get("teacherId")))
    loads = []
    for row in (project.get("loads") or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or row.get("loadId") or "")
        # Old/generated instances can miss loadId. In that case retain the small
        # set of rows belonging to one of the shard's actual groups/teachers.
        if rid in load_ids or str(row.get("groupId") or "") in group_ids or str(row.get("teacherId") or "") in teacher_ids:
            loads.append(row)
    substitutions = [
        row for row in (project.get("substitutions") or [])
        if isinstance(row, dict) and (
            str(row.get("instId") or "") in inst_ids
            or str(row.get("groupId") or "") in group_ids
            or str(row.get("teacherId") or row.get("newTeacherId") or "") in teacher_ids
        )
    ]
    return {"loads": loads, "substitutions": substitutions}


def _public_schedule_shard_for_week(project, schedule, week_number):
    """Trim an already entity-scoped schedule to exactly one semester week."""
    try:
        week_number = int(week_number)
    except Exception:
        return schedule
    if week_number <= 0:
        return schedule
    instances = schedule.get("instances") if isinstance(schedule, dict) else []
    chosen = []
    for inst in (instances or []):
        if week_number not in _instance_weeks_py(project.get("config") or {}, inst):
            continue
        out = dict(inst)
        out["weekPattern"] = "custom"
        out["customWeeks"] = [week_number]
        chosen.append(out)
    ids = {str(inst.get("instId")) for inst in chosen if inst.get("instId")}
    assignment = schedule.get("assignment") if isinstance(schedule.get("assignment"), dict) else {}
    locked = schedule.get("locked") or []
    out = {k: v for k, v in schedule.items() if k not in {"instances", "assignment", "locked", "unplaced"}}
    out["instances"] = chosen
    out["assignment"] = {iid: assignment[iid] for iid in ids if iid in assignment}
    out["locked"] = ({iid: locked[iid] for iid in ids if iid in locked} if isinstance(locked, dict) else [iid for iid in locked if str(iid) in ids])
    out["unplaced"] = []
    return out


def _public_snapshot_dir_name(marker):
    return "snapshot-" + hashlib.sha1(str(marker).encode("utf-8")).hexdigest()[:20]


def _public_entity_dir(snapshot_dir, kind, entity_id):
    return os.path.join(snapshot_dir, kind, hashlib.sha1(str(entity_id).encode("utf-8")).hexdigest())


def _write_public_shard_file(path, payload):
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(plain, compresslevel=1)
    _atomic_write_bytes(path, plain)
    _atomic_write_bytes(path + ".gz", gz)
    return hashlib.sha1(plain).hexdigest(), len(plain), len(gz)


def _clone_public_snapshot_tree(src_dir, dst_dir):
    """Clone an immutable public snapshot cheaply using hard links when possible."""
    if not src_dir or not os.path.isdir(src_dir):
        return False
    try:
        shutil.copytree(src_dir, dst_dir, copy_function=os.link)
        return True
    except Exception:
        shutil.rmtree(dst_dir, ignore_errors=True)
        try:
            shutil.copytree(src_dir, dst_dir, copy_function=shutil.copy2)
            return True
        except Exception:
            shutil.rmtree(dst_dir, ignore_errors=True)
            return False


def _publication_teacher_ids_for_groups(project, group_ids):
    group_ids = {str(x) for x in (group_ids or []) if str(x)}
    if not group_ids:
        return set()
    schedule = project.get("schedule") if isinstance(project.get("schedule"), dict) else {}
    inst_ids = set()
    teacher_ids = set()
    for inst in (schedule.get("instances") or []):
        if not isinstance(inst, dict):
            continue
        if not group_ids.intersection(_instance_group_ids(inst)):
            continue
        if inst.get("instId"):
            inst_ids.add(str(inst.get("instId")))
        if inst.get("teacherId"):
            teacher_ids.add(str(inst.get("teacherId")))
        for part in (inst.get("streamParticipants") or []):
            if isinstance(part, dict) and part.get("teacherId"):
                teacher_ids.add(str(part.get("teacherId")))
    for sub in (project.get("substitutions") or []):
        if not isinstance(sub, dict):
            continue
        if str(sub.get("groupId") or "") in group_ids or str(sub.get("instId") or "") in inst_ids:
            tid = str(sub.get("teacherId") or sub.get("newTeacherId") or "")
            if tid:
                teacher_ids.add(tid)
    return teacher_ids


def _prebuild_public_snapshot_shards(project, marker, change=None):
    """Build the public shard snapshot, reusing the previous one when possible.

    v1705: publishing one group/week no longer regenerates every group x teacher x
    semester-week JSON file. We hard-link the previous immutable snapshot and only
    replace files affected by this publication. Full publication still performs a
    clean rebuild.
    """
    source = _public_source_project(project)
    snapshot_name = _public_snapshot_dir_name(marker)
    final_dir = os.path.join(PUBLIC_SHARD_DIR, snapshot_name)
    tmp_dir = final_dir + ".building-" + uuid.uuid4().hex[:8]
    total_weeks = _total_semester_weeks_py(source.get("config") or {})
    published_weeks = set(range(1, total_weeks + 1))
    if source.get("publicWeekSelectionEnabled") is True:
        published_weeks = {int(x) for x in (source.get("publishedWeeks") or []) if str(x).isdigit() and 1 <= int(x) <= total_weeks}

    change = change if isinstance(change, dict) else {}
    scope = str(change.get("scope") or "all")
    group_ids = {str(x) for x in (change.get("groupIds") or []) if str(x)}
    week_numbers = {int(x) for x in (change.get("weekNumbers") or []) if str(x).isdigit() and 1 <= int(x) <= total_weeks}
    teacher_ids = {str(x) for x in (change.get("teacherIds") or []) if str(x)}
    if group_ids:
        teacher_ids.update(_publication_teacher_ids_for_groups(source, group_ids))

    previous_manifest = _load_public_shard_manifest()
    previous_dir = None
    previous_weeks = set()
    if isinstance(previous_manifest, dict):
        prev_name = str(previous_manifest.get("snapshot") or "")
        candidate = os.path.join(PUBLIC_SHARD_DIR, prev_name)
        if prev_name and os.path.isdir(candidate):
            previous_dir = candidate
            previous_weeks = {int(x) for x in (previous_manifest.get("publishedWeeks") or []) if str(x).isdigit()}

    incremental = scope in {"group", "week", "weeks"} and previous_dir is not None
    if incremental:
        incremental = _clone_public_snapshot_tree(previous_dir, tmp_dir)
    if not incremental:
        os.makedirs(tmp_dir, exist_ok=True)
        scope = "all"

    stats = {"group": 0, "teacher": 0, "weekFiles": 0, "reused": bool(incremental), "scope": scope}

    def write_entity(kind, row, rebuild_semester=True, weeks_to_write=None):
        if not isinstance(row, dict) or not row.get("id"):
            return
        entity_id = str(row.get("id"))
        full_schedule = _build_public_schedule_shard(source, kind, entity_id)
        full_instances = full_schedule.get("instances") or []
        entity_dir = _public_entity_dir(tmp_dir, kind, entity_id)
        os.makedirs(entity_dir, exist_ok=True)
        if rebuild_semester:
            full_payload = {
                "schedule": full_schedule,
                "dataPatch": _public_entity_data_patch(source, full_instances),
                "marker": marker, "kind": kind, "id": entity_id, "scope": "semester",
            }
            _write_public_shard_file(os.path.join(entity_dir, "semester.json"), full_payload)
            stats[kind] += 1
        for week in sorted(weeks_to_write if weeks_to_write is not None else published_weeks):
            if week not in published_weeks:
                continue
            week_schedule = _public_schedule_shard_for_week(source, full_schedule, week)
            week_instances = week_schedule.get("instances") or []
            week_payload = {
                "schedule": week_schedule,
                "dataPatch": _public_entity_data_patch(source, week_instances),
                "marker": marker, "kind": kind, "id": entity_id, "scope": "week", "week": week,
            }
            _write_public_shard_file(os.path.join(entity_dir, f"week-{week}.json"), week_payload)
            stats["weekFiles"] += 1

    try:
        if scope == "all":
            for kind, rows in (("group", source.get("groups") or []), ("teacher", source.get("teachers") or [])):
                for row in rows:
                    write_entity(kind, row, rebuild_semester=True, weeks_to_write=published_weeks)
        elif scope == "group":
            for row in (source.get("groups") or []):
                if isinstance(row, dict) and str(row.get("id") or "") in group_ids:
                    write_entity("group", row, rebuild_semester=True, weeks_to_write=published_weeks)
            for row in (source.get("teachers") or []):
                if isinstance(row, dict) and str(row.get("id") or "") in teacher_ids:
                    write_entity("teacher", row, rebuild_semester=True, weeks_to_write=published_weeks)
        elif scope == "week":
            # Semester view changes too, but only the published week file itself
            # needs replacement; other immutable week files are reused as links.
            targets = week_numbers or published_weeks
            for kind, rows in (("group", source.get("groups") or []), ("teacher", source.get("teachers") or [])):
                for row in rows:
                    write_entity(kind, row, rebuild_semester=True, weeks_to_write=targets)
        elif scope == "weeks":
            # Visibility selection can add/remove week files. Semester files must
            # reflect the new allowed week set, while unchanged week files remain.
            added = published_weeks - previous_weeks
            removed = previous_weeks - published_weeks
            for kind, rows in (("group", source.get("groups") or []), ("teacher", source.get("teachers") or [])):
                for row in rows:
                    write_entity(kind, row, rebuild_semester=True, weeks_to_write=added)
                    if isinstance(row, dict) and row.get("id"):
                        entity_dir = _public_entity_dir(tmp_dir, kind, str(row.get("id")))
                        for week in removed:
                            for suffix in ("", ".gz"):
                                try:
                                    os.remove(os.path.join(entity_dir, f"week-{week}.json" + suffix))
                                except FileNotFoundError:
                                    pass

        if os.path.exists(final_dir):
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(tmp_dir, final_dir)
        tmp_dir = None
        manifest = {
            "marker": marker,
            "snapshot": snapshot_name,
            "totalWeeks": total_weeks,
            "publishedWeeks": sorted(published_weeks),
            "stats": stats,
        }
        _atomic_write_bytes(PUBLIC_SHARD_SNAPSHOT_FILE, json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        try:
            dirs = [d for d in os.listdir(PUBLIC_SHARD_DIR) if d.startswith("snapshot-") and os.path.isdir(os.path.join(PUBLIC_SHARD_DIR, d))]
            dirs.sort(key=lambda d: os.path.getmtime(os.path.join(PUBLIC_SHARD_DIR, d)), reverse=True)
            for old in dirs[2:]:
                shutil.rmtree(os.path.join(PUBLIC_SHARD_DIR, old), ignore_errors=True)
        except Exception:
            pass
        return manifest
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

def _load_public_shard_manifest():
    try:
        with open(PUBLIC_SHARD_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _ensure_public_shard_on_demand(kind, entity_id, scope="semester", week=0):
    """Create only the requested public shard after a cold deploy/restart.

    v1706: a missing shard manifest must never make a public visitor rebuild the
    whole semester synchronously.  We read the published source once per worker
    and materialise just one group/teacher file (and only one week when asked).
    Normal publication can still replace this lazy snapshot atomically later.
    """
    project, marker = _public_project_for_shard()
    if not isinstance(project, dict) or not marker:
        return None
    if kind not in {"group", "teacher"}:
        return None

    rows = project.get("groups") if kind == "group" else project.get("teachers")
    exists = any(isinstance(row, dict) and str(row.get("id") or "") == str(entity_id) for row in (rows or []))
    if not exists:
        return None

    total_weeks = _total_semester_weeks_py(project.get("config") or {})
    published_weeks = set(range(1, total_weeks + 1))
    if project.get("publicWeekSelectionEnabled") is True:
        published_weeks = {int(x) for x in (project.get("publishedWeeks") or []) if str(x).isdigit() and 1 <= int(x) <= total_weeks}

    if scope == "week":
        week = int(week or 0)
        if week <= 0 or week not in published_weeks:
            return None
    else:
        scope = "semester"
        week = 0

    snapshot_name = _public_snapshot_dir_name(marker)
    snapshot_dir = os.path.join(PUBLIC_SHARD_DIR, snapshot_name)
    entity_dir = _public_entity_dir(snapshot_dir, kind, entity_id)
    os.makedirs(entity_dir, exist_ok=True)

    filename = f"week-{week}.json" if scope == "week" else "semester.json"
    plain_path = os.path.join(entity_dir, filename)
    if not os.path.exists(plain_path):
        full_schedule = _build_public_schedule_shard(project, kind, str(entity_id))
        if scope == "week":
            schedule = _public_schedule_shard_for_week(project, full_schedule, week)
        else:
            schedule = full_schedule
        instances = schedule.get("instances") or []
        payload = {
            "schedule": schedule,
            "dataPatch": _public_entity_data_patch(project, instances),
            "marker": marker, "kind": kind, "id": str(entity_id), "scope": scope,
        }
        if scope == "week":
            payload["week"] = week
        _write_public_shard_file(plain_path, payload)

    manifest = _load_public_shard_manifest()
    if not isinstance(manifest, dict) or str(manifest.get("marker") or "") != str(marker) or str(manifest.get("snapshot") or "") != snapshot_name:
        manifest = {
            "marker": marker,
            "snapshot": snapshot_name,
            "totalWeeks": total_weeks,
            "publishedWeeks": sorted(published_weeks),
            "stats": {"lazy": True},
        }
        _atomic_write_bytes(PUBLIC_SHARD_SNAPSHOT_FILE, json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return manifest


def _public_project_for_shard():
    with _public_index_cache_lock:
        project = _public_index_cache.get("project")
        marker = _public_index_cache.get("key")
    if isinstance(project, dict) and marker:
        return project, str(marker)
    # Cold worker: one canonical read, then retain only the stable published
    # public source in RAM. Normal editor saves don't invalidate it.
    store = load_store()
    working = _public_project_from_store(store)
    if not working:
        return None, ""
    marker = _public_marker(working)
    project = _public_source_project(working)
    with _public_index_cache_lock:
        _public_index_cache["project"] = project
        _public_index_cache["key"] = marker
    return project, marker


def _public_shard_cache_put(key, value):
    with _public_shard_cache_lock:
        _public_shard_cache[key] = value
        _public_shard_cache.move_to_end(key)
        while len(_public_shard_cache) > PUBLIC_SHARD_CACHE_MAX:
            _public_shard_cache.popitem(last=False)


def _public_shard_cache_get(key):
    with _public_shard_cache_lock:
        value = _public_shard_cache.get(key)
        if value is not None:
            _public_shard_cache.move_to_end(key)
        return value


def _atomic_write_bytes(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            try: os.remove(tmp)
            except FileNotFoundError: pass

def _refresh_public_sidecar_from_store(store, force=False):
    project = _public_project_from_store(store)
    if not project:
        return False
    marker = _public_marker(project)
    if not force and os.path.exists(PUBLIC_BOOTSTRAP_META_FILE) and os.path.exists(PUBLIC_BOOTSTRAP_FILE):
        try:
            with open(PUBLIC_BOOTSTRAP_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("marker") == marker:
                return False
        except Exception:
            pass
    payload = _build_public_payload(project)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(plain, compresslevel=1)
    etag = hashlib.sha1(plain).hexdigest()
    _atomic_write_bytes(PUBLIC_BOOTSTRAP_FILE, plain)
    _atomic_write_bytes(PUBLIC_BOOTSTRAP_GZIP_FILE, gz)
    meta_plain = json.dumps({"marker": marker, "etag": etag, "size": len(plain), "gzipSize": len(gz)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(PUBLIC_BOOTSTRAP_META_FILE, meta_plain)
    with _public_bootstrap_cache_lock:
        _public_bootstrap_cache.update({"key": marker, "plain": plain, "gzip": gz, "etag": etag})
    return True

def _schedule_options_cache_get(key):
    with _schedule_options_cache_lock:
        entry = _schedule_options_cache.get(key)
        if entry is None:
            return None
        _schedule_options_cache.move_to_end(key)
        if isinstance(entry, tuple) and len(entry) == 2:
            return entry[0]
        return entry

def _schedule_options_cache_put(key, value):
    # v1684: cache by both item count and approximate byte size. Manual-placement
    # option payloads can be large; keeping 256 of them was enough to grow one
    # gunicorn worker until the container OOM-killed it.
    global _schedule_options_cache_bytes
    try:
        approx = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        approx = 0
    with _schedule_options_cache_lock:
        previous = _schedule_options_cache.pop(key, None)
        if isinstance(previous, tuple) and len(previous) == 2:
            _schedule_options_cache_bytes = max(0, _schedule_options_cache_bytes - int(previous[1] or 0))
        _schedule_options_cache[key] = (value, approx)
        _schedule_options_cache_bytes += approx
        _schedule_options_cache.move_to_end(key)
        while (_schedule_options_cache and (len(_schedule_options_cache) > SCHEDULE_OPTIONS_CACHE_MAX or _schedule_options_cache_bytes > SCHEDULE_OPTIONS_CACHE_MAX_BYTES)):
            _, evicted = _schedule_options_cache.popitem(last=False)
            if isinstance(evicted, tuple) and len(evicted) == 2:
                _schedule_options_cache_bytes = max(0, _schedule_options_cache_bytes - int(evicted[1] or 0))



def load_store():
    if not os.path.exists(DATA_FILE):
        _invalidate_store_cache()
        return {}
    try:
        current_mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        current_mtime = None
    if current_mtime is not None and _store_cache["mtime"] == current_mtime and _store_cache["store"] is not None:
        # Shallow copy: callers do things like `store[key] = ...` on the
        # result and expect that not to silently corrupt what's cached until
        # save_store() is actually called. The individual values are large
        # JSON-encoded strings (immutable), so sharing those references
        # across copies is safe and cheap — this avoids re-parsing megabytes
        # of JSON for a copy that will very often just be read from.
        return dict(_store_cache["store"])
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            parsed = json.load(f)
        except json.JSONDecodeError as exc:
            # Повреждённое хранилище нельзя трактовать как пустое: иначе следующий POST
            # мог бы затереть рабочие данные. Останавливаем запись и требуем восстановление.
            raise RuntimeError(f"storage.json is corrupted: {exc}")
    _store_cache["mtime"] = current_mtime
    _store_cache["store"] = parsed
    return dict(parsed)


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


def save_store(store, backup=True, durable=True, refresh_public=True):
    # v1634: never use one shared `/data/storage.json.tmp` filename. Even though
    # most writers are protected by the in-process `_lock`, gunicorn restarts,
    # overlapping processes during deploys, or a future multi-worker setup can
    # still have two processes writing at the same time. With a shared tmp name,
    # process A can rename the tmp file created by process B; B then reaches
    # os.replace() and gets FileNotFoundError. Use a unique temp file in the same
    # directory and atomically replace DATA_FILE from that unique path instead.
    # v1609: `backup=False` is used by every frequent atomic per-edit endpoint
    # (section/graph/schedule merge and patch) — same fix as v1576 applied to
    # generation-progress saves, extended here. Backing up (a full file copy +
    # backups-directory listing) on every single manual edit was holding the
    # global storage lock far longer than the edit itself needs, which matters
    # a lot once several people (e.g. 4 concurrent editors) are saving at
    # once: every save queues behind the previous one's backup-copy time,
    # widening the exact race window client-side reconciliation has to land
    # in before being read as stale. A backup still happens for the plain
    # whole-document save path (`set_key`) and via the explicit manual backup
    # action — this only removes it from the hot, frequent, per-edit path.
    os.makedirs(DATA_DIR, exist_ok=True)
    if backup:
        _backup_current_store()
    tmp = None
    try:
        # Same filesystem/directory is required so os.replace stays atomic.
        fd, tmp = tempfile.mkstemp(prefix="storage.json.", suffix=".tmp", dir=DATA_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            if durable:
                os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)
        tmp = None
        # v1670: this dict is now exactly what's on disk — update the cache
        # directly instead of leaving the next load_store() call to re-read
        # and re-parse the file it just wrote.
        try:
            _store_cache["mtime"] = os.path.getmtime(DATA_FILE)
            _store_cache["store"] = store
        except OSError:
            _invalidate_store_cache()
        if durable:
            # Persist the directory entry replacement as well when possible.
            try:
                dir_fd = os.open(DATA_DIR, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        # v1682: every editor save updates only a tiny status sidecar. Heavy
        # public JSON/gzip is rebuilt only on publication (or first deploy).
        try:
            project_for_status = _public_project_from_store(store)
            _write_public_status_sidecar(project_for_status)
        except Exception as exc:
            app.logger.warning("public status refresh failed: %s", exc)
        if refresh_public:
            try:
                project_for_public = _public_project_from_store(store)
                scope = str(((project_for_public or {}).get("_syncMeta") or {}).get("scope") or "")
                heavy_missing = not (os.path.exists(PUBLIC_INDEX_FILE) and os.path.exists(PUBLIC_INDEX_META_FILE))
                if heavy_missing or scope in {"publication", "publish-weeks"}:
                    _refresh_public_index_from_store(store, force=True)
                    _refresh_public_sidecar_from_store(store, force=True)
            except Exception as exc:
                app.logger.warning("public sidecar refresh failed: %s", exc)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass




def _manual_schedule_touched_ids(base_schedule, local_schedule):
    """Return instance ids explicitly changed by a manual timetable save."""
    base_schedule = base_schedule if isinstance(base_schedule, dict) else {}
    local_schedule = local_schedule if isinstance(local_schedule, dict) else {}
    touched = set()
    for key in ("assignment",):
        b = base_schedule.get(key) or {}
        l = local_schedule.get(key) or {}
        if isinstance(b, dict) and isinstance(l, dict):
            for sid in set(map(str, b.keys())) | set(map(str, l.keys())):
                if b.get(sid) != l.get(sid):
                    touched.add(sid)
    def rows_by_id(rows):
        return {str(x.get("instId") or ""): x for x in (rows or []) if isinstance(x, dict) and str(x.get("instId") or "")}
    bm, lm = rows_by_id(base_schedule.get("instances")), rows_by_id(local_schedule.get("instances"))
    for sid in set(bm) | set(lm):
        if bm.get(sid) != lm.get(sid):
            touched.add(sid)
    for key in ("locked", "unplaced"):
        b = set(map(str, base_schedule.get(key) or []))
        l = set(map(str, local_schedule.get(key) or []))
        touched.update(b ^ l)
    return touched


def _protect_manual_schedule_ids(ids):
    ids = {str(x) for x in (ids or []) if str(x)}
    if not ids:
        return
    with _generation_jobs_lock:
        for job in _generation_jobs.values():
            if job.get("kind") == "schedule" and job.get("status") in {"queued", "running"}:
                protected = job.setdefault("protected_ids", set())
                protected.update(ids)


def _generation_protected_ids(job_id):
    if not job_id:
        return set()
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        return set(job.get("protected_ids") or []) if job else set()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


def _editor_compact_project(data):
    """Return editor payload without historical publication snapshots.

    scheduleVersions can contain dozens of full semester snapshots and was the
    largest source of browser RAM use.  Keep only metadata in ordinary editor
    responses; the selected snapshot is fetched lazily by id when restoring.
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    compact_versions = []
    for row in (data.get("scheduleVersions") or []):
        if not isinstance(row, dict):
            continue
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        stats = ((snapshot.get("schedule") or {}).get("stats") or {}) if isinstance(snapshot, dict) else {}
        compact_versions.append({
            "id": row.get("id"),
            "at": row.get("at"),
            "by": row.get("by"),
            "note": row.get("note"),
            "placed": stats.get("placed"),
            "hasSnapshot": bool(snapshot),
        })
    out["scheduleVersions"] = compact_versions
    return out


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
                # v1670: written outside save_store(), keep the cache in sync.
                try:
                    _store_cache["mtime"] = os.path.getmtime(DATA_FILE)
                    _store_cache["store"] = backup_store
                except OSError:
                    _invalidate_store_cache()
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

    if key == "schedule-data-v2" and request.args.get("editor") == "1":
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            return jsonify({"key": key, "value": _editor_compact_project(parsed)})
        except Exception:
            pass
    return jsonify({"key": key, "value": value})


@app.get("/api/storage-version/<key>/<version_id>")
def get_storage_version(key, version_id):
    """Fetch one historical publication snapshot lazily for restore."""
    with _lock:
        store = load_store()
        raw = store.get(key)
    if raw is None:
        return jsonify({"error": "not found"}), 404
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return jsonify({"error": "invalid storage"}), 422
    for row in (data.get("scheduleVersions") or []) if isinstance(data, dict) else []:
        if isinstance(row, dict) and str(row.get("id") or "") == str(version_id):
            if not isinstance(row.get("snapshot"), dict):
                return jsonify({"error": "snapshot missing"}), 404
            return jsonify({"ok": True, "version": row})
    return jsonify({"error": "version not found"}), 404


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
    # v1681: never serialize the whole project merely to report an informational
    # byte count. This endpoint is polled by every open editor and must remain O(1).
    try:
        size = len(value.encode("utf-8")) if isinstance(value, str) else 0
    except Exception:
        size = 0
    return jsonify({"key": key, "token": token, "meta": meta, "size": size})


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
    # v1681: new canonical writes keep the project as a nested JSON object instead
    # of an escaped JSON string. API readers already support both representations.
    if key == "schedule-data-v2" and isinstance(incoming_value, str):
        try:
            incoming_value = json.loads(incoming_value)
        except Exception:
            return jsonify({"error": "Не удалось разобрать данные расписания"}), 422
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



@app.get("/api/public-index")
def public_index():
    """Serve prebuilt public dictionaries without parsing storage.json."""
    try:
        if not (os.path.exists(PUBLIC_INDEX_FILE) and os.path.exists(PUBLIC_INDEX_META_FILE)):
            store = load_store()
            project = _public_project_from_store(store)
            if not project:
                return jsonify({"error": "Рабочие данные отсутствуют"}), 404
            _refresh_public_index_from_store(store, force=True)
        with open(PUBLIC_INDEX_META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        etag = str(meta.get("etag") or "")
        marker = str(meta.get("marker") or "")
        if request.headers.get("If-None-Match", "").strip('"') == etag:
            resp = Response(status=304)
        else:
            accept_gzip = "gzip" in str(request.headers.get("Accept-Encoding") or "").lower()
            path = PUBLIC_INDEX_GZIP_FILE if accept_gzip and os.path.exists(PUBLIC_INDEX_GZIP_FILE) else PUBLIC_INDEX_FILE
            with open(path, "rb") as f:
                body = f.read()
            resp = Response(body, status=200, mimetype="application/json")
            if path == PUBLIC_INDEX_GZIP_FILE:
                resp.headers["Content-Encoding"] = "gzip"
        resp.headers["ETag"] = f'"{etag}"'
        resp.headers["Vary"] = "Accept-Encoding"
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["X-Public-Snapshot"] = marker[:96]
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/public-status")
def public_status():
    """Tiny fast publication state; hot path never parses the full timetable."""
    try:
        if not os.path.exists(PUBLIC_STATUS_FILE):
            store = load_store()
            project = _public_project_from_store(store)
            if not project:
                return jsonify({"publicationStatus": "unpublished", "publishedAt": ""})
            _write_public_status_sidecar(project)
        with open(PUBLIC_STATUS_FILE, "rb") as f:
            body = f.read()
        resp = Response(body, status=200, mimetype="application/json")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/public-schedule/<kind>/<path:entity_id>")
def public_schedule_shard(kind, entity_id):
    """Serve one prebuilt public entity/week file without touching storage.json."""
    try:
        if kind not in {"group", "teacher"}:
            return jsonify({"error": "unknown public schedule kind"}), 404
        scope = str(request.args.get("scope") or "semester")
        week_raw = request.args.get("week")
        week = int(week_raw) if str(week_raw or "").isdigit() else 0
        if scope != "week" or week <= 0:
            scope = "semester"
            week = 0

        manifest = _load_public_shard_manifest()
        # v1706: never rebuild the whole public semester in a visitor request.
        # After deploy/restart create only the requested entity/week shard.
        if not manifest:
            manifest = _ensure_public_shard_on_demand(kind, entity_id, scope, week)
            if not manifest:
                return jsonify({"error": "Расписание не опубликовано для выбранной недели/объекта"}), 404
        marker = str(manifest.get("marker") or "")
        snapshot_name = str(manifest.get("snapshot") or "")
        snapshot_dir = os.path.join(PUBLIC_SHARD_DIR, snapshot_name)
        entity_dir = _public_entity_dir(snapshot_dir, kind, entity_id)
        filename = f"week-{week}.json" if scope == "week" else "semester.json"
        plain_path = os.path.join(entity_dir, filename)
        gzip_path = plain_path + ".gz"
        if not os.path.exists(plain_path):
            manifest = _ensure_public_shard_on_demand(kind, entity_id, scope, week) or manifest
            marker = str(manifest.get("marker") or marker)
            snapshot_name = str(manifest.get("snapshot") or snapshot_name)
            snapshot_dir = os.path.join(PUBLIC_SHARD_DIR, snapshot_name)
            entity_dir = _public_entity_dir(snapshot_dir, kind, entity_id)
            plain_path = os.path.join(entity_dir, filename)
            gzip_path = plain_path + ".gz"
        if not os.path.exists(plain_path):
            return jsonify({"error": "Расписание не опубликовано для выбранной недели/объекта"}), 404

        # ETag can be derived from the ready bytes and is cached by the OS page
        # cache; no project parsing, schedule filtering or JSON serialization.
        accept_gzip = "gzip" in str(request.headers.get("Accept-Encoding") or "").lower()
        path = gzip_path if accept_gzip and os.path.exists(gzip_path) else plain_path
        stat = os.stat(path)
        etag_seed = f"{snapshot_name}:{kind}:{entity_id}:{filename}:{stat.st_size}:{int(stat.st_mtime_ns)}"
        etag = hashlib.sha1(etag_seed.encode("utf-8")).hexdigest()
        if request.headers.get("If-None-Match", "").strip('"') == etag:
            resp = Response(status=304)
        else:
            with open(path, "rb") as f:
                body = f.read()
            resp = Response(body, status=200, mimetype="application/json")
            if path == gzip_path:
                resp.headers["Content-Encoding"] = "gzip"
        resp.headers["ETag"] = f'"{etag}"'
        resp.headers["Vary"] = "Accept-Encoding"
        # Published snapshot files are immutable by snapshot id. The endpoint URL
        # itself is stable, so keep a short freshness window + long stale grace.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["X-Public-Snapshot"] = marker[:96]
        resp.headers["X-Public-Scope"] = scope
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/public-bootstrap")
def public_bootstrap():
    """Very fast public payload served from a publication-only sidecar."""
    try:
        # First request after an old deploy may not have the sidecar yet. Build it once.
        if not (os.path.exists(PUBLIC_BOOTSTRAP_FILE) and os.path.exists(PUBLIC_BOOTSTRAP_META_FILE)):
            store = load_store()
            if not _refresh_public_sidecar_from_store(store, force=True):
                return jsonify({"error": "Рабочие данные отсутствуют"}), 404

        try:
            with open(PUBLIC_BOOTSTRAP_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
            etag = str(meta.get("etag") or "")
            marker = str(meta.get("marker") or "")
        except Exception:
            store = load_store()
            _refresh_public_sidecar_from_store(store, force=True)
            with open(PUBLIC_BOOTSTRAP_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
            etag = str(meta.get("etag") or "")
            marker = str(meta.get("marker") or "")

        if request.headers.get("If-None-Match", "").strip('"') == etag:
            resp = Response(status=304)
        else:
            accept_gzip = "gzip" in str(request.headers.get("Accept-Encoding") or "").lower()
            path = PUBLIC_BOOTSTRAP_GZIP_FILE if accept_gzip and os.path.exists(PUBLIC_BOOTSTRAP_GZIP_FILE) else PUBLIC_BOOTSTRAP_FILE
            with open(path, "rb") as f:
                body = f.read()
            resp = Response(body, status=200, mimetype="application/json")
            if path == PUBLIC_BOOTSTRAP_GZIP_FILE:
                resp.headers["Content-Encoding"] = "gzip"
        resp.headers["ETag"] = f'"{etag}"'
        resp.headers["Vary"] = "Accept-Encoding"
        # v1637: проверяем актуальность на каждом открытии, но ETag позволяет
        # получить дешёвый 304 без повторной передачи/разбора большого JSON.
        # Это убирает до 30 секунд расхождения между редактором и публичным контуром.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["X-Public-Snapshot"] = marker[:96]
        return resp
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



def _apply_generation_group_progress(storage_key, progress, backup=False, job_id=None, durable=False):
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

    # v1595: manual editing always wins over a calculation that was already
    # running. Every manual cell/full schedule save marks the touched instance
    # ids as protected for active jobs. Auto progress may continue for all other
    # ids, but it must not overwrite, remove, lock/unlock, or re-unplace those ids.
    protected_ids = _generation_protected_ids(job_id)
    if protected_ids:
        replace_ids -= protected_ids
        instances_remove -= protected_ids
        assignment_remove -= protected_ids
        instances_set = {str(k): v for k, v in instances_set.items() if str(k) not in protected_ids}
        assignment_set = {str(k): v for k, v in assignment_set.items() if str(k) not in protected_ids}
        unplaced_target = [x for x in unplaced_target if x not in protected_ids]
        locked_target = [x for x in locked_target if x not in protected_ids]
        unplaced_set = [x for x in unplaced_set if x not in protected_ids]
        unplaced_remove -= protected_ids
        locked_set = [x for x in locked_set if x not in protected_ids]
        locked_remove -= protected_ids

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
            "sessionId": str(meta.get("sessionId") or ""),
        }
        store[storage_key] = remote
        # Internal auto checkpoints are recoverable and are followed by a durable
        # final/manual save. Avoid a full fsync every few seconds so manual saves
        # are not stalled behind the scheduler.
        save_store(store, backup=backup, durable=durable)
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
                        if not _apply_generation_group_progress(storage_key, progress, job_id=job_id):
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
                                current["progress_history"] = history[-6:]

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
                    _apply_generation_group_progress(storage_key, final_patch, job_id=job_id, durable=True)
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
                        # v1684: do not pin a second full generated schedule in RAM.
                        # finalPatch has already been committed to canonical storage.
                        "result": {"ok": True, "saved": True}, "finalPatch": None,
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
    week_number = max(0, int(body.get("weekNumber") or 0))
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
            "id": job_id, "kind": "schedule", "status": "queued", "phase": "queued", "created_at": time.time(),
            "queue_position": queued + 1, "storage_key": storage_key, "group_id": group_id, "protected_ids": set(),
        }
    raw_for_hash = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    storage_fingerprint = hashlib.blake2b(raw_for_hash.encode("utf-8"), digest_size=16).hexdigest()
    payload = {"data": data, "prior": data.get("schedule"), "groupId": group_id, "storageKey": storage_key, "fastMode": fast_mode, "weekNumber": week_number, "_cacheKey": storage_fingerprint}
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
            "created_at", "started_at", "finished_at", "progress_file", "progress", "progress_history", "process", "protected_ids"
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


def _merge_three_way_no_clobber(base, local, remote, path=(), conflicts=None):
    """Three-way merge for collaborative timetable edits.

    Different fields/rows/assignment ids are merged normally. If both clients changed
    the *same* leaf since the common base, the already persisted server value wins and
    the path is reported as a conflict. This prevents a later stale autosave from
    silently overwriting another editor's newer work.
    """
    if conflicts is None:
        conflicts = []
    if _json_same(local, base):
        return remote
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
            child_path = path + (str(key),)
            if l is _MISSING:
                if b is _MISSING:
                    continue
                if r is _MISSING or _json_same(r, b):
                    out.pop(key, None)
                else:
                    # Local deleted while another editor changed the same key.
                    conflicts.append(".".join(child_path))
                    out[key] = r
                continue
            if b is _MISSING:
                if r is _MISSING or _json_same(l, r):
                    out[key] = l
                else:
                    conflicts.append(".".join(child_path))
                    out[key] = r
                continue
            if r is _MISSING:
                # Another editor deleted the key while this client changed it.
                conflicts.append(".".join(child_path))
                out.pop(key, None)
                continue
            out[key] = _merge_three_way_no_clobber(b, l, r, child_path, conflicts)
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
                child_path = path + (f"{key}={ident}",)
                if l is _MISSING:
                    if b is _MISSING and r is not _MISSING:
                        out.append(r)
                    elif b is not _MISSING and r is not _MISSING:
                        if _json_same(r, b):
                            # uncontested local deletion
                            pass
                        else:
                            conflicts.append(".".join(child_path))
                            out.append(r)
                    continue
                if b is _MISSING:
                    if r is _MISSING:
                        out.append(l)
                    elif _json_same(l, r):
                        out.append(l)
                    elif isinstance(l, dict) and isinstance(r, dict):
                        out.append(_merge_three_way_no_clobber({}, l, r, child_path, conflicts))
                    else:
                        conflicts.append(".".join(child_path))
                        out.append(r)
                    continue
                if r is _MISSING:
                    conflicts.append(".".join(child_path))
                    # preserve the already-persisted deletion
                    continue
                out.append(_merge_three_way_no_clobber(b, l, r, child_path, conflicts))
            return out
        # Opaque arrays (for example id lists) cannot be safely field-merged when
        # both users changed them. Keep the server copy rather than clobber it.
        conflicts.append(".".join(path) or "<root>")
        return remote

    conflicts.append(".".join(path) or "<root>")
    return remote


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
            "sessionId": str(meta.get("sessionId") or ""),
        }
        encoded = json.dumps(merged, ensure_ascii=False)
        store[key] = merged
        _maybe_periodic_backup()
        save_store(store, backup=False)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})




def _schedule_patch_shape_valid(patch):
    if not isinstance(patch, dict):
        return False
    assignment_set = patch.get("assignmentSet") or {}
    assignment_remove = patch.get("assignmentRemove") or []
    instances_set = patch.get("instancesSet") or {}
    instances_remove = patch.get("instancesRemove") or []
    locked_add = patch.get("lockedAdd") or []
    locked_remove = patch.get("lockedRemove") or []
    unplaced_add = patch.get("unplacedAdd") or []
    unplaced_remove = patch.get("unplacedRemove") or []
    return (isinstance(assignment_set, dict) and isinstance(instances_set, dict)
            and all(isinstance(x, list) for x in (assignment_remove, instances_remove, locked_add, locked_remove, unplaced_add, unplaced_remove)))


def _apply_schedule_patch_py(schedule, patch):
    schedule = dict(schedule or {})
    patch = patch or {}
    assignment_set = patch.get("assignmentSet") or {}
    assignment_remove = patch.get("assignmentRemove") or []
    instances_set = patch.get("instancesSet") or {}
    instances_remove = patch.get("instancesRemove") or []
    locked_add = patch.get("lockedAdd") or []
    locked_remove = patch.get("lockedRemove") or []
    unplaced_add = patch.get("unplacedAdd") or []
    unplaced_remove = patch.get("unplacedRemove") or []
    instances = [dict(x) for x in (schedule.get("instances") or []) if isinstance(x, dict)]
    remove_ids = {str(x) for x in instances_remove}
    by_id = {str(x.get("instId") or ""): x for x in instances if str(x.get("instId") or "") and str(x.get("instId") or "") not in remove_ids}
    order = [str(x.get("instId") or "") for x in instances if str(x.get("instId") or "") and str(x.get("instId") or "") not in remove_ids]
    for inst_id, value in instances_set.items():
        sid = str(inst_id)
        if not sid or not isinstance(value, dict):
            continue
        if sid not in by_id:
            order.append(sid)
        row = dict(value); row["instId"] = sid; by_id[sid] = row
    schedule["instances"] = [by_id[sid] for sid in order if sid in by_id]
    valid_ids = {str(x.get("instId") or "") for x in schedule["instances"] if str(x.get("instId") or "")}
    assignment = dict(schedule.get("assignment") or {})
    for inst_id, value in assignment_set.items():
        sid = str(inst_id)
        if sid and isinstance(value, dict):
            assignment[sid] = dict(value)
    for inst_id in assignment_remove:
        assignment.pop(str(inst_id), None)
    assignment = {sid: value for sid, value in assignment.items() if sid in valid_ids}
    def apply_ids(existing, adds, removes):
        rem = {str(x) for x in removes}
        out = [str(x) for x in (existing or []) if str(x) in valid_ids and str(x) not in rem]
        seen = set(out)
        for x in adds:
            sx = str(x)
            if sx and sx in valid_ids and sx not in seen:
                out.append(sx); seen.add(sx)
        return out
    schedule["assignment"] = assignment
    schedule["locked"] = apply_ids(schedule.get("locked") or [], locked_add, locked_remove)
    schedule["unplaced"] = [sid for sid in apply_ids(schedule.get("unplaced") or [], unplaced_add, unplaced_remove) if sid not in assignment]
    if isinstance(patch.get("stats"), dict):
        schedule["stats"] = dict(patch.get("stats") or {})
    touched = set(map(str, assignment_set.keys())) | set(map(str, instances_set.keys()))
    for seq in (assignment_remove, instances_remove, locked_add, locked_remove, unplaced_add, unplaced_remove):
        touched.update(str(x) for x in seq if str(x))
    return schedule, touched


def _instance_group_subgroups_py(inst, group_id):
    gid = str(group_id or "")
    out = set()
    if isinstance(inst, dict):
        for part in (inst.get("streamParticipants") or []):
            if isinstance(part, dict) and str(part.get("groupId") or "") == gid:
                try: out.add(int(part.get("subgroup") or 0))
                except Exception: out.add(0)
    if out:
        return out
    if isinstance(inst, dict) and str(inst.get("groupId") or "") == gid:
        try: return {int(inst.get("subgroup") or 0)}
        except Exception: return {0}
    if gid in _schedule_instance_group_ids(inst):
        return {0}
    return set()


def _schedule_group_overlap_pairs(project, schedule, only_ids=None):
    """Find real timetable overlaps without scanning every assignment pair.

    v1678: hot cell saves usually touch one or a handful of instance ids. The old
    implementation still performed an O(N²) pass over the complete semester and
    only *inside* the nested loop checked whether one side was touched. With a
    large timetable this made every tiny cell patch consume a CPU core. Build a
    day/period index once and compare touched cards only with cards that can
    physically collide in the same slot. Broad callers (only_ids=None) still
    validate every slot, but do it bucket-by-bucket rather than globally.
    """
    schedule = schedule if isinstance(schedule, dict) else {}
    by_id = {str(x.get("instId") or ""): x for x in (schedule.get("instances") or [])
             if isinstance(x, dict) and str(x.get("instId") or "")}
    assignment = schedule.get("assignment") or {}
    active_ids = [str(sid) for sid, value in assignment.items()
                  if str(sid) in by_id and isinstance(value, dict)]
    only = {str(x) for x in (only_ids or []) if str(x)} if only_ids is not None else None

    slot_ids = {}
    for sid in active_ids:
        a = assignment[sid]
        try:
            key = (int(a.get("day", -1)), int(a.get("period", -1)))
        except Exception:
            continue
        slot_ids.setdefault(key, []).append(sid)

    cfg = project.get("config") or {}
    weeks_cache = {}
    groups_cache = {}
    subgroup_cache = {}
    def weeks(sid):
        if sid not in weeks_cache:
            weeks_cache[sid] = set(_instance_weeks_py(cfg, by_id[sid]))
        return weeks_cache[sid]
    def groups(sid):
        if sid not in groups_cache:
            groups_cache[sid] = _schedule_instance_group_ids(by_id[sid])
        return groups_cache[sid]
    def subgroups(sid, gid):
        key = (sid, str(gid))
        if key not in subgroup_cache:
            subgroup_cache[key] = _instance_group_subgroups_py(by_id[sid], gid)
        return subgroup_cache[key]

    out = []
    seen_pairs = set()
    if only is None:
        candidates = [(sid, oid) for ids in slot_ids.values() for pos, sid in enumerate(ids) for oid in ids[pos+1:]]
    else:
        candidates = []
        for sid in only:
            if sid not in assignment or sid not in by_id or not isinstance(assignment.get(sid), dict):
                continue
            a = assignment[sid]
            try:
                key = (int(a.get("day", -1)), int(a.get("period", -1)))
            except Exception:
                continue
            for oid in slot_ids.get(key, []):
                if oid == sid:
                    continue
                pair = tuple(sorted((sid, oid)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append(pair)

    for sid, oid in candidates:
        wa = weeks(sid)
        if not wa or not wa.intersection(weeks(oid)):
            continue
        inst, other = by_id[sid], by_id[oid]
        groups_a = groups(sid)
        group_conflict = False
        for gid in groups_a.intersection(groups(oid)):
            s1 = subgroups(sid, gid); s2 = subgroups(oid, gid)
            allowed = bool(s1 and s2 and 0 not in s1 and 0 not in s2 and s1.isdisjoint(s2))
            if not allowed:
                out.append((sid, oid, gid)); group_conflict = True; break
        if group_conflict:
            continue

        a, oa = assignment[sid], assignment[oid]
        t1, t2 = str(inst.get("teacherId") or ""), str(other.get("teacherId") or "")
        vacancy = bool(inst.get("isVacancyTeacher")) or bool(other.get("isVacancyTeacher"))
        explicit = bool(a.get("manualTeacherMultiRoom") or oa.get("manualTeacherMultiRoom") or
                        a.get("manualSameSubjectTeacherSameRoom") or oa.get("manualSameSubjectTeacherSameRoom") or
                        a.get("manualMultiGroupRoom") or oa.get("manualMultiGroupRoom"))
        if t1 and t1 == t2 and not vacancy and not explicit:
            same_group_subgroups = False
            for gid in groups_a.intersection(groups(oid)):
                s1 = subgroups(sid, gid); s2 = subgroups(oid, gid)
                if s1 and s2 and 0 not in s1 and 0 not in s2 and s1.isdisjoint(s2):
                    same_group_subgroups = True; break
            if not same_group_subgroups:
                out.append((sid, oid, "teacher:" + t1))
    return out


def _sanitize_graph_schedule_overlaps(project, before_schedule, schedule, touched_ids, assignment_set_ids):
    before_assignment = (before_schedule or {}).get("assignment") or {}
    assignment_set_ids = {str(x) for x in (assignment_set_ids or []) if str(x)}
    touched_ids = {str(x) for x in (touched_ids or []) if str(x)}
    assignment = dict(schedule.get("assignment") or {})
    unplaced = [str(x) for x in (schedule.get("unplaced") or [])]
    unplaced_set = set(unplaced)
    for _ in range(1000):
        pairs = _schedule_group_overlap_pairs(project, {**schedule, "assignment": assignment}, touched_ids)
        if not pairs: break
        sid, oid, _gid = pairs[0]
        sid_old, oid_old = sid in before_assignment, oid in before_assignment
        if sid_old and not oid_old: victim = oid
        elif oid_old and not sid_old: victim = sid
        elif sid in assignment_set_ids and oid not in assignment_set_ids: victim = sid
        elif oid in assignment_set_ids and sid not in assignment_set_ids: victim = oid
        else: victim = max(str(sid), str(oid))
        assignment.pop(victim, None)
        if victim not in unplaced_set:
            unplaced.append(victim); unplaced_set.add(victim)
    schedule["assignment"] = assignment
    schedule["unplaced"] = [sid for sid in unplaced if sid not in assignment]
    return schedule


def _sanitize_merged_schedule_overlaps(project, before_schedule, schedule):
    before_schedule = before_schedule if isinstance(before_schedule, dict) else {}
    schedule = schedule if isinstance(schedule, dict) else {}
    b_instances = {str(x.get("instId") or ""): x for x in (before_schedule.get("instances") or []) if isinstance(x, dict) and str(x.get("instId") or "")}
    a_instances = {str(x.get("instId") or ""): x for x in (schedule.get("instances") or []) if isinstance(x, dict) and str(x.get("instId") or "")}
    b_assignment = before_schedule.get("assignment") or {}; a_assignment = schedule.get("assignment") or {}
    all_ids = set(b_instances) | set(a_instances) | set(map(str,b_assignment.keys())) | set(map(str,a_assignment.keys()))
    touched, assignment_set = set(), set()
    for sid in all_ids:
        if not _json_same(b_instances.get(sid, _MISSING), a_instances.get(sid, _MISSING)) or not _json_same(b_assignment.get(sid, _MISSING), a_assignment.get(sid, _MISSING)):
            touched.add(sid)
        if sid in a_assignment and not _json_same(b_assignment.get(sid, _MISSING), a_assignment.get(sid, _MISSING)):
            assignment_set.add(sid)
    return _sanitize_graph_schedule_overlaps(project, before_schedule, schedule, touched, assignment_set) if touched else schedule


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
    schedule_patch = body.get("schedulePatch")
    if not isinstance(changes, list) or not isinstance(history_append, list):
        return jsonify({"error": "Неверный формат быстрого сохранения графика"}), 400
    if schedule_patch is not None and not _schedule_patch_shape_valid(schedule_patch):
        return jsonify({"error": "Неверный формат синхронизации расписания с графиком"}), 400
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
        if schedule_patch is not None:
            before_schedule = dict(merged.get("schedule") or {})
            next_schedule, touched = _apply_schedule_patch_py(before_schedule, schedule_patch)
            next_schedule = _sanitize_graph_schedule_overlaps(merged, before_schedule, next_schedule, touched, (schedule_patch.get("assignmentSet") or {}).keys())
            merged["schedule"] = next_schedule
        sync_meta = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "graph-patch-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "graphs",
            "sessionId": str(meta.get("sessionId") or ""),
        }
        merged["_syncMeta"] = sync_meta
        store[key] = merged
        _maybe_periodic_backup()
        save_store(store, backup=False)
    return jsonify({"ok": True, "token": sync_meta["saveId"], "rows": returned_rows, "schedule": merged.get("schedule") if schedule_patch is not None else None, "_syncMeta": sync_meta})


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
        store[key] = merged
        _maybe_periodic_backup()
        save_store(store, backup=False)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})


# v1565: immediate atomic timetable-cell patch.  This endpoint intentionally
# updates only explicitly named instance/assignment/locked/unplaced entries under the
# storage lock.  It never replaces the whole schedule object.
@app.post("/api/storage-schedule-patch/<key>")
def storage_schedule_patch(key):
    """Queue one atomic timetable delta and coalesce physical storage writes.

    v1681 keeps the semantic persistence unit as instId/cell, but many requests
    arriving together are applied under one storage lock and one storage.json
    replace. This removes the worst CPU/disk amplification under concurrent use.
    """
    body = request.get_json(force=True, silent=True) or {}
    patch = body.get("patch") or {}
    meta = body.get("meta") or {}
    if not _schedule_patch_shape_valid(patch):
        return jsonify({"error": "Неверный формат изменений ячейки"}), 400

    touched_ids = set(map(str, (patch.get("assignmentSet") or {}).keys())) | set(map(str, (patch.get("instancesSet") or {}).keys()))
    for field in ("assignmentRemove", "instancesRemove", "lockedAdd", "lockedRemove", "unplacedAdd", "unplacedRemove"):
        touched_ids.update(str(x) for x in (patch.get(field) or []) if str(x))
    _protect_manual_schedule_ids(touched_ids)

    item = {
        "key": str(key),
        "patch": patch,
        "meta": meta if isinstance(meta, dict) else {},
        "event": threading.Event(),
        "result": None,
    }
    _ensure_schedule_patch_worker()
    with _schedule_patch_queue_lock:
        _schedule_patch_queue.append(item)
        _schedule_patch_queue_event.set()
    if not item["event"].wait(timeout=45.0):
        return jsonify({"error": "Сервер не успел подтвердить сохранение ячейки"}), 503
    result = item.get("result") or {"status": 500, "body": {"error": "Неизвестная ошибка сохранения"}}
    return jsonify(result.get("body") or {}), int(result.get("status") or 500)


def _confirmed_schedule_patch(patch):
    out = {
        "assignmentSet": {str(k): v for k, v in (patch.get("assignmentSet") or {}).items() if isinstance(v, dict)},
        "assignmentRemove": [str(x) for x in (patch.get("assignmentRemove") or [])],
        "instancesSet": {str(k): v for k, v in (patch.get("instancesSet") or {}).items() if isinstance(v, dict)},
        "instancesRemove": [str(x) for x in (patch.get("instancesRemove") or [])],
        "lockedAdd": [str(x) for x in (patch.get("lockedAdd") or [])],
        "lockedRemove": [str(x) for x in (patch.get("lockedRemove") or [])],
        "unplacedAdd": [str(x) for x in (patch.get("unplacedAdd") or [])],
        "unplacedRemove": [str(x) for x in (patch.get("unplacedRemove") or [])],
    }
    if isinstance(patch.get("stats"), dict):
        out["stats"] = dict(patch.get("stats") or {})
    return out


def _schedule_patch_batch_loop():
    while True:
        _schedule_patch_queue_event.wait()
        # Tiny collection window: imperceptible to a user, large enough to merge
        # bursts from several simultaneous editors into one physical write.
        time.sleep(max(0.0, SCHEDULE_PATCH_BATCH_WINDOW_SECONDS))
        with _schedule_patch_queue_lock:
            batch = _schedule_patch_queue[:SCHEDULE_PATCH_BATCH_MAX]
            del _schedule_patch_queue[:len(batch)]
            if not _schedule_patch_queue:
                _schedule_patch_queue_event.clear()
        if not batch:
            continue

        # Group by storage key; normal operation has exactly schedule-data-v2.
        by_key = OrderedDict()
        for item in batch:
            by_key.setdefault(item["key"], []).append(item)

        for storage_key, items in by_key.items():
            successful = []
            try:
                with _lock:
                    if not os.path.exists(DATA_FILE):
                        raise FileNotFoundError("Хранилище отсутствует")
                    store = load_store()
                    raw = store.get(storage_key)
                    if raw is None:
                        raise KeyError("Рабочие данные не найдены")
                    try:
                        base_project = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception as exc:
                        raise RuntimeError(f"Не удалось прочитать рабочие данные: {exc}")
                    if not isinstance(base_project, dict):
                        raise RuntimeError("Рабочие данные имеют неверный формат")
                    # Never mutate the cached object in place when storage.json is
                    # already using object representation.
                    project = dict(base_project)

                    for item in items:
                        patch = item["patch"]
                        before_schedule = project.get("schedule") if isinstance(project.get("schedule"), dict) else {}
                        next_schedule, touched = _apply_schedule_patch_py(before_schedule, patch)
                        overlap_pairs = _schedule_group_overlap_pairs(project, next_schedule, touched)
                        if overlap_pairs:
                            sid, oid, gid = overlap_pairs[0]
                            item["result"] = {
                                "status": 409,
                                "body": {
                                    "error": "Нельзя наложить конфликтующие пары в одну ячейку",
                                    "groupId": gid,
                                    "instId": sid,
                                    "conflictInstId": oid,
                                },
                            }
                            continue
                        project["schedule"] = next_schedule
                        successful.append(item)

                    if successful:
                        last_meta = successful[-1].get("meta") or {}
                        sync_meta = {
                            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
                            "saveId": "schedule-batch-" + uuid.uuid4().hex,
                            "userId": str(last_meta.get("userId") or ""),
                            "by": str(last_meta.get("by") or "Пользователь"),
                            "scope": "schedule-cell",
                            "sessionId": str(last_meta.get("sessionId") or ""),
                            "batchSize": len(successful),
                        }
                        project["_syncMeta"] = sync_meta
                        # v1681: store the project as a JSON object, not as a JSON
                        # string inside JSON. Old backups remain readable, while new
                        # saves avoid a second full json.dumps + escaping pass.
                        store[storage_key] = project
                        # v1686: never copy the multi-megabyte backup while holding
                        # the hot storage lock. That pause was long enough to make
                        # queued cell requests hit HTTP 503 under load.
                        save_store(store, backup=False, durable=False, refresh_public=False)
                        for item in successful:
                            item["result"] = {
                                "status": 200,
                                "body": {
                                    "ok": True,
                                    "token": sync_meta["saveId"],
                                    "patch": _confirmed_schedule_patch(item["patch"]),
                                    "_syncMeta": sync_meta,
                                },
                            }
                if successful:
                    _maybe_periodic_backup_async()
            except FileNotFoundError as exc:
                for item in items:
                    if item.get("result") is None:
                        item["result"] = {"status": 409, "body": {"error": str(exc)}}
            except KeyError as exc:
                for item in items:
                    if item.get("result") is None:
                        item["result"] = {"status": 404, "body": {"error": str(exc).strip("'")}}
            except Exception as exc:
                app.logger.exception("schedule patch batch failed")
                for item in items:
                    if item.get("result") is None:
                        item["result"] = {"status": 500, "body": {"error": f"Ошибка сохранения ячейки: {exc}"}}
            finally:
                for item in items:
                    item["event"].set()


def _ensure_schedule_patch_worker():
    global _schedule_patch_worker_started
    with _schedule_patch_queue_lock:
        if _schedule_patch_worker_started:
            return
        _schedule_patch_worker_started = True
        threading.Thread(target=_schedule_patch_batch_loop, name="schedule-patch-batcher", daemon=True).start()


# v1489: atomic field/group/slot merge for simultaneous work in «Расписание».
# It uses the same recursive three-way merge as graphs, but owns timetable sections only.
# Different groups/instances/assignment keys survive regardless of save timing.

def _schedule_instance_group_ids(inst):
    if not isinstance(inst, dict):
        return set()
    out = set()
    gid = str(inst.get("groupId") or "")
    if gid:
        out.add(gid)
    for key in ("streamGroupIds", "groupIds"):
        rows = inst.get(key) or []
        if isinstance(rows, list):
            for x in rows:
                sx = str(x or "")
                if sx:
                    out.add(sx)
    parts = inst.get("streamParticipants") or []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                sx = str(part.get("groupId") or "")
                if sx:
                    out.add(sx)
    return out


def _schedule_group_slice(schedule, group_id):
    schedule = schedule if isinstance(schedule, dict) else {}
    group_id = str(group_id or "")
    rows = [dict(x) for x in (schedule.get("instances") or [])
            if isinstance(x, dict) and group_id in _schedule_instance_group_ids(x)]
    ids = {str(x.get("instId") or "") for x in rows if str(x.get("instId") or "")}
    assignment = schedule.get("assignment") or {}
    return {
        "instances": rows,
        "assignment": {str(k): v for k, v in assignment.items() if str(k) in ids},
        "locked": [str(x) for x in (schedule.get("locked") or []) if str(x) in ids],
        "unplaced": [str(x) for x in (schedule.get("unplaced") or []) if str(x) in ids],
    }


def _merge_id_list_three_way(base, local, remote):
    b = {str(x) for x in (base or []) if str(x)}
    l = {str(x) for x in (local or []) if str(x)}
    r = {str(x) for x in (remote or []) if str(x)}
    # Apply independent additions/removals from both editors to the common base.
    removed = (b - l) | (b - r)
    added = (l - b) | (r - b)
    return sorted((b - removed) | added)


def _replace_group_schedule_slice(remote_schedule, merged_slice, group_id, known_ids=None):
    remote_schedule = dict(remote_schedule or {})
    known_ids = {str(x) for x in (known_ids or set()) if str(x)}
    remote_group_rows = [x for x in (remote_schedule.get("instances") or [])
                         if isinstance(x, dict) and str(group_id) in _schedule_instance_group_ids(x)]
    remote_group_ids = {str(x.get("instId") or "") for x in remote_group_rows if str(x.get("instId") or "")}
    merged_rows = [dict(x) for x in (merged_slice.get("instances") or []) if isinstance(x, dict)]
    merged_ids = {str(x.get("instId") or "") for x in merged_rows if str(x.get("instId") or "")}
    owned_ids = remote_group_ids | merged_ids | known_ids

    untouched_rows = [dict(x) for x in (remote_schedule.get("instances") or [])
                      if isinstance(x, dict) and str(x.get("instId") or "") not in owned_ids]
    remote_schedule["instances"] = untouched_rows + merged_rows

    assignment = dict(remote_schedule.get("assignment") or {})
    for sid in owned_ids:
        assignment.pop(sid, None)
    assignment.update({str(k): v for k, v in (merged_slice.get("assignment") or {}).items()})
    remote_schedule["assignment"] = assignment

    def replace_ids(existing, replacement):
        kept = [str(x) for x in (existing or []) if str(x) not in owned_ids]
        seen = set(kept)
        for x in (replacement or []):
            sx = str(x)
            if sx and sx not in seen:
                kept.append(sx)
                seen.add(sx)
        return kept

    remote_schedule["locked"] = replace_ids(remote_schedule.get("locked") or [], merged_slice.get("locked") or [])
    remote_schedule["unplaced"] = replace_ids(remote_schedule.get("unplaced") or [], merged_slice.get("unplaced") or [])
    return remote_schedule


def _rows_for_group(rows, group_id):
    return [dict(x) for x in (rows or []) if isinstance(x, dict) and str(x.get("groupId") or "") == str(group_id)]


def _replace_rows_for_group(remote_rows, merged_rows, group_id):
    keep = [dict(x) for x in (remote_rows or []) if isinstance(x, dict) and str(x.get("groupId") or "") != str(group_id)]
    return keep + [dict(x) for x in (merged_rows or []) if isinstance(x, dict)]


@app.post("/api/storage-publication-commit/<key>")
def storage_publication_commit(key):
    """Atomically persist one publication and its restore point.

    Publication metadata is top-level project state and must not depend on the
    group-scoped timetable autosave. This endpoint appends scheduleVersions and
    changeHistory while replacing only the explicit public snapshot/visibility
    fields. It never replaces the working schedule; for single-week publication
    it may only add lock ids supplied by the client.
    """
    body = request.get_json(force=True, silent=True) or {}
    version = body.get("version") or {}
    snapshot = body.get("publishedSnapshot")
    weeks_raw = body.get("publishedWeeks") or []
    history_entry = body.get("historyEntry") or {}
    locked_add = body.get("lockedAdd") or []
    publish_change = body.get("publishChange") or {}
    meta = body.get("meta") or {}
    if not isinstance(version, dict) or not version.get("id") or not isinstance(snapshot, dict):
        return jsonify({"error": "Неверные данные публикации"}), 400
    if not isinstance(weeks_raw, list) or not isinstance(locked_add, list):
        return jsonify({"error": "Неверный формат публикации"}), 400

    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            project = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(project, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        total = _total_semester_weeks_py(project.get("config") or {})
        cleaned = []
        for value in weeks_raw:
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if n > 0 and (total <= 0 or n <= total):
                cleaned.append(n)
        cleaned = sorted(set(cleaned))

        versions = [x for x in (project.get("scheduleVersions") or []) if isinstance(x, dict) and str(x.get("id") or "") != str(version.get("id") or "")]
        versions.append(version)
        project["scheduleVersions"] = versions[-40:]
        project["publishedSnapshot"] = snapshot
        project["publishedWeeks"] = cleaned
        project["publicWeekSelectionEnabled"] = bool(body.get("publicWeekSelectionEnabled"))
        project["publishedAt"] = str(body.get("publishedAt") or version.get("at") or datetime.utcnow().isoformat(timespec="milliseconds") + "Z")
        project["publishedBy"] = str(body.get("publishedBy") or version.get("by") or meta.get("by") or "Сотрудник")

        if isinstance(history_entry, dict) and history_entry.get("id"):
            hist = [x for x in (project.get("changeHistory") or []) if isinstance(x, dict) and str(x.get("id") or "") != str(history_entry.get("id") or "")]
            hist.append(history_entry)
            project["changeHistory"] = hist[-1000:]

        if locked_add and isinstance(project.get("schedule"), dict):
            sched = dict(project.get("schedule") or {})
            sched["locked"] = list(dict.fromkeys([str(x) for x in (sched.get("locked") or []) if str(x)] + [str(x) for x in locked_add if str(x)]))
            project["schedule"] = sched

        at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        save_id = "publication-" + uuid.uuid4().hex
        project["_syncMeta"] = {
            "at": at, "saveId": save_id, "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or project.get("publishedBy") or "Сотрудник"),
            "scope": "publication", "sessionId": str(meta.get("sessionId") or ""),
        }
        store[key] = project
        _maybe_periodic_backup()
        # v1704: do not expose the new public index/bootstrap before the matching
        # shard snapshot is completely built. Otherwise a public client can see
        # new publication metadata while /api/public-schedule still points at the
        # previous snapshot. The public files are switched only after shard build.
        save_store(store, backup=False, refresh_public=False)
        public_build_project = copy.deepcopy(project)

    try:
        marker = _public_marker(public_build_project)
        _prebuild_public_snapshot_shards(public_build_project, marker, publish_change)
        public_store = {"schedule-data-v2": public_build_project}
        _refresh_public_index_from_store(public_store, force=True)
        _refresh_public_sidecar_from_store(public_store, force=True)
    except Exception as exc:
        app.logger.warning("public snapshot build failed after publication: %s", exc)

    return jsonify({"ok": True, "token": save_id, "versionId": version.get("id"), "publishedWeeks": cleaned})


@app.post("/api/storage-publish-weeks/<key>")
def storage_publish_weeks(key):
    """Atomically replace the exact set of weeks visible in the public contour.

    This is deliberately independent from schedule/group autosave: publication is
    top-level metadata, while group-scoped timetable saves intentionally carry only
    one group's working set. storage.json remains the single source of truth.
    """
    body = request.get_json(force=True, silent=True) or {}
    weeks_raw = body.get("weeks") or []
    meta = body.get("meta") or {}
    if not isinstance(weeks_raw, list):
        return jsonify({"error": "Неверный список недель"}), 400

    with _lock:
        if not os.path.exists(DATA_FILE):
            return jsonify({"error": "Хранилище отсутствует"}), 409
        store = load_store()
        raw = store.get(key)
        if raw is None:
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        try:
            project = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            return jsonify({"error": f"Не удалось прочитать рабочие данные: {exc}"}), 422
        if not isinstance(project, dict):
            return jsonify({"error": "Рабочие данные имеют неверный формат"}), 422

        total = _total_semester_weeks_py(project.get("config") or {})
        cleaned = []
        for value in weeks_raw:
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if n > 0 and (total <= 0 or n <= total):
                cleaned.append(n)
        cleaned = sorted(set(cleaned))
        if not cleaned:
            return jsonify({"error": "Нужно выбрать хотя бы одну неделю"}), 400

        at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        save_id = "publish-weeks-" + uuid.uuid4().hex
        project["publishedWeeks"] = cleaned
        project["publicWeekSelectionEnabled"] = True
        project["publishedAt"] = at
        project["publishedBy"] = str(meta.get("by") or "Сотрудник")
        project["_syncMeta"] = {
            "at": at,
            "saveId": save_id,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Сотрудник"),
            "scope": "publish-weeks",
            "sessionId": str(meta.get("sessionId") or ""),
        }
        store[key] = project
        _maybe_periodic_backup()
        # v1704: same atomic public switch for week-selection publication.
        save_store(store, backup=False, refresh_public=False)
        public_build_project = copy.deepcopy(project)

    try:
        marker = _public_marker(public_build_project)
        _prebuild_public_snapshot_shards(public_build_project, marker, {"scope": "weeks", "weekNumbers": cleaned})
        public_store = {"schedule-data-v2": public_build_project}
        _refresh_public_index_from_store(public_store, force=True)
        _refresh_public_sidecar_from_store(public_store, force=True)
    except Exception as exc:
        app.logger.warning("public snapshot build failed after publish-weeks: %s", exc)

    return jsonify({
        "ok": True,
        "weeks": cleaned,
        "token": save_id,
        "publishedAt": at,
        "publishedBy": project.get("publishedBy"),
    })


@app.post("/api/storage-schedule-group-merge/<key>")
def storage_schedule_group_merge(key):
    """Merge only one group's timetable working set.

    `storage.json` remains the only source of truth.  The browser may render a
    dedicated /admin/schedule/group/<id> page, but autosave from that page is
    not allowed to carry stale assignments of unrelated groups back to the
    server. Shared stream instances are included because they explicitly name
    this group; concurrent edits of the same shared instance still go through
    the normal three-way conflict rules.
    """
    body = request.get_json(force=True, silent=True) or {}
    group_id = str(body.get("groupId") or "").strip()
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not group_id:
        return jsonify({"error": "Не указана группа для сохранения"}), 400
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных расписания группы"}), 400

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

        conflicts = []
        base_schedule = base.get("schedule") or {}
        local_schedule = local.get("schedule") or {}
        remote_schedule = remote.get("schedule") or {}
        b_slice = _schedule_group_slice(base_schedule, group_id)
        l_slice = _schedule_group_slice(local_schedule, group_id)
        r_slice = _schedule_group_slice(remote_schedule, group_id)
        merged_slice = _merge_three_way_no_clobber(b_slice, l_slice, r_slice, ("schedule", f"group={group_id}"), conflicts)
        # ID lists are sets semantically. The generic three-way merge treats opaque
        # arrays conservatively, which would make two colleagues editing different
        # lessons of the same group fight over `locked`/`unplaced`. Merge those two
        # fields as independent set deltas instead.
        merged_slice["locked"] = _merge_id_list_three_way(b_slice.get("locked"), l_slice.get("locked"), r_slice.get("locked"))
        merged_slice["unplaced"] = _merge_id_list_three_way(b_slice.get("unplaced"), l_slice.get("unplaced"), r_slice.get("unplaced"))

        # v1651: group autosave is a BROAD SNAPSHOT and is never allowed to
        # delete a timetable card or its placement. Deletion/unplacement/move is
        # committed only by the explicit atomic /api/storage-schedule-patch path.
        #
        # This is stronger than the v1646 stream-only guard: after a deploy, live
        # refresh or a stale/partially hydrated group page, *ordinary* group rows
        # could also be missing from the browser snapshot. Treating that absence as
        # a deletion caused already placed lessons to disappear from canonical
        # storage.json. The current server copy therefore wins for anything absent
        # from the broad snapshot.
        local_slice_ids = {
            str(x.get("instId") or "") for x in (l_slice.get("instances") or [])
            if isinstance(x, dict) and str(x.get("instId") or "")
        }
        merged_rows_by_id = {
            str(x.get("instId") or ""): dict(x) for x in (merged_slice.get("instances") or [])
            if isinstance(x, dict) and str(x.get("instId") or "")
        }
        merged_assignment = dict(merged_slice.get("assignment") or {})
        merged_locked = {str(x) for x in (merged_slice.get("locked") or []) if str(x)}
        merged_unplaced = {str(x) for x in (merged_slice.get("unplaced") or []) if str(x)}
        remote_assignment = remote_schedule.get("assignment") or {}
        remote_locked = {str(x) for x in (remote_schedule.get("locked") or []) if str(x)}
        remote_unplaced = {str(x) for x in (remote_schedule.get("unplaced") or []) if str(x)}

        for row in (r_slice.get("instances") or []):
            if not isinstance(row, dict):
                continue
            sid = str(row.get("instId") or "")
            if not sid:
                continue

            # A remote row missing from this browser's group snapshot must survive.
            if sid not in local_slice_ids:
                merged_rows_by_id[sid] = dict(row)

            # A broad autosave may never clear a placement/lock merely because its
            # local snapshot is stale. Explicit schedule-patch has already changed
            # the current remote state before this code runs, so preserving *current*
            # remote here does not undo an intentional atomic edit.
            if sid in remote_assignment and sid not in merged_assignment:
                merged_assignment[sid] = remote_assignment[sid]
            if sid in remote_locked:
                merged_locked.add(sid)
            if sid in remote_assignment:
                merged_unplaced.discard(sid)
            elif sid in remote_unplaced and sid not in merged_assignment:
                merged_unplaced.add(sid)

        merged_slice["instances"] = list(merged_rows_by_id.values())
        merged_slice["assignment"] = merged_assignment
        merged_slice["locked"] = sorted(merged_locked)
        merged_slice["unplaced"] = sorted(x for x in merged_unplaced if x not in merged_assignment)

        known_ids = {
            str(x.get("instId") or "")
            for src in (b_slice.get("instances") or [], l_slice.get("instances") or [], r_slice.get("instances") or [])
            for x in [src]
            if isinstance(x, dict) and str(x.get("instId") or "")
        }
        remote["schedule"] = _replace_group_schedule_slice(remote_schedule, merged_slice, group_id, known_ids)
        # v1666: a stale/versioned broad group save must not create stacked
        # lessons. Preserve the already-canonical placement and unplace the
        # newly merged conflicting one instead.
        remote["schedule"] = _sanitize_merged_schedule_overlaps(remote, remote_schedule, remote["schedule"])

        # Group-only administrative blocks are merged at row granularity too.
        for field in ("groupDayBlocks", "groupSlotBlocks", "groupDayFreezes", "groupScheduleFreezes"):
            b_rows = _rows_for_group(base.get(field) or [], group_id)
            l_rows = _rows_for_group(local.get(field) or [], group_id)
            r_rows = _rows_for_group(remote.get(field) or [], group_id)
            merged_rows = _merge_three_way_no_clobber(b_rows, l_rows, r_rows, (field, f"group={group_id}"), conflicts)
            remote[field] = _replace_rows_for_group(remote.get(field) or [], merged_rows, group_id)

        # Loads can be touched by timetable-only synchronization. Never send or
        # merge another group's rows from a dedicated group page.
        b_loads = _rows_for_group(base.get("loads") or [], group_id)
        l_loads = _rows_for_group(local.get("loads") or [], group_id)
        r_loads = _rows_for_group(remote.get("loads") or [], group_id)
        merged_loads = _merge_three_way_no_clobber(b_loads, l_loads, r_loads, ("loads", f"group={group_id}"), conflicts)
        remote["loads"] = _replace_rows_for_group(remote.get("loads") or [], merged_loads, group_id)

        # History is append/identity merged globally, but it is small and does
        # not carry timetable state of unrelated groups.
        if "changeHistory" in local:
            remote["changeHistory"] = _merge_three_way_no_clobber(
                base.get("changeHistory") or [], local.get("changeHistory") or [], remote.get("changeHistory") or [],
                ("changeHistory",), conflicts)

        sync_meta = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-group-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "schedule-group",
            "groupId": group_id,
            "sessionId": str(meta.get("sessionId") or ""),
            "requestId": str(meta.get("requestId") or ""),
        }
        remote["_syncMeta"] = sync_meta
        store[key] = remote
        _maybe_periodic_backup()
        save_store(store, backup=False)

    # v1676: on a dedicated group page the browser already has the rest of the
    # project. Returning it again after every autosave was often several MB and
    # made the UI sit in "saving" even after the disk commit had finished. Send
    # only the confirmed group slice; the client patches it into its server base.
    if bool(meta.get("deltaOnly")):
        group_value = {
            "schedule": _schedule_group_slice(remote.get("schedule") or {}, group_id),
            "loads": _rows_for_group(remote.get("loads") or [], group_id),
            "changeHistory": (remote.get("changeHistory") or [])[-200:],
            "_syncMeta": sync_meta,
        }
        for field in ("groupDayBlocks", "groupSlotBlocks", "groupDayFreezes", "groupScheduleFreezes"):
            group_value[field] = _rows_for_group(remote.get(field) or [], group_id)
        return jsonify({
            "ok": True,
            "groupValue": group_value,
            "token": sync_meta["saveId"],
            "conflicts": conflicts[:100],
            "hadConflicts": bool(conflicts),
            "_syncMeta": sync_meta,
        })

    return jsonify({
        "ok": True,
        "value": _editor_compact_project(remote) if bool(meta.get("compact")) else remote,
        "token": sync_meta["saveId"],
        "conflicts": conflicts[:100],
        "hadConflicts": bool(conflicts),
        "_syncMeta": sync_meta,
    })


@app.post("/api/storage-schedule-merge/<key>")
def storage_schedule_merge(key):
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных расписания"}), 400
    _protect_manual_schedule_ids(_manual_schedule_touched_ids(base.get("schedule"), local.get("schedule")))
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
        merge_conflicts = []
        for section in (
            "schedule", "loads", "groupDayBlocks", "groupSlotBlocks",
            "groupDayFreezes", "changeHistory"
        ):
            if section in local:
                merged[section] = _merge_three_way_no_clobber(
                    base.get(section), local.get(section), remote.get(section),
                    path=(section,), conflicts=merge_conflicts
                )
        if isinstance(merged.get("schedule"), dict):
            merged["schedule"] = _sanitize_merged_schedule_overlaps(merged, remote.get("schedule") or {}, merged.get("schedule") or {})
        merged["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "schedule",
        }
        encoded = json.dumps(merged, ensure_ascii=False)
        store[key] = merged
        _maybe_periodic_backup()
        save_store(store, backup=False)
    return jsonify({
        "ok": True,
        "token": str(merged["_syncMeta"]["saveId"]),
        "value": _editor_compact_project(merged) if bool(meta.get("compact")) else merged,
        "conflicts": sorted(set(merge_conflicts))[:200],
        "hadConflicts": bool(merge_conflicts),
    })


def _request_admin_user():
    """Resolve an admin/superadmin from the canonical store for sensitive recovery endpoints."""
    user_id = str(request.headers.get("X-Admin-User-Id") or "").strip()
    if not user_id:
        return None
    try:
        store = load_store()
        raw = store.get("schedule-data-v2")
        data = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
        for user in (data.get("users") or []):
            if str(user.get("id") or "") == user_id and str(user.get("role") or "") in {"admin", "superadmin"}:
                return user
    except Exception:
        return None
    return None


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


@app.get("/api/storage-backups/<name>/download")
def storage_backup_download(name):
    # v1652: recovery download bypasses Amvera's broken file-storage download link,
    # but is still restricted to a canonical admin user and storage_*.json files.
    if _request_admin_user() is None:
        return jsonify({"error": "admin authorization required"}), 403
    if "/" in name or "\\" in name or not name.startswith("storage_") or not name.endswith(".json"):
        return jsonify({"error": "invalid backup name"}), 400
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    try:
        # Refuse to download a truncated/corrupt JSON backup by mistake.
        with open(path, "r", encoding="utf-8") as f:
            candidate = json.load(f)
        if not _is_valid_working_value(candidate.get("schedule-data-v2")):
            return jsonify({"error": "backup does not contain valid working data"}), 422
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"backup is invalid: {exc}"}), 422
    return send_from_directory(BACKUP_DIR, name, as_attachment=True, download_name=name, mimetype="application/json")


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
        _invalidate_store_cache()  # v1670: written outside save_store(), cache would otherwise go stale.
    return jsonify({"ok": True, "restored": name})


# v1681: hashed Vite assets are immutable. With many public/admin users, let
# browsers/proxies keep them instead of asking Flask for the same JS/CSS again.
@app.after_request
def _performance_cache_headers(response):
    path = request.path or ""
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.startswith("/admin") or path.startswith("/group/") or path.startswith("/teacher/") or path.startswith("/specialty/"):
        # HTML shell must notice a new deploy, but avoid legacy proxy caching.
        response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


# v1663: SPA navigation must survive a hard refresh / browser Back/Forward.
# Explicit client routes are registered in addition to the generic fallback.
# This is deliberately separate from /api/* so a missing API endpoint still
# returns a real 404 instead of HTML.
def _serve_spa_index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.get("/")
@app.get("/group/<path:route_id>")
@app.get("/teacher/<path:route_id>")
@app.get("/admin")
@app.get("/admin/<path:spa_path>")
@app.get("/specialty/<path:route_id>")
def serve_spa_routes(route_id=None, spa_path=None):
    return _serve_spa_index()

# Отдаём существующие файлы сборки и используем index.html как SPA fallback
# для прочих GET/HEAD URL.
@app.route("/<path:path>", methods=["GET", "HEAD"])
def serve(path):
    full = os.path.join(STATIC_DIR, path)
    if path and os.path.exists(full) and os.path.isfile(full):
        return send_from_directory(STATIC_DIR, path)
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    return _serve_spa_index()

@app.errorhandler(404)
def spa_404_fallback(error):
    # If an upstream Flask rule produced a 404 for a browser page request,
    # recover into React. API/file requests must keep their real 404 status.
    if request.method in ("GET", "HEAD") and not request.path.startswith("/api/"):
        accept = request.headers.get("Accept", "")
        if "text/html" in accept or accept in ("", "*/*"):
            try:
                return _serve_spa_index()
            except Exception:
                pass
    return error


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
