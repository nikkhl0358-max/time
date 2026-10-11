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
import sqlite3
import re
from collections import OrderedDict
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response

# В контейнере путь задаётся явно через DATA_DIR=/data. Локально без переменной
# сохраняем данные рядом с приложением.
DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if "AMVERA" in os.environ else os.path.join(os.path.dirname(__file__), "data"))
LEGACY_DATA_FILE = os.path.join(DATA_DIR, "storage.json")
SQLITE_PATH = os.environ.get("SQLITE_PATH") or os.path.join(DATA_DIR, "raspisanie.db")
# Compatibility alias for old diagnostics/comments. SQLite is canonical from v1711.
DATA_FILE = LEGACY_DATA_FILE
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
MAX_BACKUPS = int(os.environ.get("MAX_BACKUPS", "30"))

_lock = threading.Lock()
_sqlite_init_lock = threading.Lock()
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

# v1777: idempotency registry for schedule-cell requests. A network retry keeps
# the same requestId; the second HTTP request waits for the original commit
# instead of queueing the same edit again and amplifying server load.
_schedule_patch_request_lock = threading.Lock()
_schedule_patch_requests = OrderedDict()
SCHEDULE_PATCH_REQUEST_TTL_SECONDS = float(os.environ.get("SCHEDULE_PATCH_REQUEST_TTL_SECONDS", "300"))

# v1780: graph autosaves are also idempotent by requestId. A transient gateway
# response must not cause the same weeklyPairs edit to rewrite the whole project
# several times. The cache is process-local and only suppresses exact retries.
_graph_patch_results = OrderedDict()
GRAPH_PATCH_REQUEST_TTL_SECONDS = float(os.environ.get("GRAPH_PATCH_REQUEST_TTL_SECONDS", "300"))

SCHEDULE_PATCH_BATCH_WINDOW_SECONDS = float(os.environ.get("SCHEDULE_PATCH_BATCH_WINDOW_SECONDS", "0.025"))
SCHEDULE_PATCH_BATCH_MAX = int(os.environ.get("SCHEDULE_PATCH_BATCH_MAX", "64"))

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
GENERATION_CLIENT_LEASE_SECONDS = int(os.environ.get("GENERATION_CLIENT_LEASE_SECONDS", "20"))
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
# v1761: tiny independent switch for temporarily suspending the public contour.
# It deliberately lives outside schedule-data-v2 so toggling it never rewrites the
# multi-megabyte project and cannot contend with timetable cell saves.
PUBLIC_PAUSE_FILE = os.path.join(DATA_DIR, "public-pause.json")
_public_pause_lock = threading.Lock()

def _public_pause_state():
    try:
        with _public_pause_lock:
            if not os.path.exists(PUBLIC_PAUSE_FILE):
                return {"paused": False, "at": "", "by": ""}
            with open(PUBLIC_PAUSE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        return {
            "paused": data.get("paused") is True,
            "at": str(data.get("at") or ""),
            "by": str(data.get("by") or ""),
        }
    except Exception:
        return {"paused": False, "at": "", "by": ""}

def _write_public_pause_state(paused, by=""):
    state = {
        "paused": bool(paused),
        "at": datetime.now(timezone.utc).isoformat(),
        "by": str(by or ""),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PUBLIC_PAUSE_FILE + ".tmp"
    with _public_pause_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PUBLIC_PAUSE_FILE)
    return state

def _public_paused_payload():
    state = _public_pause_state()
    return {
        **state,
        "message": "Публичное расписание временно приостановлено на время работы администраторов. Обновите страницу позже.",
    }
PUBLIC_SHARD_DIR = os.path.join(DATA_DIR, "public-shards")
PUBLIC_SHARD_SNAPSHOT_FILE = os.path.join(DATA_DIR, "public-shards.current.json")
_public_index_cache_lock = threading.Lock()
_public_index_cache = {"key": None, "plain": None, "gzip": None, "etag": None, "project": None}
# v1613: publication and lazy shard generation must not switch/read the global
# manifest concurrently inside the gthread worker.
_public_activation_lock = threading.Lock()
_public_shard_cache_lock = threading.Lock()
_public_shard_cache = OrderedDict()
_public_status_cache = None
PUBLIC_SHARD_CACHE_MAX = int(os.environ.get("PUBLIC_SHARD_CACHE_MAX", "16"))
# v1870: shard schema is independent from the publication marker. A deploy that
# fixes shard selection must be able to rebuild lazy public files from the SAME
# publishedSnapshot without forcing staff to republish the timetable.
PUBLIC_SHARD_SCHEMA_VERSION = 1894
PUBLIC_INDEX_SCHEMA_VERSION = 1871


def _public_project_from_store(store):
    raw = (store or {}).get("schedule-data-v2") if isinstance(store, dict) else None
    try:
        project = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        project = None
    return project if isinstance(project, dict) else None

def _public_visible_schedule_count(snapshot):
    """Count placed public lessons without expanding recurring weeks.

    Zero is the important signal here: a college timetable with a populated
    working schedule must never silently publish an all-empty public contour.
    """
    if not isinstance(snapshot, dict):
        return 0
    schedule = snapshot.get("schedule") if isinstance(snapshot.get("schedule"), dict) else {}
    instances = schedule.get("instances") if isinstance(schedule.get("instances"), list) else []
    assignment = schedule.get("assignment") if isinstance(schedule.get("assignment"), dict) else {}
    count = 0
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        iid = str(inst.get("instId") or "")
        a = assignment.get(iid) if iid else None
        if isinstance(a, dict) and a.get("day") is not None and a.get("period") is not None:
            count += 1
    return count


def _latest_nonempty_publication_archive(store_key="schedule-data-v2", limit=80):
    """Return the newest immutable publication that actually contains lessons."""
    try:
        conn = _sqlite_connect()
        try:
            rows = conn.execute(
                """SELECT archive_id,published_at,published_by,snapshot_json
                   FROM schedule_publication_archive
                   WHERE store_key=?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (str(store_key), int(limit)),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    for row in rows:
        try:
            snapshot = json.loads(row[3])
        except Exception:
            continue
        if _public_visible_schedule_count(snapshot) > 0:
            return {
                "archiveId": str(row[0] or ""),
                "publishedAt": str(row[1] or ""),
                "publishedBy": str(row[2] or ""),
                "snapshot": snapshot,
            }
    return None


def _public_source_project(project):
    """Stable heavy source for the public contour.

    v1871 emergency guard: if the canonical publishedSnapshot contains ZERO
    placed lessons while the working timetable is populated, do not fan that
    empty snapshot out to every group and teacher. Serve the newest non-empty
    immutable publication archive instead. This is read-only recovery: the
    working project and publishedSnapshot are not modified.
    """
    published = project.get("publishedSnapshot")
    recovery = None
    published_visible = _public_visible_schedule_count(published)
    working_visible = _public_visible_schedule_count(project)
    if published_visible == 0 and working_visible > 0:
        recovery = _latest_nonempty_publication_archive("schedule-data-v2")
        if recovery and isinstance(recovery.get("snapshot"), dict):
            published = recovery["snapshot"]

    source = dict(published) if isinstance(published, dict) else dict(project)
    # v1892: old group publications may have persisted only the raw primary row
    # of an implicit stream even though the editor rendered all linked groups.
    # Repair only linkage already declared by the published primary load.
    if isinstance(published, dict):
        source = _repair_public_implicit_stream_membership(source, project)
    for key in (
        "publishedWeeks", "publicWeekSelectionEnabled", "publishedAt", "publishedBy",
        "publicIntroText", "announcements", "consultations",
    ):
        # When recovering, week visibility belongs to the recovered publication.
        # Overlaying the broken publication's week filter can hide the recovered
        # lessons again. Cosmetic/current metadata may still come from working data.
        if recovery and key in {"publishedWeeks", "publicWeekSelectionEnabled"}:
            continue
        if key in project:
            source[key] = project.get(key)
    if recovery:
        source["publicRecovery"] = {
            "active": True,
            "archiveId": recovery.get("archiveId") or "",
            "publishedAt": recovery.get("publishedAt") or "",
            "publishedBy": recovery.get("publishedBy") or "",
            "reason": "empty-published-snapshot",
        }
    return source



def _repair_public_implicit_stream_membership(source, working):
    """Backfill stream participants that were visible in the editor but lost in
    an older group-scoped publishedSnapshot.

    v1892 fixes a legacy asymmetry: the editor can reconstruct a stream from
    load-level links (remoteSubgroupLoadIds / streamId), while the published
    snapshot may contain only the primary raw schedule row.  Derived public
    shards then see only that primary group.  This compatibility repair is
    intentionally conservative: it expands only when the *published primary
    load row itself* contains linkage metadata.  Placement/weeks/teachers are
    not taken from the working timetable, and transfer-split occurrences are
    never re-joined.
    """
    if not isinstance(source, dict) or not isinstance(working, dict):
        return source
    published_loads = [x for x in (source.get("loads") or []) if isinstance(x, dict)]
    working_loads = [x for x in (working.get("loads") or []) if isinstance(x, dict)]
    if not published_loads or not working_loads:
        return source

    pub_by_id = {
        str(row.get("id") or row.get("loadId") or ""): row
        for row in published_loads
        if str(row.get("id") or row.get("loadId") or "")
    }
    work_by_id = {
        str(row.get("id") or row.get("loadId") or ""): row
        for row in working_loads
        if str(row.get("id") or row.get("loadId") or "")
    }

    def row_id(row):
        return str((row or {}).get("id") or (row or {}).get("loadId") or "")

    def remote_capable(row):
        if not isinstance(row, dict):
            return False
        delivery = str(row.get("format") or "inperson")
        if delivery == "remote":
            return True
        if delivery != "auto" or not bool(row.get("canBeRemote")):
            return False
        try:
            pct = float(row.get("remotePercent") or 0)
        except Exception:
            pct = 0
        try:
            assemblies = float(row.get("mandatoryRemoteAssemblies") or 0)
        except Exception:
            assemblies = 0
        return pct > 0 or assemblies > 0

    def compatible(row, primary, require_subgroup=False):
        if not isinstance(row, dict) or not isinstance(primary, dict):
            return False
        same = (
            str(row.get("subjectId") or "") == str(primary.get("subjectId") or "") and
            str(row.get("teacherId") or "") == str(primary.get("teacherId") or "") and
            str(row.get("typeId") or "") == str(primary.get("typeId") or "")
        )
        if require_subgroup:
            try:
                same = same and int(row.get("subgroup") or 0) == int(primary.get("subgroup") or 0)
            except Exception:
                return False
        return same

    def linked_rows(primary):
        """Resolve only linkage already declared by the published primary row."""
        pid = row_id(primary)
        if not pid:
            return [primary]

        # Remote cluster: follow refs starting from refs carried by the published
        # primary row. Once inside that declared cluster, reciprocal refs may be
        # followed to keep old asymmetric link storage usable.
        refs = [str(x) for x in (primary.get("remoteSubgroupLoadIds") or []) if str(x)]
        if refs and remote_capable(primary):
            reverse = {}
            for row in working_loads:
                rid = row_id(row)
                if not rid:
                    continue
                for ref in (row.get("remoteSubgroupLoadIds") or []):
                    ref = str(ref or "")
                    if ref:
                        reverse.setdefault(ref, []).append(rid)
            seen = set()
            resolved_ids = []
            queue = [pid] + refs
            while queue:
                lid = str(queue.pop(0) or "")
                if not lid or lid in seen:
                    continue
                row = work_by_id.get(lid) or pub_by_id.get(lid)
                if not row or not remote_capable(row) or not compatible(row, primary):
                    continue
                seen.add(lid)
                resolved_ids.append(lid)
                for ref in (row.get("remoteSubgroupLoadIds") or []):
                    ref = str(ref or "")
                    if ref and ref not in seen:
                        queue.append(ref)
                for source_id in reverse.get(lid, []):
                    if source_id not in seen:
                        queue.append(source_id)
            rows = [work_by_id.get(lid) or pub_by_id.get(lid) for lid in resolved_ids]
            rows = [row for row in rows if isinstance(row, dict)]
            if len(rows) > 1:
                return rows

        # Explicit in-person stream id on the published load is also safe to
        # resolve: the publication already declared that this row belongs to a
        # named stream, it merely omitted the peer load rows.
        stream_id = str(primary.get("streamId") or "")
        if stream_id:
            rows = [
                row for row in working_loads
                if str(row.get("streamId") or "") == stream_id and compatible(row, primary)
            ]
            if len(rows) > 1:
                return rows

        # Legacy streamGroupIds on the published load.
        peer_groups = {str(x) for x in (primary.get("streamGroupIds") or []) if str(x)}
        if peer_groups:
            peer_groups.add(str(primary.get("groupId") or ""))
            rows = [
                row for row in working_loads
                if str(row.get("groupId") or "") in peer_groups and compatible(row, primary, require_subgroup=True)
            ]
            if len(rows) > 1:
                return rows
        return [primary]

    schedule = source.get("schedule") if isinstance(source.get("schedule"), dict) else None
    if not schedule or not isinstance(schedule.get("instances"), list):
        return source

    repaired_instances = []
    add_load_ids = set()
    changed = False
    for inst in schedule.get("instances") or []:
        if not isinstance(inst, dict):
            repaired_instances.append(inst)
            continue
        transfer_scoped = bool(
            inst.get("manualStreamBreak") or inst.get("transferSubgroupDetached") or
            inst.get("transferSubgroupRetained") or inst.get("transferredLoadRemainder")
        )
        explicit_members = _instance_group_ids(inst)
        # v1893: occurrence-level scope is authoritative even when it is
        # explicitly EMPTY. A normal standalone lesson may belong to a load row
        # that still carries streamId/streamGroupIds for other occurrences.
        # v1892 ignored the empty arrays and rebuilt such standalone practices as
        # a fake stream in the public contour. Only truly legacy occurrences
        # where BOTH scope fields are absent may be recovered from load metadata.
        has_explicit_occurrence_scope = (
            isinstance(inst.get("streamParticipants"), list) and
            isinstance(inst.get("streamGroupIds"), list)
        )
        # If publication already contains an occurrence-level decision (stream or
        # explicitly not a stream), or a transfer split, never override it.
        if transfer_scoped or has_explicit_occurrence_scope or len(set(explicit_members)) > 1:
            repaired_instances.append(inst)
            continue
        lid = str(inst.get("loadId") or "")
        primary_published = pub_by_id.get(lid)
        if not primary_published:
            repaired_instances.append(inst)
            continue
        rows = linked_rows(primary_published)
        groups = []
        unique_rows = []
        seen_rows = set()
        for row in rows:
            rid = row_id(row)
            gid = str(row.get("groupId") or "")
            if not rid or not gid or rid in seen_rows:
                continue
            seen_rows.add(rid)
            unique_rows.append(row)
            groups.append(gid)
        groups = list(dict.fromkeys(groups))
        if len(groups) <= 1:
            repaired_instances.append(inst)
            continue

        primary_gid = str(inst.get("groupId") or primary_published.get("groupId") or "")
        primary_lid = lid or row_id(primary_published)
        peers = []
        for row in unique_rows:
            rid = row_id(row)
            gid = str(row.get("groupId") or "")
            if rid == primary_lid and gid == primary_gid:
                continue
            peers.append({
                "groupId": gid,
                "subgroup": int(row.get("subgroup") or 0),
                "loadId": rid,
            })
            add_load_ids.add(rid)
        if not peers:
            repaired_instances.append(inst)
            continue
        out = dict(inst)
        out["groupIds"] = groups
        out["streamGroupIds"] = [gid for gid in groups if gid != primary_gid]
        out["streamParticipants"] = peers
        out["_publicStreamMembershipRecovered"] = True
        repaired_instances.append(out)
        add_load_ids.add(primary_lid)
        changed = True

    if not changed:
        return source

    existing_load_ids = {row_id(row) for row in published_loads}
    repaired_loads = list(published_loads)
    for lid in add_load_ids:
        if lid and lid not in existing_load_ids and lid in work_by_id:
            repaired_loads.append(work_by_id[lid])
            existing_load_ids.add(lid)

    out_source = dict(source)
    out_schedule = dict(schedule)
    out_schedule["instances"] = repaired_instances
    out_source["schedule"] = out_schedule
    out_source["loads"] = repaired_loads
    return out_source


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
        "retakes", "interimAssessments", "debtLiquidations", "attestation", "consultations", "announcements",
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
    if isinstance(source.get("publicRecovery"), dict):
        public_data["publicRecovery"] = source.get("publicRecovery")
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
        "lessonTypes", "rooms", "roomTypes", "roomSizes", "practices", "retakes", "interimAssessments", "debtLiquidations",
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
    if isinstance(source.get("publicRecovery"), dict):
        public_data["publicRecovery"] = source.get("publicRecovery")
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
            if meta.get("marker") == marker and int(meta.get("schemaVersion") or 0) == PUBLIC_INDEX_SCHEMA_VERSION:
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
    meta_plain = json.dumps({"marker": marker, "etag": etag, "size": len(plain), "gzipSize": len(gz), "schemaVersion": PUBLIC_INDEX_SCHEMA_VERSION}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(PUBLIC_INDEX_META_FILE, meta_plain)
    with _public_index_cache_lock:
        _public_index_cache.update({"key": marker, "plain": plain, "gzip": gz, "etag": etag, "project": _public_source_project(project)})
    return True


def _instance_group_ids(inst):
    """Effective public group membership, aligned with editor/server ownership.

    v1891: ordinary multi-group/stream cards may store some participants only in
    ``groupIds`` (legacy/publication-compatible metadata).  Those groups must be
    visible in every public shard.  Detailed ``streamParticipants`` remain
    authoritative over stale ``streamGroupIds``.  Transfer-split fragments are
    the one exception: their pre-split ``groupIds`` can be stale and must not
    reconnect groups that v1888/v1889 deliberately separated.
    """
    if not isinstance(inst, dict):
        return []
    out = []
    base_gid = inst.get("groupId")
    if base_gid:
        out.append(str(base_gid))

    transfer_scoped = bool(
        inst.get("manualStreamBreak") or inst.get("transferSubgroupDetached") or
        inst.get("transferSubgroupRetained") or inst.get("transferredLoadRemainder")
    )
    if not transfer_scoped or inst.get("isScheduleEvent") is True:
        for gid in inst.get("groupIds") or []:
            if gid:
                out.append(str(gid))

    explicit = inst.get("streamParticipants") if isinstance(inst.get("streamParticipants"), list) else []
    explicit_parts = any(isinstance(part, dict) and part.get("groupId") for part in explicit)
    if explicit_parts:
        for part in explicit:
            if isinstance(part, dict) and part.get("groupId"):
                out.append(str(part.get("groupId")))
    else:
        for gid in inst.get("streamGroupIds") or []:
            if gid:
                out.append(str(gid))
    return list(dict.fromkeys(out))


def _canonical_public_inst_id(value):
    """Stable identity shared by working and week-publication occurrence ids."""
    import re
    sid = str(value or "")
    # Week publication may wrap an already published occurrence more than once.
    # Always compare the original working id, not the disposable __PUBW suffix.
    while re.search(r"__PUBW\d+$", sid, flags=re.I):
        sid = re.sub(r"__PUBW\d+$", "", sid, flags=re.I)
    return sid


def _public_instance_identity_ids(inst):
    if not isinstance(inst, dict):
        return set()
    out = set()
    for value in (inst.get("instId"), inst.get("_publishedSourceInstId")):
        sid = str(value or "")
        if not sid:
            continue
        out.add(sid)
        root = _canonical_public_inst_id(sid)
        if root:
            out.add(root)
    return out


def _public_substitution_source_ids(sub):
    if not isinstance(sub, dict):
        return set()
    out = set()
    # instId is canonical today; the aliases keep old/legacy replacement rows usable.
    for key in ("instId", "sourceInstId", "originalInstId"):
        sid = str(sub.get(key) or "")
        if not sid:
            continue
        out.add(sid)
        root = _canonical_public_inst_id(sid)
        if root:
            out.add(root)
    return out


def _substitution_targets_teacher(sub, teacher_id):
    if not isinstance(sub, dict):
        return False
    tid = str(teacher_id or "")
    if not tid:
        return False
    # Never use `teacherId or newTeacherId`: legacy rows can contain BOTH.
    # newTeacherId is the receiver of a replacement; teacherId is retained only
    # for compatibility with old rows that used it as the effective teacher.
    return tid in {str(sub.get("newTeacherId") or ""), str(sub.get("teacherId") or "")}


def _public_instance_teacher_ids(project, inst, loads_by_id=None):
    """Teacher identities visible for one public schedule occurrence.

    The browser refreshes instance.teacherId from the authoritative load row.
    Public teacher shards used to filter the *raw* instance before that refresh,
    so a legacy/stale teacherId (or a stream participant whose teacher lived only
    on its loadId) produced an empty /teacher/<id> page although group pages still
    showed the lesson. Resolve the same identities server-side before sharding.
    """
    if not isinstance(inst, dict):
        return set()
    if loads_by_id is None:
        loads_by_id = {
            str(row.get("id") or row.get("loadId") or ""): row
            for row in ((project or {}).get("loads") or [])
            if isinstance(row, dict) and str(row.get("id") or row.get("loadId") or "")
        }
    out = set()
    def add(value):
        sid = str(value or "")
        if sid:
            out.add(sid)
    add(inst.get("teacherId"))
    for tid in (inst.get("teacherIds") or []):
        add(tid)
    primary_load = loads_by_id.get(str(inst.get("loadId") or ""))
    if isinstance(primary_load, dict):
        add(primary_load.get("teacherId"))
    for part in (inst.get("streamParticipants") or []):
        if not isinstance(part, dict):
            continue
        add(part.get("teacherId"))
        part_load = loads_by_id.get(str(part.get("loadId") or ""))
        if isinstance(part_load, dict):
            add(part_load.get("teacherId"))
    return out


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
    if pattern == "perWeek":
        # v1594: never treat graph-based rows as active for the whole semester.
        # If weeklyPairs is embedded in the instance, only weeks with a positive
        # value are active. Project-level fallback is handled by
        # _instance_weeks_for_project_py below.
        wp = inst.get("weeklyPairs") or {}
        if isinstance(wp, dict) and wp:
            out = []
            for key, value in wp.items():
                try:
                    n = int(key); amount = float(value or 0)
                except Exception:
                    continue
                if 1 <= n <= total and amount > 0:
                    out.append(n)
            return sorted(set(out))
        return []
    if pattern == "all":
        return list(range(1, total + 1))
    # Client parity is semester-week parity; odd/even are enough for public trimming.
    if pattern == "odd":
        return [n for n in range(1, total + 1) if n % 2 == 1]
    if pattern == "even":
        return [n for n in range(1, total + 1) if n % 2 == 0]
    return list(range(1, total + 1))


def _instance_weeks_for_project_py(project, inst):
    """Resolve actual active weeks, including legacy/stale perWeek instances.

    Normally schedule sync converts a perWeek load into custom week-layer
    instances. During hot manual edits an older perWeek instance can still be
    present for a moment. The conflict validator must read the source load's
    weeklyPairs instead of assuming that instance occupies every week.
    """
    cfg = (project or {}).get("config") or {}
    if not isinstance(inst, dict):
        return []
    if str(inst.get("weekPattern") or "all") != "perWeek":
        return _instance_weeks_py(cfg, inst)
    total = _total_semester_weeks_py(cfg)
    if total <= 0:
        return []
    wp = inst.get("weeklyPairs") or {}
    if not isinstance(wp, dict) or not wp:
        load_id = str(inst.get("loadId") or "")
        load = next((x for x in ((project or {}).get("loads") or [])
                     if isinstance(x, dict) and str(x.get("id") or "") == load_id), None)
        wp = (load or {}).get("weeklyPairs") or {}
    out = []
    if isinstance(wp, dict):
        for key, value in wp.items():
            try:
                n = int(key); amount = float(value or 0)
            except Exception:
                continue
            if 1 <= n <= total and amount > 0:
                out.append(n)
    return sorted(set(out))


def _instance_visible_weeks_for_assignment_py(project, inst, assignment):
    """Mirror the weekly grid's real visibility rules for one assigned card.

    The client does not render an occurrence merely because its weekPattern says
    that it exists. It also excludes:
      * dates outside semester bounds;
      * holidays / calendarExceptions with noClasses;
      * dates when any participating group is on practice.

    Before v1604 the server conflict validator ignored those date-level rules.
    That produced a classic ghost conflict: the grid was empty, but the server
    still treated an invisible occurrence as occupying the slot.
    """
    if not isinstance(project, dict) or not isinstance(inst, dict) or not isinstance(assignment, dict):
        return []

    cfg = project.get("config") or {}
    try:
        semester_start = date.fromisoformat(str(cfg.get("semesterStart") or ""))
        semester_end = date.fromisoformat(str(cfg.get("semesterEnd") or ""))
        day = int(assignment.get("day"))
    except Exception:
        return []

    monday = semester_start - timedelta(days=semester_start.weekday())

    # Same no-class sources as frontend calendarExceptionFor()/isNoClassDate().
    holidays = {str(x) for x in (cfg.get("holidays") or []) if str(x)}
    exceptions = []
    for row in (project.get("calendarExceptions") or []):
        if isinstance(row, dict):
            exceptions.append(row)
    for row in (cfg.get("calendarExceptions") or []):
        if isinstance(row, dict):
            exceptions.append(row)
    no_class_dates = {
        str(row.get("date") or "")
        for row in exceptions
        if row.get("noClasses") and str(row.get("date") or "")
    }

    # Effective participating groups must use the same group resolver as the
    # conflict validator, including streamParticipants authority from v1603.
    group_ids = _schedule_instance_group_ids(inst)
    practices = [x for x in (project.get("practices") or []) if isinstance(x, dict)]

    out = []
    for week in _instance_weeks_py(cfg, inst):
        try:
            occurrence = monday + timedelta(days=(int(week) - 1) * 7 + day)
        except Exception:
            continue
        if occurrence < semester_start or occurrence > semester_end:
            continue
        d = occurrence.isoformat()
        if d in holidays or d in no_class_dates:
            continue

        on_practice = False
        for practice in practices:
            gid = str(practice.get("groupId") or "")
            if not gid or gid not in group_ids:
                continue
            start = str(practice.get("dateStart") or "")
            end = str(practice.get("dateEnd") or "")
            if start and end and start <= d <= end:
                on_practice = True
                break
        if on_practice:
            continue

        out.append(int(week))

    return sorted(set(out))


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
    loads_by_id = {
        str(row.get("id") or row.get("loadId") or ""): row
        for row in (project.get("loads") or [])
        if isinstance(row, dict) and str(row.get("id") or row.get("loadId") or "")
    }
    if kind == "group":
        raw_chosen = [inst for inst in instances if entity_id in _instance_group_ids(inst)]
    elif kind == "teacher":
        # Original teacher's instances plus source instances needed to render
        # moved/substitute lessons assigned to this teacher. v1870 resolves the
        # teacher from authoritative loadIds too; raw instance.teacherId can be
        # stale after graph/stream operations even while the group card is valid.
        incoming_subs = [
            sub for sub in (project.get("substitutions") or [])
            if isinstance(sub, dict) and _substitution_targets_teacher(sub, entity_id)
        ]
        extra_inst_ids = set()
        for sub in incoming_subs:
            extra_inst_ids.update(_public_substitution_source_ids(sub))
        raw_chosen = [
            inst for inst in instances
            if entity_id in _public_instance_teacher_ids(project, inst, loads_by_id)
            # v1890: replacement receiver must see the source occurrence even when
            # the public week id has been rewritten one or more times.
            or bool(_public_instance_identity_ids(inst).intersection(extra_inst_ids))
        ]
    elif kind == "room":
        # v1864: public room timetable. Include lessons whose base assignment
        # uses the room, plus source lessons moved/reassigned INTO this room by
        # a date substitution. The client applies the overlay for the concrete
        # date, so a lesson reassigned away from this room is not shown as busy.
        assignment = schedule.get("assignment") if isinstance(schedule.get("assignment"), dict) else {}
        extra_inst_ids = set()
        for sub in (project.get("substitutions") or []):
            if not isinstance(sub, dict) or str(sub.get("newRoomId") or "") != entity_id:
                continue
            extra_inst_ids.update(_public_substitution_source_ids(sub))
        raw_chosen = []
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            iid = str(inst.get("instId") or "")
            a = assignment.get(iid) if iid else None
            room_ids = set()
            if isinstance(a, dict):
                for key in ("roomId", "extraRoomId"):
                    if a.get(key):
                        room_ids.add(str(a.get(key)))
                for key in ("roomIds", "additionalRoomIds", "extraRoomIds"):
                    for item in (a.get(key) or []):
                        if isinstance(item, dict):
                            rid = item.get("roomId") or item.get("id")
                        else:
                            rid = item
                        if rid:
                            room_ids.add(str(rid))
            if (entity_id in room_ids
                or bool(_public_instance_identity_ids(inst).intersection(extra_inst_ids))):
                raw_chosen.append(inst)
    else:
        raw_chosen = []
    chosen = []
    for inst in raw_chosen:
        trimmed = _trim_instance_to_public_weeks(project, inst)
        if trimmed is not None:
            if kind == "teacher":
                # Shard-only compatibility metadata. Do not mutate the published
                # snapshot; merely make every resolved teacher identity visible to
                # the client before enrichInstanceStreamGroups() runs.
                trimmed = dict(trimmed)
                resolved = _public_instance_teacher_ids(project, inst, loads_by_id)
                existing = [str(x) for x in (trimmed.get("teacherIds") or []) if str(x)]
                trimmed["teacherIds"] = list(dict.fromkeys(existing + sorted(resolved)))
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
    inst_ids = set()
    for inst in chosen_instances:
        inst_ids.update(_public_instance_identity_ids(inst))
    load_ids = set()
    group_ids = set()
    teacher_ids = set()
    for inst in chosen_instances:
        if inst.get("loadId"):
            load_ids.add(str(inst.get("loadId")))
        # v1891: retain load rows for EVERY participant of a public stream.
        # Older stream cards can carry secondary groups only in groupIds, and
        # dropping those rows makes student/teacher shards collapse to primary.
        group_ids.update(_instance_group_ids(inst))
        if inst.get("teacherId"):
            teacher_ids.add(str(inst.get("teacherId")))
        for tid in (inst.get("teacherIds") or []):
            if tid:
                teacher_ids.add(str(tid))
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
            bool(_public_substitution_source_ids(row).intersection(inst_ids))
            or str(row.get("groupId") or "") in group_ids
            or str(row.get("teacherId") or "") in teacher_ids
            or str(row.get("newTeacherId") or "") in teacher_ids
        )
    ]
    return {"loads": loads, "substitutions": substitutions}


def _public_schedule_shard_for_week(project, schedule, week_number):
    """Trim an already entity-scoped schedule to exactly one semester week.

    v1894: a moved lesson can target a different semester week than its source
    occurrence.  The target-week public page still needs the source instance as
    metadata so the client can materialise the moved card.  Keep that instance
    in the shard as support data, but DO NOT rewrite its weekPattern: ordinary
    rendering must continue to see that the source lesson itself does not occur
    in the target week.
    """
    try:
        week_number = int(week_number)
    except Exception:
        return schedule
    if week_number <= 0:
        return schedule
    instances = schedule.get("instances") if isinstance(schedule, dict) else []

    from datetime import date, timedelta
    cfg = project.get("config") or {}
    support_source_ids = set()
    try:
        semester_start = date.fromisoformat(str(cfg.get("semesterStart") or ""))
        semester_monday = semester_start - timedelta(days=semester_start.weekday())
    except Exception:
        semester_start = None
        semester_monday = None
    if semester_monday is not None:
        for sub in (project.get("substitutions") or []):
            if not isinstance(sub, dict) or str(sub.get("type") or "") != "moved":
                continue
            target_date = str(sub.get("targetDate") or "")
            try:
                target_day = date.fromisoformat(target_date)
            except Exception:
                continue
            target_week = ((target_day - semester_monday).days // 7) + 1
            if int(target_week) == week_number:
                support_source_ids.update(_public_substitution_source_ids(sub))

    chosen = []
    for inst in (instances or []):
        normal_week = week_number in _instance_weeks_py(cfg, inst)
        support_only = bool(_public_instance_identity_ids(inst).intersection(support_source_ids))
        if not normal_week and not support_only:
            continue
        out = dict(inst)
        if normal_week:
            out["weekPattern"] = "custom"
            out["customWeeks"] = [week_number]
        else:
            out["_publicMoveSupportOnly"] = True
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
    # Include the code-level shard schema, not just the publication marker.
    # This lets a deploy invalidate broken derived public files while preserving
    # the exact publishedSnapshot and publishedAt chosen by staff.
    seed = f"{marker}|shard-schema:{PUBLIC_SHARD_SCHEMA_VERSION}"
    return "snapshot-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


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
        inst_ids.update(_public_instance_identity_ids(inst))
        # v1870: use the same authoritative load-aware teacher resolver as
        # public teacher shards. Otherwise a group-only publication can rebuild
        # the group shard but leave the real teacher's shard stale.
        teacher_ids.update(_public_instance_teacher_ids(project, inst))
    for sub in (project.get("substitutions") or []):
        if not isinstance(sub, dict):
            continue
        if str(sub.get("groupId") or "") in group_ids or bool(_public_substitution_source_ids(sub).intersection(inst_ids)):
            # v1890: a legacy substitution may carry original teacherId AND
            # replacement newTeacherId. Rebuild both public teacher shards.
            for value in (sub.get("teacherId"), sub.get("newTeacherId")):
                tid = str(value or "")
                if tid:
                    teacher_ids.add(tid)
    return teacher_ids



def _activate_public_snapshot_lazy(project, marker):
    """Atomically switch the public contour to a new snapshot namespace.

    v1611: publication must not synchronously rebuild every group x teacher x week
    file. That work can exceed the request budget on production and previously
    caused HTTP 503 despite valid publication data.

    We create a fresh immutable snapshot namespace + manifest immediately.
    Individual group/teacher shards are then materialised on demand by the already
    existing _ensure_public_shard_on_demand(), always from publishedSnapshot.
    """
    source = _public_source_project(project)
    snapshot_name = _public_snapshot_dir_name(marker)
    final_dir = os.path.join(PUBLIC_SHARD_DIR, snapshot_name)
    total_weeks = _total_semester_weeks_py(source.get("config") or {})
    published_weeks = set(range(1, total_weeks + 1))
    if source.get("publicWeekSelectionEnabled") is True:
        published_weeks = {
            int(x) for x in (source.get("publishedWeeks") or [])
            if str(x).isdigit() and 1 <= int(x) <= total_weeks
        }

    # Never reuse files generated for a previous failed attempt with the same
    # marker; a publication activation starts from a clean namespace.
    if os.path.isdir(final_dir):
        shutil.rmtree(final_dir, ignore_errors=True)
    os.makedirs(final_dir, exist_ok=True)

    manifest = {
        "marker": marker,
        "snapshot": snapshot_name,
        "totalWeeks": total_weeks,
        "publishedWeeks": sorted(published_weeks),
        "stats": {
            "group": 0,
            "teacher": 0,
            "weekFiles": 0,
            "reused": False,
            "scope": "lazy",
        },
        "lazy": True,
        "schemaVersion": PUBLIC_SHARD_SCHEMA_VERSION,
    }
    _atomic_write_bytes(
        PUBLIC_SHARD_SNAPSHOT_FILE,
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )

    # Keep only a tiny number of immutable namespaces.
    try:
        dirs = [
            d for d in os.listdir(PUBLIC_SHARD_DIR)
            if d.startswith("snapshot-") and os.path.isdir(os.path.join(PUBLIC_SHARD_DIR, d))
        ]
        dirs.sort(key=lambda d: os.path.getmtime(os.path.join(PUBLIC_SHARD_DIR, d)), reverse=True)
        for old in dirs[2:]:
            shutil.rmtree(os.path.join(PUBLIC_SHARD_DIR, old), ignore_errors=True)
    except Exception:
        pass

    return manifest


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
        # v1891: a group publication that touches one member of a stream changes
        # the same shared occurrence for every participant.  Rebuild all of those
        # group shards; otherwise hard-linked shards for peers (e.g. groups 12/14)
        # remain stale even though the canonical publishedSnapshot is correct.
        schedule = source.get("schedule") if isinstance(source.get("schedule"), dict) else {}
        expanded = set(group_ids)
        changed = True
        while changed:
            changed = False
            for inst in (schedule.get("instances") or []):
                if not isinstance(inst, dict):
                    continue
                members = set(_instance_group_ids(inst))
                if not members or not expanded.intersection(members):
                    continue
                before = len(expanded)
                expanded.update(members)
                if len(expanded) != before:
                    changed = True
        group_ids = expanded
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
            "schemaVersion": PUBLIC_SHARD_SCHEMA_VERSION,
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


def _upgrade_public_shard_manifest_schema(manifest):
    """Switch derived public shards to the current code schema, without republish.

    The publication marker and publishedSnapshot remain untouched. Only the
    disposable shard directory pointer changes, so each entity is rebuilt lazily
    from the already-published source when a visitor opens it.
    """
    if not isinstance(manifest, dict):
        return manifest
    try:
        if int(manifest.get("schemaVersion") or 0) == PUBLIC_SHARD_SCHEMA_VERSION:
            return manifest
    except Exception:
        pass
    with _public_activation_lock:
        current = _load_public_shard_manifest()
        if not isinstance(current, dict):
            return manifest
        try:
            if int(current.get("schemaVersion") or 0) == PUBLIC_SHARD_SCHEMA_VERSION:
                return current
        except Exception:
            pass
        marker = str(current.get("marker") or "")
        if not marker:
            return current
        snapshot_name = _public_snapshot_dir_name(marker)
        os.makedirs(os.path.join(PUBLIC_SHARD_DIR, snapshot_name), exist_ok=True)
        upgraded = {
            **current,
            "snapshot": snapshot_name,
            "schemaVersion": PUBLIC_SHARD_SCHEMA_VERSION,
            "stats": {**(current.get("stats") or {}), "schemaUpgrade": True, "lazy": True},
            "lazy": True,
        }
        _atomic_write_bytes(
            PUBLIC_SHARD_SNAPSHOT_FILE,
            json.dumps(upgraded, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        return upgraded


def _ensure_public_shard_on_demand(kind, entity_id, scope="semester", week=0, _retry=False):
    """Create only the requested public shard from the ACTIVE publication.

    v1613:
    - the global manifest is authoritative;
    - a stale worker/cache may never rewrite it backwards;
    - if cache marker != active manifest marker, refresh from canonical storage
      and retry once;
    - publication and lazy shard creation are serialized in this gthread worker.
    """
    with _public_activation_lock:
        project, marker = _public_project_for_shard()
        if not isinstance(project, dict) or not marker:
            return None
        if kind not in {"group", "teacher", "room"}:
            return None

        # The manifest is the active public pointer. If our RAM cache still points
        # at the previous publication, NEVER overwrite the manifest with it.
        active_manifest = _load_public_shard_manifest()
        if isinstance(active_manifest, dict):
            active_marker = str(active_manifest.get("marker") or "")
            if active_marker and active_marker != str(marker):
                if _retry:
                    return None
                # Drop only public-index RAM cache; canonical SQLite contains the
                # publication that owns the active manifest.
                with _public_index_cache_lock:
                    _public_index_cache.update({
                        "key": None, "plain": None, "gzip": None,
                        "etag": None, "project": None,
                    })
                # Recursive call is made after releasing the lock below.
                retry_needed = True
            else:
                retry_needed = False
        else:
            retry_needed = False

        if retry_needed:
            pass
        else:
            rows = project.get("groups") if kind == "group" else (project.get("teachers") if kind == "teacher" else project.get("rooms"))
            exists = any(
                isinstance(row, dict) and str(row.get("id") or "") == str(entity_id)
                for row in (rows or [])
            )
            if not exists:
                return None

            total_weeks = _total_semester_weeks_py(project.get("config") or {})
            published_weeks = set(range(1, total_weeks + 1))
            if project.get("publicWeekSelectionEnabled") is True:
                published_weeks = {
                    int(x) for x in (project.get("publishedWeeks") or [])
                    if str(x).isdigit() and 1 <= int(x) <= total_weeks
                }

            if scope == "week":
                week = int(week or 0)
                if week <= 0 or week not in published_weeks:
                    return None
            else:
                scope = "semester"
                week = 0

            snapshot_name = _public_snapshot_dir_name(marker)

            # If a manifest exists, its snapshot name is authoritative too.
            if isinstance(active_manifest, dict) and str(active_manifest.get("marker") or "") == str(marker):
                snapshot_name = str(active_manifest.get("snapshot") or snapshot_name)

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
                    "marker": marker,
                    "kind": kind,
                    "id": str(entity_id),
                    "scope": scope,
                }
                if scope == "week":
                    payload["week"] = week
                _write_public_shard_file(plain_path, payload)

            # IMPORTANT: only initialise a missing manifest. An on-demand public
            # request is never allowed to replace an existing active manifest.
            manifest = _load_public_shard_manifest()
            if not isinstance(manifest, dict):
                manifest = {
                    "marker": marker,
                    "snapshot": snapshot_name,
                    "totalWeeks": total_weeks,
                    "publishedWeeks": sorted(published_weeks),
                    "stats": {"lazy": True},
                    "lazy": True,
                    "schemaVersion": PUBLIC_SHARD_SCHEMA_VERSION,
                }
                _atomic_write_bytes(
                    PUBLIC_SHARD_SNAPSHOT_FILE,
                    json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                )
            return manifest

    # Stale cache path: retry OUTSIDE the non-reentrant activation lock.
    return _ensure_public_shard_on_demand(kind, entity_id, scope=scope, week=week, _retry=True)


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
    meta_plain = json.dumps({"marker": marker, "etag": etag, "size": len(plain), "gzipSize": len(gz), "schemaVersion": PUBLIC_INDEX_SCHEMA_VERSION}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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



def _sqlite_connect():
    """Open the canonical SQLite store with safe settings for one-container deployment."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(SQLITE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # v1712: heavy historical semester snapshots live outside the main project
    # JSON. The editor still sees lightweight scheduleVersions metadata, while
    # /api/storage-version loads the requested snapshot lazily from this table.
    conn.execute("CREATE TABLE IF NOT EXISTS publication_snapshots (store_key TEXT NOT NULL, version_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(store_key, version_id))")
    # v1638: immutable accounting archive. Unlike scheduleVersions, these rows
    # are append-only and are not pruned by ordinary project saves.
    conn.execute("""CREATE TABLE IF NOT EXISTS schedule_publication_archive (
        archive_id TEXT PRIMARY KEY,
        store_key TEXT NOT NULL,
        version_id TEXT NOT NULL,
        published_at TEXT NOT NULL,
        published_by TEXT NOT NULL,
        note TEXT NOT NULL,
        scope TEXT NOT NULL,
        week_numbers_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedule_publication_archive_store_time ON schedule_publication_archive(store_key, created_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS schedule_week_final_archive (
        archive_id TEXT PRIMARY KEY,
        store_key TEXT NOT NULL,
        week_number INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        source_version_id TEXT NOT NULL,
        finalized_at TEXT NOT NULL,
        finalized_by TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedule_week_final_store_week ON schedule_week_final_archive(store_key, week_number, revision DESC)")
    # v1758: durable per-cell revision ledger. This is additive: the canonical
    # schedule JSON remains unchanged and all existing readers keep working.
    # Revisions only protect independent instId cells from stale concurrent writes.
    conn.execute("""CREATE TABLE IF NOT EXISTS schedule_cell_revision (
        store_key TEXT NOT NULL,
        inst_id TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL,
        updated_by TEXT NOT NULL DEFAULT '',
        request_id TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(store_key, inst_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedule_cell_revision_store ON schedule_cell_revision(store_key, revision)")
    conn.execute("""CREATE TABLE IF NOT EXISTS schedule_cell_change_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_key TEXT NOT NULL,
        inst_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        changed_at REAL NOT NULL,
        changed_by TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        request_id TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schedule_cell_change_store_time ON schedule_cell_change_log(store_key, changed_at DESC)")
    conn.commit()
    return conn


def _schedule_cell_revisions_get(store_key, inst_ids=None):
    """Return durable revisions for independent timetable cells."""
    try:
        conn = _sqlite_connect()
        try:
            if inst_ids is None:
                rows = conn.execute(
                    "SELECT inst_id, revision FROM schedule_cell_revision WHERE store_key=?",
                    (str(store_key),),
                ).fetchall()
            else:
                ids = [str(x) for x in inst_ids if str(x)]
                if not ids:
                    return {}
                out = {}
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i+500]
                    q = ",".join("?" for _ in chunk)
                    rows = conn.execute(
                        f"SELECT inst_id, revision FROM schedule_cell_revision WHERE store_key=? AND inst_id IN ({q})",
                        [str(store_key), *chunk],
                    ).fetchall()
                    out.update({str(inst_id): int(rev or 0) for inst_id, rev in rows})
                return out
            return {str(inst_id): int(rev or 0) for inst_id, rev in rows}
        finally:
            conn.close()
    except Exception:
        app.logger.exception("failed to read schedule cell revisions")
        return {}


def _schedule_cell_revisions_commit(store_key, revision_map, meta_by_id=None):
    """Persist already-committed cell revisions and a compact audit trail."""
    revision_map = {str(k): int(v) for k, v in (revision_map or {}).items() if str(k)}
    if not revision_map:
        return
    meta_by_id = meta_by_id or {}
    now = time.time()
    conn = _sqlite_connect()
    try:
        for sid, revision in revision_map.items():
            meta = meta_by_id.get(sid) if isinstance(meta_by_id, dict) else {}
            meta = meta if isinstance(meta, dict) else {}
            by = str(meta.get("by") or "Пользователь")
            session_id = str(meta.get("sessionId") or "")
            request_id = str(meta.get("requestId") or "")
            conn.execute(
                """INSERT INTO schedule_cell_revision(store_key,inst_id,revision,updated_at,updated_by,request_id)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(store_key,inst_id) DO UPDATE SET
                     revision=excluded.revision, updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by, request_id=excluded.request_id""",
                (str(store_key), sid, revision, now, by, request_id),
            )
            conn.execute(
                "INSERT INTO schedule_cell_change_log(store_key,inst_id,revision,changed_at,changed_by,session_id,request_id) VALUES(?,?,?,?,?,?,?)",
                (str(store_key), sid, revision, now, by, session_id, request_id),
            )
        # keep the journal bounded; revisions themselves are never pruned
        conn.execute(
            """DELETE FROM schedule_cell_change_log WHERE id IN (
                 SELECT id FROM schedule_cell_change_log WHERE store_key=?
                 ORDER BY id DESC LIMIT -1 OFFSET 20000
               )""",
            (str(store_key),),
        )
        conn.commit()
    finally:
        conn.close()


def _project_without_embedded_snapshots(store_key, value, conn=None):
    """Move scheduleVersions[*].snapshot to SQLite and return lightweight value.

    This keeps the canonical schedule-data-v2 row small enough for frequent saves.
    The operation is transaction-friendly: when conn is supplied, snapshot rows and
    the project row are committed together by the caller.
    """
    parsed_from_string = isinstance(value, str)
    if parsed_from_string:
        try:
            project = json.loads(value)
        except Exception:
            return value
    else:
        project = value
    if not isinstance(project, dict):
        return value
    versions = project.get("scheduleVersions")
    if not isinstance(versions, list):
        return value
    own_conn = conn is None
    if own_conn:
        conn = _sqlite_connect()
        conn.execute("BEGIN IMMEDIATE")
    changed = False
    kept_ids = []
    new_versions = []
    try:
        now = time.time()
        for raw in versions[-40:]:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            version_id = str(row.get("id") or "")
            snapshot = row.pop("snapshot", None)
            if version_id:
                kept_ids.append(version_id)
            if isinstance(snapshot, dict) and version_id:
                payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                conn.execute(
                    "INSERT INTO publication_snapshots(store_key,version_id,snapshot_json,created_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(store_key,version_id) DO UPDATE SET snapshot_json=excluded.snapshot_json, created_at=excluded.created_at",
                    (str(store_key), version_id, payload, now),
                )
                row["hasSnapshot"] = True
                if row.get("placed") is None:
                    try:
                        row["placed"] = ((snapshot.get("schedule") or {}).get("stats") or {}).get("placed")
                    except Exception:
                        pass
                changed = True
            new_versions.append(row)
        if len(new_versions) != len(versions):
            changed = True
        if kept_ids:
            placeholders = ",".join("?" for _ in kept_ids)
            conn.execute(
                f"DELETE FROM publication_snapshots WHERE store_key=? AND version_id NOT IN ({placeholders})",
                (str(store_key), *kept_ids),
            )
        else:
            conn.execute("DELETE FROM publication_snapshots WHERE store_key=?", (str(store_key),))
        if changed:
            project = dict(project)
            project["scheduleVersions"] = new_versions
        if own_conn:
            conn.commit()
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
    if not changed:
        return value
    return json.dumps(project, ensure_ascii=False, separators=(",", ":")) if parsed_from_string else project


def _load_publication_snapshot(store_key, version_id):
    if not os.path.exists(SQLITE_PATH):
        return None
    conn = _sqlite_connect()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM publication_snapshots WHERE store_key=? AND version_id=?",
            (str(store_key), str(version_id)),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _storage_exists():
    return os.path.exists(SQLITE_PATH) or os.path.exists(LEGACY_DATA_FILE)


def _migrate_legacy_json_to_sqlite_if_needed():
    """One-time, non-destructive import of legacy storage.json into SQLite.

    The JSON file is deliberately left in place as a rollback safety copy. Once
    the database exists it is never re-imported, so a stale legacy file cannot
    overwrite newer SQLite data after a restart.
    """
    with _sqlite_init_lock:
        if os.path.exists(SQLITE_PATH):
            return False
        if not os.path.exists(LEGACY_DATA_FILE):
            return False
        with open(LEGACY_DATA_FILE, "r", encoding="utf-8") as f:
            try:
                legacy = json.load(f)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"storage.json is corrupted; SQLite migration aborted: {exc}")
        if not isinstance(legacy, dict):
            raise RuntimeError("storage.json has invalid root type; SQLite migration aborted")
        conn = _sqlite_connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            for key, value in legacy.items():
                payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store(key,value_json,updated_at) VALUES(?,?,?)",
                    (str(key), payload, now),
                )
            conn.execute(
                "INSERT OR REPLACE INTO storage_meta(key,value) VALUES('migrated_from_storage_json', ?)",
                (datetime.utcnow().isoformat(timespec="seconds") + "Z",),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            try:
                os.remove(SQLITE_PATH)
            except OSError:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _invalidate_store_cache()
        return True


def load_store():
    _migrate_legacy_json_to_sqlite_if_needed()
    if not os.path.exists(SQLITE_PATH):
        _invalidate_store_cache()
        return {}
    # All application writes go through save_store(), which refreshes this cache.
    # On process start the cache is empty and SQLite is read once.
    if _store_cache.get("store") is not None:
        return dict(_store_cache["store"])
    conn = _sqlite_connect()
    try:
        rows = conn.execute("SELECT key, value_json FROM kv_store").fetchall()
    finally:
        conn.close()
    parsed = {}
    for key, value_json in rows:
        try:
            parsed[key] = json.loads(value_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"SQLite value for {key!r} is corrupted: {exc}")
    # v1712: migrate old embedded version snapshots lazily on the first read.
    # This is non-destructive: snapshot bytes are inserted before the compact
    # project value replaces the old row.
    working = parsed.get("schedule-data-v2")
    if isinstance(working, dict) and any(isinstance(x, dict) and isinstance(x.get("snapshot"), dict) for x in (working.get("scheduleVersions") or [])):
        parsed = _replace_sqlite_store(parsed, durable=True)
    _store_cache["mtime"] = None
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
        (name for name in os.listdir(BACKUP_DIR) if name.startswith("storage_") and (name.endswith(".json") or name.endswith(".json.gz"))),
        reverse=True,
    )
    for name in names:
        path = os.path.join(BACKUP_DIR, name)
        try:
            if name.endswith(".gz"):
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    store = json.load(f)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    store = json.load(f)
            value = store.get(key)
            if _is_valid_working_value(value):
                return name, store
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None, None


def _store_with_snapshots_for_backup(store):
    """Hydrate external publication snapshots into a portable JSON backup."""
    out = copy.deepcopy(store)
    value = out.get("schedule-data-v2")
    if not isinstance(value, dict) or not isinstance(value.get("scheduleVersions"), list) or not os.path.exists(SQLITE_PATH):
        return out
    conn = _sqlite_connect()
    try:
        rows = conn.execute("SELECT version_id, snapshot_json FROM publication_snapshots WHERE store_key=?", ("schedule-data-v2",)).fetchall()
    finally:
        conn.close()
    snapshots = {}
    for version_id, payload in rows:
        try:
            snapshots[str(version_id)] = json.loads(payload)
        except Exception:
            continue
    hydrated = []
    for raw in value.get("scheduleVersions") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        vid = str(row.get("id") or "")
        if not isinstance(row.get("snapshot"), dict) and vid in snapshots:
            row["snapshot"] = snapshots[vid]
            row["hasSnapshot"] = True
        hydrated.append(row)
    value = dict(value)
    value["scheduleVersions"] = hydrated
    out["schedule-data-v2"] = value
    return out


def _write_json_backup_snapshot(store=None):
    store = load_store() if store is None else store
    if not store:
        return None
    store = _store_with_snapshots_for_backup(store)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%fZ")
    target = os.path.join(BACKUP_DIR, f"storage_{stamp}.json.gz")
    fd, tmp = tempfile.mkstemp(prefix="backup.", suffix=".tmp", dir=BACKUP_DIR)
    try:
        # Write gzip directly to the atomic temp file. Typical timetable JSON
        # compresses several-fold, preventing the backup ring from filling /data.
        with os.fdopen(fd, "wb") as raw_f:
            with gzip.GzipFile(fileobj=raw_f, mode="wb", compresslevel=3) as gz_f:
                gz_f.write(json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            raw_f.flush()
            os.fsync(raw_f.fileno())
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp:
            try: os.remove(tmp)
            except OSError: pass
    backups = sorted(
        (os.path.join(BACKUP_DIR, name) for name in os.listdir(BACKUP_DIR) if name.startswith("storage_") and (name.endswith(".json") or name.endswith(".json.gz"))),
        key=os.path.getmtime, reverse=True,
    )
    for old_path in backups[MAX_BACKUPS:]:
        try: os.remove(old_path)
        except OSError: pass
    return target


def _backup_current_store():
    if not _storage_exists():
        return
    _write_json_backup_snapshot()


def _replace_sqlite_store(store, durable=True):
    """Atomically replace the logical key/value store in one SQLite transaction."""
    conn = _sqlite_connect()
    try:
        conn.execute("PRAGMA synchronous=" + ("FULL" if durable else "NORMAL"))
        conn.execute("BEGIN IMMEDIATE")
        existing = {row[0] for row in conn.execute("SELECT key FROM kv_store")}
        incoming = set(map(str, store.keys()))
        for key in existing - incoming:
            conn.execute("DELETE FROM kv_store WHERE key=?", (key,))
        now = time.time()
        compacted_store = {}
        for key, value in store.items():
            compacted_store[str(key)] = _project_without_embedded_snapshots(str(key), value, conn=conn) if str(key) == "schedule-data-v2" else value
        for key, value in compacted_store.items():
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO kv_store(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (str(key), payload, now),
            )
        conn.commit()
        return compacted_store
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_store(store, backup=True, durable=True, refresh_public=True, refresh_status=True):
    # v1711: SQLite is the canonical working store. A save is a single database
    # transaction, so readers never observe a half-written project and there is
    # no storage.json temp-file/rename race. JSON backups remain available for
    # human inspection and one-click restore.
    os.makedirs(DATA_DIR, exist_ok=True)
    if backup and _storage_exists():
        _backup_current_store()
    cached_store = _replace_sqlite_store(store, durable=durable)
    # Cache the exact compact logical form committed to SQLite; historical
    # snapshots stay in publication_snapshots and are fetched only on restore.
    _store_cache["mtime"] = None
    _store_cache["store"] = cached_store

    if refresh_status:
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



def _save_store_key_fast(key, value, durable=False, revision_map=None, revision_meta_by_id=None):
    """Persist one hot storage key without re-serializing every logical key.

    Timetable cell edits touch ``schedule-data-v2`` only.  The generic
    ``save_store`` path walks/serializes the complete logical store and then opens
    a second SQLite transaction for the revision ledger.  For the hot cell path we
    write the one project row and its revisions in one transaction, then refresh
    the in-process cache.  Backups/public sidecars remain intentionally deferred
    exactly as they were for cell patches.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _sqlite_connect()
    revision_map = {str(k): int(v) for k, v in (revision_map or {}).items() if str(k)}
    revision_meta_by_id = revision_meta_by_id or {}
    revision_ledger_ok = True
    try:
        conn.execute("PRAGMA synchronous=" + ("FULL" if durable else "NORMAL"))
        conn.execute("BEGIN IMMEDIATE")
        compacted = _project_without_embedded_snapshots(str(key), value, conn=conn) if str(key) == "schedule-data-v2" else value
        payload = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        conn.execute(
            "INSERT INTO kv_store(key,value_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (str(key), payload, now),
        )
        if revision_map:
            try:
                for sid, revision in revision_map.items():
                    meta = revision_meta_by_id.get(sid) if isinstance(revision_meta_by_id, dict) else {}
                    meta = meta if isinstance(meta, dict) else {}
                    by = str(meta.get("by") or "Пользователь")
                    session_id = str(meta.get("sessionId") or "")
                    request_id = str(meta.get("requestId") or "")
                    conn.execute(
                        """INSERT INTO schedule_cell_revision(store_key,inst_id,revision,updated_at,updated_by,request_id)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(store_key,inst_id) DO UPDATE SET
                             revision=excluded.revision, updated_at=excluded.updated_at,
                             updated_by=excluded.updated_by, request_id=excluded.request_id""",
                        (str(key), sid, revision, now, by, request_id),
                    )
                    conn.execute(
                        "INSERT INTO schedule_cell_change_log(store_key,inst_id,revision,changed_at,changed_by,session_id,request_id) VALUES(?,?,?,?,?,?,?)",
                        (str(key), sid, revision, now, by, session_id, request_id),
                    )
                conn.execute(
                    """DELETE FROM schedule_cell_change_log WHERE id IN (
                         SELECT id FROM schedule_cell_change_log WHERE store_key=?
                         ORDER BY id DESC LIMIT -1 OFFSET 20000
                       )""",
                    (str(key),),
                )
            except Exception:
                revision_ledger_ok = False
                app.logger.exception("schedule cell revision ledger write failed inside fast key save")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    cached = dict(_store_cache.get("store") or {})
    cached[str(key)] = compacted
    _store_cache["mtime"] = None
    _store_cache["store"] = cached
    return compacted, revision_ledger_ok


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



def _generation_client_lease_alive(job_id):
    """A schedule job may write only while the browser that started it is alive.

    GET /api/schedule/generate/<job> renews the lease. A closed/reloaded tab stops
    polling, so an orphan calculation cannot keep changing canonical timetable in
    the background without anybody seeing it.
    """
    if not job_id:
        return True
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        if not job:
            return False
        if job.get("kind") != "schedule":
            return True
        lease_until = float(job.get("lease_until") or 0)
        if lease_until and time.time() <= lease_until:
            return True
        job["cancel_requested"] = True
        job["phase"] = "canceling"
        job["lease_expired"] = True
        return False

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# v1642: deployment fingerprint used only by authenticated admin UI polling.
# It changes when either the backend source or the built frontend shell changes.
# APP_BUILD_ID may override it explicitly in hosting settings.
def _compute_app_build_id():
    explicit = str(os.environ.get("APP_BUILD_ID") or "").strip()
    if explicit:
        return explicit
    h = hashlib.sha256()
    try:
        with open(__file__, "rb") as f:
            h.update(f.read())
    except Exception:
        h.update(str(os.path.getmtime(__file__) if os.path.exists(__file__) else time.time()).encode())
    try:
        index_path = os.path.join(STATIC_DIR, "index.html")
        with open(index_path, "rb") as f:
            h.update(f.read())
    except Exception:
        pass
    return h.hexdigest()[:20]

APP_BUILD_ID = _compute_app_build_id()

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
            "hasSnapshot": bool(snapshot) or row.get("hasSnapshot") is True,
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
                save_store(backup_store, backup=False, refresh_public=True)
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
            # v1758: revisions are transport metadata, not part of the project JSON.
            # Existing exports/backups/publication formats therefore remain unchanged.
            return jsonify({
                "key": key,
                "value": _editor_compact_project(parsed),
                "cellRevisions": _schedule_cell_revisions_get(key),
            })
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
            version = dict(row)
            snapshot = version.get("snapshot") if isinstance(version.get("snapshot"), dict) else _load_publication_snapshot(key, version_id)
            if not isinstance(snapshot, dict):
                return jsonify({"error": "snapshot missing"}), 404
            version["snapshot"] = snapshot
            return jsonify({"ok": True, "version": version})
    # Compatibility: snapshot row can survive briefly even if metadata was edited.
    snapshot = _load_publication_snapshot(key, version_id)
    if isinstance(snapshot, dict):
        return jsonify({"ok": True, "version": {"id": version_id, "snapshot": snapshot, "hasSnapshot": True}})
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




CURRENT_SCHEDULE_CLIENT_BUILD = 1895
# Short compatibility grace: during an active scheduling session a tab can lag
# behind several rapid deploys.  Keep the last three editor builds writable;
# the UI independently detects the newer build and reloads itself.  Very old
# clients are still rejected because their patch schema may no longer be safe.
SCHEDULE_CLIENT_BUILD_GRACE = 3
MIN_SCHEDULE_CLIENT_BUILD = max(1, CURRENT_SCHEDULE_CLIENT_BUILD - SCHEDULE_CLIENT_BUILD_GRACE)

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
        "currentBuild": CURRENT_SCHEDULE_CLIENT_BUILD,
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
        if not _storage_exists() and not allow_create:
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



@app.get("/api/public-control")
def public_control_status():
    # Tiny endpoint: no project/SQLite read. Used by the admin toggle.
    return jsonify(_public_pause_state())


@app.post("/api/public-control")
def public_control_set():
    user = _request_admin_user()
    if user is None:
        return jsonify({"error": "admin authorization required"}), 403
    body = request.get_json(force=True, silent=True) or {}
    state = _write_public_pause_state(body.get("paused") is True, user.get("name") or user.get("login") or user.get("id") or "")
    return jsonify({"ok": True, **state})


@app.get("/api/public-index")
def public_index():
    if _public_pause_state().get("paused"):
        resp = jsonify(_public_paused_payload())
        resp.headers["Cache-Control"] = "no-store"
        return resp
    """Serve prebuilt public dictionaries without parsing storage.json."""
    try:
        rebuild_index = not (os.path.exists(PUBLIC_INDEX_FILE) and os.path.exists(PUBLIC_INDEX_META_FILE))
        if not rebuild_index:
            try:
                with open(PUBLIC_INDEX_META_FILE, "r", encoding="utf-8") as f:
                    existing_meta = json.load(f)
                rebuild_index = int(existing_meta.get("schemaVersion") or 0) != PUBLIC_INDEX_SCHEMA_VERSION
            except Exception:
                rebuild_index = True
        if rebuild_index:
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
    if _public_pause_state().get("paused"):
        resp = jsonify(_public_paused_payload())
        resp.headers["Cache-Control"] = "no-store"
        return resp
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
    if _public_pause_state().get("paused"):
        resp = jsonify(_public_paused_payload())
        resp.status_code = 503
        resp.headers["Retry-After"] = "30"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    """Serve one prebuilt public entity/week file without touching storage.json."""
    try:
        if kind not in {"group", "teacher", "room"}:
            return jsonify({"error": "unknown public schedule kind"}), 404
        scope = str(request.args.get("scope") or "semester")
        week_raw = request.args.get("week")
        week = int(week_raw) if str(week_raw or "").isdigit() else 0
        if scope != "week" or week <= 0:
            scope = "semester"
            week = 0

        manifest = _load_public_shard_manifest()
        # v1870: derived teacher/group shard files are disposable. If their code
        # schema changed, point the SAME publication at a fresh lazy namespace.
        # No staff republish and no schedule mutation is required.
        if manifest:
            manifest = _upgrade_public_shard_manifest_schema(manifest)
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
    if _public_pause_state().get("paused"):
        resp = jsonify(_public_paused_payload())
        resp.headers["Cache-Control"] = "no-store"
        return resp
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
                        if not _generation_client_lease_alive(job_id):
                            return
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
                    if not _generation_client_lease_alive(job_id):
                        canceled = True
                        _scheduler_generate_worker.stop()
                        break
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
                if _generation_client_lease_alive(job_id):
                    consume_progress_stream()
                else:
                    canceled = True
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
                if isinstance(final_patch, dict) and _generation_client_lease_alive(job_id):
                    _apply_generation_group_progress(storage_key, final_patch, job_id=job_id, durable=True)
                elif isinstance(final_patch, dict):
                    with _generation_jobs_lock:
                        job = _generation_jobs.get(job_id)
                        if job is not None:
                            job.update({"status":"canceled","phase":"canceled","finished_at":time.time(),
                                        "message":"Расчёт остановлен: вкладка редактора больше не активна."})
                    return
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
            "lease_until": time.time() + GENERATION_CLIENT_LEASE_SECONDS,
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
    # v1838 PERF: do not json.dumps + hash the entire multi-megabyte project for
    # every "possible windows" request. Every durable schedule/graph save already
    # rotates _syncMeta.saveId, which is exactly the cache invalidation token we
    # need. Fall back to content hashing only for legacy snapshots without meta.
    sync_meta = data.get("_syncMeta") if isinstance(data, dict) else None
    storage_fingerprint = str((sync_meta or {}).get("saveId") or (sync_meta or {}).get("at") or "").strip()
    if not storage_fingerprint:
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

    # v1844 PERF: the common manual-placement request is for one visible week.
    # Sending/normalizing the entire 15–20k-instance semester in Node after each
    # schedule save made the picker feel like it was "loading the whole semester".
    # Build a compact schedule shard for the requested weeks while keeping all
    # dictionaries/loads/rooms intact. The worker cache key includes the week set
    # so this compact snapshot can never be reused by a full generation request.
    worker_data = data
    worker_cache_key = storage_fingerprint
    requested_weeks = sorted({
        int(w) for w in (body.get("weeks") or [])
        if str(w).strip().lstrip("-").isdigit() and int(w) > 0
    })
    if requested_weeks and isinstance(data, dict) and isinstance(data.get("schedule"), dict):
        week_set = set(requested_weeks)
        requested_ids = {str(x) for x in (body.get("instIds") or []) if str(x)}
        config = data.get("config") or {}
        parity_overrides = config.get("weekParityOverrides") or {}

        def _week_parity(n):
            override = parity_overrides.get(str(n), parity_overrides.get(n)) if isinstance(parity_overrides, dict) else None
            return str(override or ("odd" if int(n) % 2 == 1 else "even"))

        def _instance_hits_requested_week(inst):
            if not isinstance(inst, dict):
                return False
            if str(inst.get("instId") or "") in requested_ids:
                return True
            pattern = str(inst.get("weekPattern") or "all")
            if pattern == "all":
                return True
            if pattern == "custom":
                values = {int(x) for x in (inst.get("customWeeks") or []) if str(x).strip().lstrip("-").isdigit()}
                return bool(values & week_set)
            if pattern == "perWeek":
                wp = inst.get("weeklyPairs") or {}
                for week in week_set:
                    try:
                        value = wp.get(str(week), wp.get(week, 0)) if isinstance(wp, dict) else 0
                        if float(value or 0) > 0:
                            return True
                    except (TypeError, ValueError):
                        continue
                return False
            if pattern in {"odd", "even"}:
                return any(_week_parity(week) == pattern for week in week_set)
            # Unknown/legacy pattern: keep it rather than risk hiding a conflict.
            return True

        schedule = data.get("schedule") or {}
        source_instances = schedule.get("instances") or []
        relevant_instances = [inst for inst in source_instances if _instance_hits_requested_week(inst)]
        relevant_ids = {str(inst.get("instId") or "") for inst in relevant_instances if isinstance(inst, dict)}
        compact_schedule = dict(schedule)
        compact_schedule["instances"] = relevant_instances
        compact_schedule["assignment"] = {
            str(iid): a for iid, a in (schedule.get("assignment") or {}).items()
            if str(iid) in relevant_ids
        }
        compact_schedule["unplaced"] = [str(iid) for iid in (schedule.get("unplaced") or []) if str(iid) in relevant_ids]
        compact_schedule["locked"] = [str(iid) for iid in (schedule.get("locked") or []) if str(iid) in relevant_ids]
        worker_data = dict(data)
        worker_data["schedule"] = compact_schedule
        worker_cache_key = f"{storage_fingerprint}|options-weeks:{','.join(map(str, requested_weeks))}"

    started = time.time()
    try:
        worker_payload = {k: v for k, v in payload.items() if k not in {"data", "prior"}}
        parsed = _scheduler_options_worker.request(worker_payload, worker_cache_key, worker_data, 60)
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
        if job.get("kind") == "schedule" and job.get("status") in {"queued", "running"}:
            job["lease_until"] = time.time() + GENERATION_CLIENT_LEASE_SECONDS
        # v1572: progress exposed to the browser comes only from the exact
        # snapshot that _apply_generation_group_progress has already committed
        # to canonical storage. Never reread the Node progress file here: it can
        # legitimately be one or more sequences ahead of the persisted state.
        committed_progress = job.get("progress") if isinstance(job.get("progress"), dict) else None
        committed_seq = int(job.get("progress_seq") or 0)
        progress_history = [p for p in (job.get("progress_history") or []) if isinstance(p, dict)]
        public = {k: v for k, v in job.items() if k not in {
            "created_at", "started_at", "finished_at", "progress_file", "progress", "progress_history", "process", "protected_ids", "lease_until"
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
        if not _storage_exists():
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
        store[key] = merged
        # v1610: draft autosave must not block on a multi-MB backup nor touch the
        # public contour. SQLite NORMAL is still transactional, while avoiding
        # an fsync/public-status penalty on every small editor change.
        _maybe_periodic_backup_async()
        save_store(store, backup=False, durable=False, refresh_public=False, refresh_status=False)
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


def _schedule_cell_state_py(schedule, inst_id):
    schedule = schedule if isinstance(schedule, dict) else {}
    sid = str(inst_id or "")
    row = next((dict(x) for x in (schedule.get("instances") or []) if isinstance(x, dict) and str(x.get("instId") or "") == sid), None)
    assignment = schedule.get("assignment") or {}
    return {
        "instance": row,
        "assignment": dict(assignment[sid]) if sid in assignment and isinstance(assignment.get(sid), dict) else None,
        "locked": sid in {str(x) for x in (schedule.get("locked") or [])},
        "unplaced": sid in {str(x) for x in (schedule.get("unplaced") or [])},
    }


def _schedule_patch_cell_target_already_applied(schedule, patch, inst_id):
    """True when a retried request already produced its intended state."""
    sid = str(inst_id or "")
    state = _schedule_cell_state_py(schedule, sid)
    if sid in (patch.get("instancesSet") or {}) and state["instance"] != dict((patch.get("instancesSet") or {})[sid]):
        return False
    if sid in {str(x) for x in (patch.get("instancesRemove") or [])} and state["instance"] is not None:
        return False
    if sid in (patch.get("assignmentSet") or {}) and state["assignment"] != dict((patch.get("assignmentSet") or {})[sid]):
        return False
    if sid in {str(x) for x in (patch.get("assignmentRemove") or [])} and state["assignment"] is not None:
        return False
    if sid in {str(x) for x in (patch.get("lockedAdd") or [])} and not state["locked"]:
        return False
    if sid in {str(x) for x in (patch.get("lockedRemove") or [])} and state["locked"]:
        return False
    if sid in {str(x) for x in (patch.get("unplacedAdd") or [])} and not state["unplaced"]:
        return False
    if sid in {str(x) for x in (patch.get("unplacedRemove") or [])} and state["unplaced"]:
        return False
    return True


def _load_graph_pairs_for_week_py(project, load, week):
    """Return the graph's pair quantity for one load/week.

    The graph is a quantity model. Internal requirementLayer ids are generated
    later and must not decide whether a visible lesson belongs to the graph.
    """
    if not isinstance(load, dict):
        return 0.0
    try:
        week = int(week)
    except Exception:
        return 0.0
    pattern = str(load.get("weekPattern") or "all")
    if pattern == "perWeek":
        weekly = load.get("weeklyPairs") or {}
        try:
            return max(0.0, float(weekly.get(str(week), weekly.get(week, 0)) or 0))
        except Exception:
            return 0.0
    try:
        pairs = max(0.0, float(load.get("pairsPerWeek") or 0))
    except Exception:
        pairs = 0.0
    if pattern == "custom":
        active = set()
        for raw in load.get("customWeeks") or []:
            try: active.add(int(raw))
            except Exception: pass
        return pairs if week in active else 0.0
    if pattern in ("odd", "even"):
        cfg = (project or {}).get("config") or {}
        overrides = cfg.get("weekParityOverrides") or {}
        parity = overrides.get(str(week), overrides.get(week))
        if parity not in ("odd", "even"):
            parity = "odd" if week % 2 == 1 else "even"
        return pairs if parity == pattern else 0.0
    return pairs


def _load_graph_occurrence_capacity_py(project, load, week):
    """How many technical schedule occurrences the graph quantity authorizes."""
    import math
    pairs = _load_graph_pairs_for_week_py(project, load, week)
    return 0 if pairs <= 0 else max(1, int(math.ceil(pairs - 1e-9)))



def _graph_semantic_key_for_load_py(load):
    """Stable user-level identity of one graph requirement row.

    Format (ОЧНО/ДО), schedule instId and technical requirement layer are not
    graph identity. Equivalent duplicate load rows deliberately share a key so
    their weekly capacities are summed instead of double-counting schedule rows.
    """
    if not isinstance(load, dict):
        return ""
    return "¦".join([
        str(load.get("groupId") or ""),
        str(load.get("subjectId") or ""),
        str(load.get("teacherId") or ""),
        str(load.get("typeId") or ""),
        str(int(load.get("subgroup") or 0)),
    ])


def _graph_expected_semantic_capacity_for_week_py(project, week):
    """Graph pair-units authorized for each semantic requirement in one week.

    v1862 keeps fractional quantities (0.5) instead of rounding every graph row
    up to a full technical occurrence. This prevents two half-pair rows from
    being mistaken for two full pairs.
    """
    out = {}
    try:
        week = int(week)
    except Exception:
        return out
    for load in ((project or {}).get("loads") or []):
        if not isinstance(load, dict) or not load.get("id"):
            continue
        semantic = _graph_semantic_key_for_load_py(load)
        if not semantic:
            continue
        pairs = float(_load_graph_pairs_for_week_py(project or {}, load, week) or 0.0)
        if pairs <= 0:
            continue
        out[semantic] = float(out.get(semantic, 0.0)) + pairs
    return out


def _instance_graph_semantic_keys_py(project, inst):
    if not isinstance(inst, dict) or inst.get("isScheduleEvent") is True or inst.get("carryOverId"):
        return set()
    load_rows = [row for row in ((project or {}).get("loads") or []) if isinstance(row, dict)]
    loads = {str(row.get("id") or ""): row for row in load_rows if str(row.get("id") or "")}
    keys = set()

    # Resolve every explicit/inferred load id first.
    for load_id in _schedule_requirement_load_ids_py(project or {}, inst):
        load = loads.get(str(load_id))
        if load is not None:
            key = _graph_semantic_key_for_load_py(load)
            if key:
                keys.add(key)

    # Mirror the browser's legacy stream resolution. Older rows can carry only
    # streamGroupIds (without streamParticipants/loadId for every participant).
    # If a matching current graph load exists, include that participant bucket.
    parts = [{
        "groupId": inst.get("groupId"),
        "subgroup": int(inst.get("subgroup") or 0),
        "loadId": inst.get("loadId"),
    }]
    explicit = inst.get("streamParticipants") or []
    if isinstance(explicit, list) and explicit:
        parts.extend(x for x in explicit if isinstance(x, dict))
    else:
        for gid in (inst.get("streamGroupIds") or []):
            if gid:
                parts.append({"groupId": gid, "subgroup": int(inst.get("subgroup") or 0), "loadId": ""})

    fallback = set()
    for part in parts:
        lid = str(part.get("loadId") or "")
        if lid and lid in loads:
            key = _graph_semantic_key_for_load_py(loads[lid])
            if key:
                keys.add(key)
            continue
        gid = str(part.get("groupId") or inst.get("groupId") or "")
        if not gid:
            continue
        subgroup = int(part.get("subgroup") if part.get("subgroup") is not None else (inst.get("subgroup") or 0))
        matched = False
        for load in load_rows:
            if str(load.get("groupId") or "") != gid: continue
            if str(load.get("subjectId") or "") != str(inst.get("subjectId") or ""): continue
            if str(load.get("teacherId") or "") != str(inst.get("teacherId") or ""): continue
            if str(load.get("typeId") or "") != str(inst.get("typeId") or ""): continue
            if int(load.get("subgroup") or 0) != subgroup: continue
            key = _graph_semantic_key_for_load_py(load)
            if key:
                keys.add(key)
                matched = True
        if not matched:
            fallback.add("¦".join([
                gid,
                str(inst.get("subjectId") or ""),
                str(inst.get("teacherId") or ""),
                str(inst.get("typeId") or ""),
                str(subgroup),
            ]))

    # Use visible-field fallback only if no current load-backed semantic identity
    # could be recovered at all. This keeps stale stream metadata conservative.
    return keys if keys else fallback


def _graph_mismatch_occurrence_ids_py(project, week):
    """Return (outside, surplus) occurrence ids for one week.

    ``outside`` means every semantic bucket has zero graph capacity. ``surplus``
    means the requirement exists but the schedule contains more pair-units than
    the graph authorizes. Streams are conservative: a row is surplus only when
    every participant bucket can lose that row without creating graph debt.
    """
    try:
        week = int(week)
    except Exception:
        return set(), set()
    schedule = (project or {}).get("schedule") if isinstance((project or {}).get("schedule"), dict) else {}
    expected = _graph_expected_semantic_capacity_for_week_py(project or {}, week)
    unplaced = {str(x) for x in (schedule.get("unplaced") or []) if str(x)}
    occurrences = []
    remaining = {}
    outside = set()
    for row in (schedule.get("instances") or []):
        if not isinstance(row, dict) or row.get("isScheduleEvent") is True or row.get("carryOverId"):
            continue
        if week not in set(_instance_weeks_py((project or {}).get("config") or {}, row)):
            continue
        semantics = _instance_graph_semantic_keys_py(project or {}, row)
        if not semantics:
            continue
        buckets = sorted(set(semantics))
        sid = str(row.get("instId") or "")
        if not sid:
            continue
        unit = 0.5 if row.get("halfPair") else 1.0
        all_zero = bool(buckets) and all(float(expected.get(bucket, 0.0) or 0.0) <= 1e-9 for bucket in buckets)
        if all_zero:
            outside.add(sid)
        occurrences.append((row, sid, buckets, unit, all_zero))
        for bucket in buckets:
            remaining[bucket] = float(remaining.get(bucket, 0.0)) + unit

    def priority(item):
        row, sid, _, _, _ = item
        technical = bool(
            row.get("graphCoverageRepair") or row.get("visibilityInvariantRepair") or row.get("rawGraphDebtRecovery") or
            "__GRAPH_REPAIR_" in sid or "__FINAL_GRAPH_REPAIR_" in sid or "__VISIBILITY_REPAIR" in sid
        )
        is_unplaced = sid in unplaced or bool(row.get("forceUnplaced"))
        manual = bool(
            row.get("manualFormatOverride") or row.get("manualFormatEdited") or row.get("userManualSplit") or
            row.get("weeklyDetached") or row.get("restoredFromPublished") or row.get("restoredFromGroupUndo") or
            row.get("manualStreamMerge")
        )
        return (0 if technical else 1, 0 if is_unplaced else 1, 1 if manual else 0, sid)

    surplus = set()
    for row, sid, buckets, unit, all_zero in sorted(occurrences, key=priority):
        if all_zero:
            continue
        removable = all(
            float(remaining.get(bucket, 0.0)) - unit >= float(expected.get(bucket, 0.0) or 0.0) - 1e-9
            for bucket in buckets
        )
        if not removable:
            continue
        surplus.add(sid)
        for bucket in buckets:
            remaining[bucket] = max(0.0, float(remaining.get(bucket, 0.0)) - unit)
    return outside, surplus


def _surplus_offgraph_occurrence_ids_py(project, week):
    outside, surplus = _graph_mismatch_occurrence_ids_py(project or {}, week)
    return outside | surplus


def _instance_week_is_surplus_offgraph_py(project, inst, week):
    if not isinstance(inst, dict) or inst.get("isScheduleEvent") is True or inst.get("carryOverId"):
        return False
    try:
        week = int(week)
    except Exception:
        return False
    sid = str(inst.get("instId") or "")
    if not sid:
        return False
    return sid in _surplus_offgraph_occurrence_ids_py(project or {}, week)


def _instance_week_is_offgraph_py(project, inst, week):
    """Whether one concrete occurrence is provably outside the CURRENT graph.

    v1860 is deliberately conservative: unknown legacy metadata is never enough
    to accuse a pair. Only the semantic capacity allocator may mark it surplus.
    """
    if not isinstance(inst, dict) or inst.get("isScheduleEvent") is True or inst.get("carryOverId"):
        return False
    try:
        week = int(week)
    except Exception:
        return False
    if week not in set(_instance_weeks_py((project or {}).get("config") or {}, inst)):
        return False
    return _instance_week_is_surplus_offgraph_py(project or {}, inst, week)


def _instance_is_obsolete_offgraph_py(project, inst):
    """Whether every occurrence of ``inst`` is outside the CURRENT graph."""
    if not isinstance(inst, dict) or inst.get("isScheduleEvent") is True or inst.get("carryOverId"):
        return False
    # v1860: graphOutsideSnapshot is only provenance from an older graph state.
    # Current graph semantics always win if the requirement has since returned.
    weeks = sorted(set(_instance_weeks_py((project or {}).get("config") or {}, inst)))
    if not weeks:
        return False
    return all(_instance_week_is_offgraph_py(project or {}, inst, week) for week in weeks)


def _safe_offgraph_purge_ids_py(project, schedule, patch):
    """Return explicitly requested off-graph rows safe to purge.

    The request must be a pure removal for the id.  We independently verify the
    row against the authoritative graph, so stale cell revisions caused by graph
    projection cannot block an intentional cleanup.
    """
    requested = {str(x) for x in (patch.get("offGraphPurgeIds") or []) if str(x)}
    removed = {str(x) for x in (patch.get("instancesRemove") or []) if str(x)}
    requested &= removed
    if not requested:
        return set()
    instances_set = {str(x) for x in (patch.get("instancesSet") or {}).keys()}
    assignment_set = {str(x) for x in (patch.get("assignmentSet") or {}).keys()}
    locked_add = {str(x) for x in (patch.get("lockedAdd") or []) if str(x)}
    unplaced_add = {str(x) for x in (patch.get("unplacedAdd") or []) if str(x)}
    unsafe_rewrites = instances_set | assignment_set | locked_add | unplaced_add
    by_id = {
        str(row.get("instId") or ""): row
        for row in ((schedule or {}).get("instances") or [])
        if isinstance(row, dict) and str(row.get("instId") or "")
    }
    safe = set()
    for sid in requested:
        row = by_id.get(sid)
        if sid in unsafe_rewrites:
            continue
        if row is None:
            # Idempotent retry: already purged is success.
            safe.add(sid)
        elif _instance_is_obsolete_offgraph_py(project or {}, row):
            safe.add(sid)
    return safe


def _safe_offgraph_trim_ids_py(project, schedule, patch):
    """Validate an explicit removal of obsolete weeks from a recurring row.

    The browser may show one week of a multi-week instance as «Вне графика».
    Deleting that visible card must trim only that week, not create unplaced debt
    and not be rejected by a revision bump caused by graph projection.
    """
    requested = patch.get("offGraphTrimWeeksById") or {}
    if not isinstance(requested, dict) or not requested:
        return set()
    before_by_id = {
        str(row.get("instId") or ""): row
        for row in ((schedule or {}).get("instances") or [])
        if isinstance(row, dict) and str(row.get("instId") or "")
    }
    after_by_id = {str(k): v for k, v in (patch.get("instancesSet") or {}).items() if isinstance(v, dict)}
    removed_ids = {str(x) for x in (patch.get("instancesRemove") or []) if str(x)}
    safe = set()
    for sid, weeks_raw in requested.items():
        sid = str(sid or "")
        if not sid:
            continue
        before = before_by_id.get(sid)
        if not isinstance(before, dict):
            # Idempotent full-purge retry.
            if sid in removed_ids:
                safe.add(sid)
            continue
        try:
            trim_weeks = {int(w) for w in (weeks_raw or [])}
        except Exception:
            continue
        if not trim_weeks:
            continue
        before_weeks = set(_instance_weeks_py((project or {}).get("config") or {}, before))
        if not trim_weeks.issubset(before_weeks):
            continue
        if not all(_instance_week_is_offgraph_py(project or {}, before, w) for w in trim_weeks):
            continue
        expected_after = before_weeks - trim_weeks
        if sid in removed_ids:
            if expected_after:
                continue
            safe.add(sid)
            continue
        after = after_by_id.get(sid)
        if not isinstance(after, dict):
            continue
        after_weeks = set(_instance_weeks_py((project or {}).get("config") or {}, after))
        if after_weeks != expected_after:
            continue
        # Trimming weeks may only rewrite the instance definition/unplaced cache;
        # it must not move the remaining occurrences to another slot.
        if sid in {str(x) for x in (patch.get("assignmentRemove") or [])}:
            continue
        if sid in {str(x) for x in (patch.get("assignmentSet") or {}).keys()}:
            continue
        safe.add(sid)
    return safe


def _safe_explicit_manual_occurrence_delete_ids_py(project, schedule, patch):
    """Validate the red UI action that explicitly deletes one occurrence.

    v1862 deliberately does NOT re-run the graph heuristic here. The operator
    already selected a concrete card/week and confirmed deletion. Reclassifying
    it after a stream merge or graph refresh made the button appear to do
    nothing. Safety comes from validating the patch shape: it may only remove
    the requested weeks (or the whole row) and may not move/rewrite the lesson.
    """
    if not isinstance(patch, dict) or patch.get("explicitManualOccurrenceDelete") is not True or patch.get("manualOverride") is not True:
        return set()
    requested = patch.get("manualDeleteWeeksById") or {}
    if not isinstance(requested, dict) or not requested:
        return set()
    before_by_id = {
        str(row.get("instId") or ""): row
        for row in ((schedule or {}).get("instances") or [])
        if isinstance(row, dict) and str(row.get("instId") or "")
    }
    after_by_id = {str(k): v for k, v in (patch.get("instancesSet") or {}).items() if isinstance(v, dict)}
    removed_ids = {str(x) for x in (patch.get("instancesRemove") or []) if str(x)}
    assignment_set_ids = {str(x) for x in (patch.get("assignmentSet") or {}).keys()}
    assignment_remove_ids = {str(x) for x in (patch.get("assignmentRemove") or []) if str(x)}
    locked_add_ids = {str(x) for x in (patch.get("lockedAdd") or []) if str(x)}
    unplaced_add_ids = {str(x) for x in (patch.get("unplacedAdd") or []) if str(x)}
    safe = set()
    ignored = {"weekPattern", "customWeeks", "updatedAt", "manualGraphOverride"}
    cfg = (project or {}).get("config") or {}

    for raw_sid, raw_weeks in requested.items():
        sid = str(raw_sid or "")
        if not sid:
            continue
        try:
            delete_weeks = {int(w) for w in (raw_weeks or [])}
        except Exception:
            continue
        if not delete_weeks:
            continue
        before = before_by_id.get(sid)
        if before is None:
            # Idempotent retry after a successful full delete.
            if sid in removed_ids:
                safe.add(sid)
            continue
        before_weeks = set(_instance_weeks_py(cfg, before))
        if not delete_weeks.issubset(before_weeks):
            continue
        expected_after = before_weeks - delete_weeks

        if sid in removed_ids:
            # Full deletion only. It may clear assignment/lock/unplaced caches,
            # but must not recreate the same id or add it elsewhere.
            if expected_after or sid in after_by_id or sid in assignment_set_ids or sid in locked_add_ids or sid in unplaced_add_ids:
                continue
            safe.add(sid)
            continue

        after = after_by_id.get(sid)
        if not isinstance(after, dict):
            continue
        if set(_instance_weeks_py(cfg, after)) != expected_after:
            continue
        # A partial week trim keeps the same placement for all surviving weeks.
        if sid in assignment_set_ids or sid in assignment_remove_ids or sid in locked_add_ids or sid in unplaced_add_ids:
            continue
        bcmp = {k: v for k, v in before.items() if k not in ignored}
        acmp = {k: v for k, v in after.items() if k not in ignored}
        if bcmp != acmp:
            continue
        safe.add(sid)
    return safe


def _schedule_patch_concurrent_conflicts(project, schedule, patch):
    """Compatibility guard for clients without per-cell revisions.

    v1827: baseRevisionById is the authoritative concurrency guard whenever it
    is present for an instId.  Do NOT run the older full-cell ``baseById``
    comparison for that same id: the browser may legitimately enrich untouched
    schedule rows from Loads (teacher/subgroup/stream display metadata), while
    the durable server row is still byte-for-byte different.  Running both
    guards produced false 409s on the *next* manual stream merge (e.g. adding a
    third group after 46+48 had already been joined).

    Different instIds remain independently mergeable.  For older clients or ids
    that have no revision token we still keep the exact-state compatibility
    check below.
    """
    base = patch.get("baseById") or {}
    if not isinstance(base, dict) or not base:
        return []
    revision_guarded = {str(x) for x in (patch.get("baseRevisionById") or {}).keys() if str(x)}
    safe_offgraph_purge = _safe_offgraph_purge_ids_py(project, schedule, patch)
    safe_offgraph_trim = _safe_offgraph_trim_ids_py(project, schedule, patch)
    safe_explicit_delete = _safe_explicit_manual_occurrence_delete_ids_py(project, schedule, patch)
    safe_offgraph_ops = safe_offgraph_purge | safe_offgraph_trim | safe_explicit_delete
    conflicts = []
    for sid, expected in base.items():
        sid = str(sid or "")
        if not sid or sid in revision_guarded or sid in safe_offgraph_ops or not isinstance(expected, dict):
            continue
        current = _schedule_cell_state_py(schedule, sid)
        normalized_expected = {
            "instance": expected.get("instance") if isinstance(expected.get("instance"), dict) else None,
            "assignment": expected.get("assignment") if isinstance(expected.get("assignment"), dict) else None,
            "locked": bool(expected.get("locked")),
            "unplaced": bool(expected.get("unplaced")),
        }
        if current != normalized_expected and not _schedule_patch_cell_target_already_applied(schedule, patch, sid):
            conflicts.append(sid)
    return conflicts


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


def _current_semester_week_py(config):
    """Current 1-based semester week in Moscow time."""
    from datetime import date, timedelta, datetime
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    except Exception:
        today = date.today()
    try:
        start = date.fromisoformat(str((config or {}).get("semesterStart") or ""))
    except Exception:
        return 1
    monday = start - timedelta(days=start.weekday())
    if today < monday:
        return 1
    return ((today - monday).days // 7) + 1



def _moscow_now_py():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Moscow"))
    except Exception:
        return datetime.now()


def _placed_started_today_ids_py(project, schedule, only_ids=None):
    """Placed lessons/events whose concrete occurrence has already started today.

    Once a lesson has started, a deploy/rebase/stale browser may not move or
    delete it. Future slots in the current week remain editable. ``only_ids`` is
    a hot-path optimization for cell patches: a one-cell drag need not inspect
    every assignment in the semester.
    """
    schedule = schedule if isinstance(schedule, dict) else {}
    cfg = (project or {}).get("config") or {}
    now = _moscow_now_py()
    current_week = _current_semester_week_py(cfg)
    today_day = now.weekday()  # Monday=0
    now_minutes = now.hour * 60 + now.minute

    by_id = {
        str(x.get("instId") or ""): x
        for x in (schedule.get("instances") or [])
        if isinstance(x, dict) and str(x.get("instId") or "")
    }
    out = set()
    only = {str(x) for x in (only_ids or []) if str(x)} if only_ids is not None else None
    assignments = schedule.get("assignment") or {}
    source_items = ((sid, assignments.get(sid)) for sid in only) if only is not None else assignments.items()
    for sid, assignment in source_items:
        sid = str(sid)
        if not isinstance(assignment, dict):
            continue
        inst = by_id.get(sid)
        if not inst:
            continue
        try:
            if int(assignment.get("day", -1)) != int(today_day):
                continue
            if int(current_week) not in {int(w) for w in _instance_weeks_py(cfg, inst)}:
                continue
        except Exception:
            continue

        start_min = None
        if inst.get("isScheduleEvent") is True:
            start_min = _clock_minutes_py(inst.get("eventStartTime"))
        else:
            bounds = _period_clock_range_py(cfg, assignment.get("period"))
            if bounds:
                start_min = bounds[0]
        if start_min is not None and now_minutes >= int(start_min):
            out.add(sid)
    return out


def _placed_immutable_ids_py(project, schedule):
    return _placed_past_ids_py(project, schedule) | _placed_started_today_ids_py(project, schedule)


def _instance_has_past_week_py(project, inst):
    if not isinstance(inst, dict):
        return False
    current = _current_semester_week_py((project or {}).get("config") or {})
    # v1635: первая неделя — специальное ручное архивное исключение.
    # Она не входит в hard immutable set, чтобы можно было восстановить
    # фактически проведённые занятия после повреждения расписания.
    return any(
        int(w) < int(current) and int(w) != 1
        for w in _instance_weeks_py((project or {}).get("config") or {}, inst)
    )


def _placed_past_ids_py(project, schedule, only_ids=None):
    """Only PLACED past lessons are immutable.

    Unplaced debts from past weeks intentionally remain editable so they can be
    carried forward to a current/future week. ``only_ids`` keeps atomic cell
    patches O(touched cells) instead of rescanning the whole assignment map.
    """
    schedule = schedule if isinstance(schedule, dict) else {}
    by_id = {
        str(x.get("instId") or ""): x
        for x in (schedule.get("instances") or [])
        if isinstance(x, dict) and str(x.get("instId") or "")
    }
    assignment = schedule.get("assignment") or {}
    out = set()
    only = {str(x) for x in (only_ids or []) if str(x)} if only_ids is not None else None
    source_items = ((sid, assignment.get(sid)) for sid in only) if only is not None else assignment.items()
    for sid, a in source_items:
        sid = str(sid)
        if not a:
            continue
        inst = by_id.get(sid)
        if inst and _instance_has_past_week_py(project, inst):
            out.add(sid)
    return out


def _preserve_placed_past_schedule_py(project, remote_schedule, candidate_schedule, allow_trim_ids=None):
    """Hard server archive guard for already placed past lessons.

    The canonical server copy wins for every placed instance touching a completed
    semester week. Unplaced past requirements are deliberately NOT frozen.
    """
    remote_schedule = remote_schedule if isinstance(remote_schedule, dict) else {}
    candidate_schedule = candidate_schedule if isinstance(candidate_schedule, dict) else {}
    # v1628: preserve both completed-week lessons and lessons that have
    # already started today. This prevents an in-progress class from vanishing
    # during deploy/rebase.
    frozen = _placed_immutable_ids_py(project, remote_schedule)
    allow_trim_ids = {str(x) for x in (allow_trim_ids or []) if str(x)}
    if not frozen:
        return candidate_schedule

    remote_rows = {
        str(x.get("instId") or ""): dict(x)
        for x in (remote_schedule.get("instances") or [])
        if isinstance(x, dict) and str(x.get("instId") or "")
    }
    cand_rows = {
        str(x.get("instId") or ""): dict(x)
        for x in (candidate_schedule.get("instances") or [])
        if isinstance(x, dict) and str(x.get("instId") or "")
    }
    for sid in frozen:
        if sid in allow_trim_ids:
            continue
        if sid in remote_rows:
            cand_rows[sid] = remote_rows[sid]

    ra = dict(remote_schedule.get("assignment") or {})
    ca = dict(candidate_schedule.get("assignment") or {})
    for sid in frozen:
        if sid in allow_trim_ids:
            continue
        if sid in ra:
            ca[sid] = dict(ra[sid])
        else:
            ca.pop(sid, None)

    rl = {str(x) for x in (remote_schedule.get("locked") or []) if str(x)}
    cl = {str(x) for x in (candidate_schedule.get("locked") or []) if str(x)}
    ru = {str(x) for x in (remote_schedule.get("unplaced") or []) if str(x)}
    cu = {str(x) for x in (candidate_schedule.get("unplaced") or []) if str(x)}

    for sid in frozen:
        if sid in allow_trim_ids:
            continue
        cl.add(sid)          # system lock
        cu.discard(sid)      # a placed archived lesson cannot be "unplaced"
        if sid not in ra and sid in ru:
            cu.add(sid)

    out = dict(candidate_schedule)
    out["instances"] = list(cand_rows.values())
    out["assignment"] = ca
    out["locked"] = sorted(cl)
    out["unplaced"] = sorted(x for x in cu if x not in ca)
    return out


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



# v1718: canonical server-side policy for deliberate MANUAL timetable exceptions.
# Auto-scheduling never receives these allowances. The server validates the actual
# room/teacher/subject relationship instead of blindly trusting a client flag.
_MANUAL_GYM_RE = re.compile(r"спорт|физкульт|\bgym\b", re.I)

def _manual_policy_room_ids(a):
    if not isinstance(a, dict): return []
    return [str(x) for x in (a.get("roomId"), a.get("extraRoomId")) if x not in (None, "")]

def _manual_policy_is_gym(project, room_id):
    rid = str(room_id or "")
    if not rid: return False
    room = next((x for x in (project.get("rooms") or []) if isinstance(x, dict) and str(x.get("id") or "") == rid), None)
    if not room: return False
    tid = str(room.get("typeId") or "")
    rtype = next((x for x in (project.get("roomTypes") or []) if isinstance(x, dict) and str(x.get("id") or "") == tid), None)
    text = "%s %s" % ((rtype or {}).get("name") or "", room.get("name") or "")
    return bool(_MANUAL_GYM_RE.search(text))

def _manual_policy_subject_id(project, inst):
    if not isinstance(inst, dict): return ""
    sid = str(inst.get("subjectId") or "")
    if sid: return sid
    lid = str(inst.get("loadId") or "")
    if not lid: return ""
    load = next((x for x in (project.get("loads") or []) if isinstance(x, dict) and str(x.get("id") or "") == lid), None)
    return str((load or {}).get("subjectId") or "")

def _instance_group_subgroups_authoritative_py(project, inst, group_id):
    """Resolve concrete subgroup numbers from loadId first, instance metadata second."""
    gid = str(group_id or "")
    loads = {str(x.get("id") or ""): x for x in (project.get("loads") or []) if isinstance(x, dict)}
    authoritative = set()
    fallback = set()
    if not isinstance(inst, dict): return set()
    parts = list(inst.get("streamParticipants") or [])
    if str(inst.get("groupId") or "") == gid:
        parts = [{"groupId": gid, "subgroup": inst.get("subgroup"), "loadId": inst.get("loadId")}, *parts]
    for part in parts:
        if not isinstance(part, dict) or str(part.get("groupId") or "") != gid: continue
        load = loads.get(str(part.get("loadId") or ""))
        if load and str(load.get("groupId") or "") == gid:
            try: authoritative.add(int(load.get("subgroup") or 0))
            except Exception: authoritative.add(0)
        else:
            try: fallback.add(int(part.get("subgroup") or 0))
            except Exception: fallback.add(0)
    if authoritative: return authoritative
    concrete = {x for x in fallback if x > 0}
    return concrete or fallback or _instance_group_subgroups_py(inst, gid)


def _same_teacher_sibling_subgroups_compatible_py(project, a, b):
    """One teacher may teach two concrete subgroups of one group in one slot.

    Works for both in-person and remote lessons. To avoid accidentally allowing
    two genuinely different lessons, subject, lesson type and format must match.
    """
    if not isinstance(a, dict) or not isinstance(b, dict): return False
    if a.get("isVacancyTeacher") or b.get("isVacancyTeacher"): return False
    ta, tb = str(a.get("teacherId") or ""), str(b.get("teacherId") or "")
    if not ta or ta != tb: return False
    if _manual_policy_subject_id(project, a) != _manual_policy_subject_id(project, b): return False
    loads = {str(x.get("id") or ""): x for x in (project.get("loads") or []) if isinstance(x, dict)}
    la, lb = loads.get(str(a.get("loadId") or "")), loads.get(str(b.get("loadId") or ""))
    type_a = str((la or {}).get("typeId") or a.get("typeId") or "")
    type_b = str((lb or {}).get("typeId") or b.get("typeId") or "")
    if type_a != type_b: return False
    if str(a.get("format") or "inperson") != str(b.get("format") or "inperson"): return False
    ga, gb = _schedule_instance_group_ids(a), _schedule_instance_group_ids(b)
    if len(ga) != 1 or len(gb) != 1 or ga != gb: return False
    gid = next(iter(ga))
    sa = _instance_group_subgroups_authoritative_py(project, a, gid)
    sb = _instance_group_subgroups_authoritative_py(project, b, gid)
    return bool(sa and sb and 0 not in sa and 0 not in sb and sa.isdisjoint(sb))


def _manual_policy_teacher_overlap_allowed(project, inst, other, a, oa):
    """Validate a manual same-teacher overlap from real data, not just a flag."""
    if not isinstance(inst, dict) or not isinstance(other, dict): return False

    # v1726: temporary overlap used while the operator assembles a manual stream.
    # It is accepted only for genuinely stream-compatible lessons: same teacher,
    # subject, lesson type and format, with disjoint group/subgroup participants.
    if (a or {}).get("manualPotentialStream") or (oa or {}).get("manualPotentialStream"):
        ta, tb = str(inst.get("teacherId") or ""), str(other.get("teacherId") or "")
        if ta and ta == tb and not inst.get("isVacancyTeacher") and not other.get("isVacancyTeacher"):
            same_subject = _manual_policy_subject_id(project, inst) and _manual_policy_subject_id(project, inst) == _manual_policy_subject_id(project, other)
            loads = {str(x.get("id") or ""): x for x in (project.get("loads") or []) if isinstance(x, dict)}
            li, lo = loads.get(str(inst.get("loadId") or "")), loads.get(str(other.get("loadId") or ""))
            type_i = str((li or {}).get("typeId") or inst.get("typeId") or "")
            type_o = str((lo or {}).get("typeId") or other.get("typeId") or "")
            same_format = str(inst.get("format") or "inperson") == str(other.get("format") or "inperson")
            disjoint = True
            for gid in _schedule_instance_group_ids(inst).intersection(_schedule_instance_group_ids(other)):
                si = _instance_group_subgroups_authoritative_py(project, inst, gid)
                so = _instance_group_subgroups_authoritative_py(project, other, gid)
                # subgroup 0 means whole group and therefore overlaps anything.
                if (0 in si) or (0 in so) or not si.isdisjoint(so):
                    disjoint = False
                    break
            if same_subject and type_i == type_o and same_format and disjoint:
                if same_format and str(inst.get("format") or "inperson") == "remote":
                    return True
                ra, rb = _manual_policy_room_ids(a), _manual_policy_room_ids(oa)
                if set(ra).intersection(rb):
                    return True

    if str(inst.get("format") or "") == "remote" or str(other.get("format") or "") == "remote": return False
    ra, rb = _manual_policy_room_ids(a), _manual_policy_room_ids(oa)
    shared_rooms = set(ra).intersection(rb)

    # Teacher intentionally walks between two DIFFERENT rooms.
    if (a or {}).get("manualTeacherMultiRoom") or (oa or {}).get("manualTeacherMultiRoom"):
        if ra and rb and not shared_rooms:
            return True

    # Two different whole groups, same teacher + same subject + same room.
    if (a or {}).get("manualSameSubjectTeacherSameRoom") or (oa or {}).get("manualSameSubjectTeacherSameRoom"):
        if shared_rooms and _manual_policy_subject_id(project, inst) and _manual_policy_subject_id(project, inst) == _manual_policy_subject_id(project, other):
            return True

    # Gym sharing: two different groups can intentionally share one sports hall,
    # whether the teacher is the same or different. Client additionally enforces
    # the canonical maximum of two concurrent groups.
    if (a or {}).get("manualMultiGroupRoom") or (oa or {}).get("manualMultiGroupRoom"):
        if any(_manual_policy_is_gym(project, rid) for rid in shared_rooms):
            return True
    return False


def _schedule_requirement_layer_py(inst):
    if not isinstance(inst, dict): return "1"
    if inst.get("halfPair"): return "H"
    iid = str(inst.get("instId") or "")
    m = re.search(r"__L(\d+)$", iid)
    if m: return m.group(1)
    m = re.search(r"__(\d+)$", iid)
    if m: return str((int(m.group(1) or 0)) + 1)
    m = re.search(r"__(?:LECTURE_STREAM|PRACTICAL_STREAM)_\d+_([^_]+)_", iid)
    if m: return m.group(1)
    m = re.search(r"__MANDO_([^_]+)$", iid)
    if m: return m.group(1)
    m = re.search(r"__GRAPH_REPAIR_\d+_([^_]+)$", iid)
    if m: return m.group(1)
    if "__GRAPH_DELTA_" in iid:
        src = str(inst.get("graphDeltaFromInstId") or "")
        lm = re.search(r"__L(\d+)$", src)
        if lm: return lm.group(1)
    return "1"


def _schedule_requirement_load_ids_py(project, inst):
    if not isinstance(inst, dict): return set()
    out = {str(inst.get("loadId") or "")} if str(inst.get("loadId") or "") else set()
    loads = [x for x in (project.get("loads") or []) if isinstance(x, dict)]
    for part in (inst.get("streamParticipants") or []):
        if not isinstance(part, dict): continue
        lid = str(part.get("loadId") or "")
        if lid:
            out.add(lid); continue
        gid = str(part.get("groupId") or "")
        subgroup = int(part.get("subgroup") or 0)
        for load in loads:
            if str(load.get("groupId") or "") != gid: continue
            if str(load.get("subjectId") or "") != str(inst.get("subjectId") or ""): continue
            if str(load.get("teacherId") or "") != str(inst.get("teacherId") or ""): continue
            if str(load.get("typeId") or "") != str(inst.get("typeId") or ""): continue
            if int(load.get("subgroup") or 0) != subgroup: continue
            if load.get("id"): out.add(str(load.get("id")))
    return out


def _load_has_graph_layer_in_weeks_py(load, layer, weeks):
    """Whether a load actually owns this graph layer in any supplied week.

    Legacy/non-weekly loads are kept conservatively.  For weeklyPairs the graph
    is authoritative: an old stream participant must not occupy a week/layer that
    was removed from that participant's graph row.
    """
    if not isinstance(load, dict):
        return True
    if str(load.get("weekPattern") or "perWeek") != "perWeek":
        return True
    weekly = load.get("weeklyPairs") or {}
    if not isinstance(weekly, dict) or not weekly:
        return True
    week_set = {int(w) for w in (weeks or []) if str(w).lstrip("-").isdigit()}
    if not week_set:
        return True
    layer = str(layer or "1")
    if layer.startswith("CARRY_"):
        return True
    for week in week_set:
        try:
            count = max(0.0, float(weekly.get(str(week), weekly.get(week, 0)) or 0))
        except Exception:
            count = 0.0
        whole = int(count + 1e-9)
        if layer == "H":
            if count - whole >= 0.49:
                return True
        else:
            try:
                n = int(layer)
            except Exception:
                n = 1
            if n > 0 and whole >= n:
                return True
    return False


def _schedule_instance_group_ids_for_weeks_py(project, inst, weeks):
    """Graph-aware membership for overlap validation.

    Old persisted stream cards can contain a peer whose weeklyPairs no longer
    includes the current week/layer.  Treating that peer as present creates
    invisible/fantom group conflicts.
    """
    if not isinstance(inst, dict):
        return set()
    loads = {str(x.get("id") or ""): x for x in (project.get("loads") or []) if isinstance(x, dict)}
    layer = _schedule_requirement_layer_py(inst)
    parts = [{
        "groupId": inst.get("groupId"),
        "loadId": inst.get("loadId"),
    }]
    explicit = inst.get("streamParticipants") or []
    if isinstance(explicit, list):
        parts.extend(x for x in explicit if isinstance(x, dict))

    any_load_backed = False
    out = set()
    for part in parts:
        gid = str(part.get("groupId") or "")
        if not gid:
            continue
        lid = str(part.get("loadId") or "")
        load = loads.get(lid) if lid else None
        if load is not None:
            any_load_backed = True
            if not _load_has_graph_layer_in_weeks_py(load, layer, weeks):
                continue
        out.add(gid)

    # Legacy rows without detailed load-backed participants normally retain the
    # old membership semantics. Transfer/manual-stream-break fragments are the
    # exception: their current scoped primary/streamParticipants are deliberate,
    # while old groupIds can be pre-split garbage from v1888. Returning `out`
    # prevents the same false cross-group overlap/snap-back even when a legacy
    # fragment temporarily has no recoverable loadId.
    if not any_load_backed:
        transfer_scoped = bool(
            inst.get("manualStreamBreak") or inst.get("transferSubgroupDetached") or
            inst.get("transferSubgroupRetained") or inst.get("transferredLoadRemainder")
        )
        return out if transfer_scoped and out else _schedule_instance_group_ids(inst)
    return out


def _schedule_requirement_keys_py(project, inst):
    layer = _schedule_requirement_layer_py(inst)
    weeks = _instance_weeks_py(project.get("config") or {}, inst)
    return {f"{lid}|{week}|{layer}" for lid in _schedule_requirement_load_ids_py(project, inst) for week in weeks}


def _is_technical_graph_tail_py(inst):
    if not isinstance(inst, dict): return False
    iid = str(inst.get("instId") or "")
    return bool(inst.get("graphCoverageRepair")) or "__GRAPH_REPAIR_" in iid or "__GRAPH_DELTA_" in iid

def _clock_minutes_py(value):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(value or ""))
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    if h < 0 or h > 23 or minute < 0 or minute > 59:
        return None
    return h * 60 + minute


def _period_clock_range_py(config, period):
    try:
        raw = ((config or {}).get("periodTimes") or [])[int(period)]
    except Exception:
        return None
    parts = re.findall(r"(\d{1,2}:\d{2})", str(raw or ""))
    if len(parts) < 2:
        return None
    start, end = _clock_minutes_py(parts[0]), _clock_minutes_py(parts[1])
    if start is None or end is None:
        return None
    return start, end


def _group_period_clock_range_py(project, group_id, day, period, week=None):
    project = project if isinstance(project, dict) else {}
    config = project.get("config") or {}
    group = next((g for g in (project.get("groups") or [])
                  if isinstance(g, dict) and str(g.get("id") or "") == str(group_id or "")), None)
    raw = None
    if group:
        parity = None
        try:
            parity = "odd" if int(week) % 2 == 1 else "even" if int(week) > 0 else None
        except Exception:
            parity = None
        if parity:
            parity_map = (group.get("dayPeriodTimesByParity") or {}).get(parity) or {}
            arr = parity_map.get(str(day), parity_map.get(day))
            if isinstance(arr, list) and any(str(x or "").strip() for x in arr):
                try: raw = arr[int(period)]
                except Exception: raw = None
        if not raw:
            base_map = group.get("dayPeriodTimes") or {}
            arr = base_map.get(str(day), base_map.get(day))
            if isinstance(arr, list) and any(str(x or "").strip() for x in arr):
                try: raw = arr[int(period)]
                except Exception: raw = None
    if not raw:
        try: raw = (config.get("periodTimes") or [])[int(period)]
        except Exception: raw = None
    parts = re.findall(r"(\d{1,2}:\d{2})", str(raw or ""))
    if len(parts) < 2:
        return None
    start, end = _clock_minutes_py(parts[0]), _clock_minutes_py(parts[1])
    if start is None or end is None:
        return None
    return start, end


def _event_starts_inside_period_py(project, event_inst, lesson_inst, period, day, shared_weeks=None):
    if not isinstance(event_inst, dict) or event_inst.get("isScheduleEvent") is not True:
        return False

    # v1729: the event editor persists which timetable rows contain the actual
    # START of the event inside an academic pair. Accept that explicit marker
    # before recomputing bell times. With per-group bell schedules one event can
    # occupy several row numbers; recomputing from only one row used to make the
    # server reject a client-valid lesson+event cell and roll the lesson back.
    explicit = event_inst.get("eventStartOverlapPeriods") or []
    try:
        if int(period) in {int(x) for x in explicit}:
            return True
    except Exception:
        pass

    start = _clock_minutes_py(event_inst.get("eventStartTime"))
    if start is None:
        return False
    config = (project or {}).get("config") or {}
    bounds = _period_clock_range_py(config, period)
    if bounds and bounds[0] < start < bounds[1]:
        return True

    # v1640: use the actual group/day/parity bell schedule. Otherwise an event
    # ten minutes before the group's real end could be rejected because the
    # global config.periodTimes ends earlier.
    lesson_groups = _schedule_instance_group_ids(lesson_inst) if isinstance(lesson_inst, dict) else set()
    event_groups = _schedule_instance_group_ids(event_inst)
    shared_groups = set(lesson_groups).intersection(event_groups) or set(lesson_groups)
    weeks = sorted(set(int(w) for w in (shared_weeks or []) if str(w).lstrip("-").isdigit())) or [None]
    for gid in shared_groups:
        for week in weeks:
            rng = _group_period_clock_range_py(project, gid, day, period, week)
            if rng and rng[0] < start < rng[1]:
                return True
    return False


def _midpair_event_soft_overlap_py(project, a_inst, b_inst, period, day, shared_weeks=None):
    """Group/teacher may leave an academic pair when event begins mid-pair."""
    if isinstance(a_inst, dict) and a_inst.get("isScheduleEvent") is True:
        return _event_starts_inside_period_py(project, a_inst, b_inst, period, day, shared_weeks)
    if isinstance(b_inst, dict) and b_inst.get("isScheduleEvent") is True:
        return _event_starts_inside_period_py(project, b_inst, a_inst, period, day, shared_weeks)
    return False


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

    # v1600: the client deliberately suppresses obsolete graph repair/delta tails
    # when the same load/week/layer is already covered by a real assigned card.
    # The server must use the same semantic visibility rule; otherwise an invisible
    # technical tail can block an actually empty cell with a false group conflict.
    canonical_requirement_keys = set()
    for sid in active_ids:
        inst = by_id[sid]
        if not _is_technical_graph_tail_py(inst):
            canonical_requirement_keys.update(_schedule_requirement_keys_py(project, inst))
    redundant_technical_ids = set()
    for sid in active_ids:
        inst = by_id[sid]
        if not _is_technical_graph_tail_py(inst):
            continue
        keys = _schedule_requirement_keys_py(project, inst)
        if keys and keys.issubset(canonical_requirement_keys):
            redundant_technical_ids.add(sid)
    if redundant_technical_ids:
        active_ids = [sid for sid in active_ids if sid not in redundant_technical_ids]

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
    # v1595: build the load index once per validation pass. v1594's fallback for
    # legacy perWeek instances used next(...loads...) for every instance whose
    # weeklyPairs were not embedded, which becomes noticeable on large projects.
    load_by_id = {str(x.get("id") or ""): x for x in (project.get("loads") or [])
                  if isinstance(x, dict) and str(x.get("id") or "")}
    total_weeks = _total_semester_weeks_py(cfg)
    weeks_cache = {}
    groups_cache = {}
    subgroup_cache = {}
    def weeks(sid):
        if sid not in weeks_cache:
            inst = by_id[sid]
            # v1604: conflict validation uses the SAME effective occurrences as
            # the weekly grid, not only weekPattern/customWeeks. Holidays,
            # no-class calendar exceptions and group practice remove an occurrence
            # from both rendering and server occupancy.
            weeks_cache[sid] = set(
                _instance_visible_weeks_for_assignment_py(project, inst, assignment.get(sid) or {})
            )
        return weeks_cache[sid]
    def groups(sid):
        if sid not in groups_cache:
            groups_cache[sid] = _schedule_instance_group_ids(by_id[sid])
        return groups_cache[sid]
    def subgroups(sid, gid):
        key = (sid, str(gid))
        if key not in subgroup_cache:
            # v1598: use the source load as the authority. A schedule instance can
            # temporarily miss subgroup metadata after graph/manual splitting;
            # treating that as subgroup=0 caused false whole-group conflicts.
            subgroup_cache[key] = _instance_group_subgroups_authoritative_py(project, by_id[sid], gid)
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
        wb = weeks(oid)
        shared_weeks = wa.intersection(wb)
        if not wa or not shared_weeks:
            continue
        inst, other = by_id[sid], by_id[oid]
        a, oa = assignment[sid], assignment[oid]
        slot_period = int(a.get("period", -1)) if isinstance(a, dict) else -1
        slot_day = int(a.get("day", -1)) if isinstance(a, dict) else -1
        midpair_event_soft = _midpair_event_soft_overlap_py(project, inst, other, slot_period, slot_day, shared_weeks)

        # v1883: manualForceOverlap may soften a TEACHER collision only.
        # It must never hide a collision of the same group/subgroup. v1823 put
        # this `continue` before group validation, which let a manual drag save
        # two lessons of one group in the same slot. Keep the flag for legacy
        # manual teacher-overlap workflows, but validate participants first.
        manual_force_overlap = bool((a or {}).get("manualForceOverlap")) or bool((oa or {}).get("manualForceOverlap"))

        # v1598: split/custom-week cards generated from the same load are fragments
        # of one logical lesson. During a hot manual edit stale and fresh fragments
        # can coexist for a request; they must not block each other as if they were
        # two independent lessons.
        load_a = str(inst.get("loadId") or "")
        load_b = str(other.get("loadId") or "")
        if load_a and load_a == load_b:
            same_group = _schedule_instance_group_ids(inst) == _schedule_instance_group_ids(other)
            same_teacher = str(inst.get("teacherId") or "") == str(other.get("teacherId") or "")
            same_layer = _schedule_requirement_layer_py(inst) == _schedule_requirement_layer_py(other)
            # v1636: only fragments of the SAME graph requirement may overlap
            # temporarily. L1 and L2 of one load are two real lessons and must
            # never be accepted in the same physical slot.
            if same_group and same_teacher and same_layer:
                continue
        groups_a = _schedule_instance_group_ids_for_weeks_py(project, inst, shared_weeks)
        groups_b = _schedule_instance_group_ids_for_weeks_py(project, other, shared_weeks)
        # A stale stream occurrence with no active graph participant in these
        # weeks must not block either the group or the teacher.
        if not groups_a or not groups_b:
            continue
        group_conflict = False
        for gid in groups_a.intersection(groups_b):
            s1 = subgroups(sid, gid); s2 = subgroups(oid, gid)
            allowed = bool(s1 and s2 and 0 not in s1 and 0 not in s2 and s1.isdisjoint(s2))
            # v1627: если мероприятие начинается внутри пары, группа имеет право
            # стоять на полной учебной паре до этого времени и затем уйти.
            if not allowed and not midpair_event_soft:
                out.append((sid, oid, gid)); group_conflict = True; break
        if group_conflict:
            continue

        # Broad manual override is retained only for deliberate teacher overlap.
        # Group/subgroup occupancy above has already been validated strictly.
        if manual_force_overlap:
            continue

        t1, t2 = str(inst.get("teacherId") or ""), str(other.get("teacherId") or "")
        vacancy = bool(inst.get("isVacancyTeacher")) or bool(other.get("isVacancyTeacher"))
        manual_overlap_allowed = _manual_policy_teacher_overlap_allowed(project, inst, other, a, oa)
        if t1 and t1 == t2 and not vacancy and not manual_overlap_allowed and not midpair_event_soft:
            # v1592: deliberate exception is exact and format-agnostic: the same
            # teacher may conduct subgroup 1 + subgroup 2 of ONE group as one
            # lesson, both in-person and remotely. Different subjects/types remain
            # a real double booking.
            if not _same_teacher_sibling_subgroups_compatible_py(project, inst, other):
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
    if not _schedule_client_build_ok():
        return _stale_schedule_client_response()
    """Lightweight atomic save for Graphs.

    v1780 hard boundary: this endpoint may change graph-owned load fields only.
    It never mutates timetable instances/assignment, even if a stale browser sends
    the legacy schedulePatch field.
    """
    body = request.get_json(force=True, silent=True) or {}
    changes = body.get("changes") or []
    history_append = body.get("historyAppend") or []
    meta = body.get("meta") or {}
    request_id = str((meta or {}).get("requestId") or "").strip()
    schedule_patch = None
    if not isinstance(changes, list) or not isinstance(history_append, list):
        return jsonify({"error": "Неверный формат быстрого сохранения графика"}), 400
    graph_fields = ("weeklyPairs", "weekPattern", "customWeeks", "graphMode", "graphLocked")
    with _lock:
        if request_id:
            now_mono = time.monotonic()
            for rid, rec in list(_graph_patch_results.items()):
                if now_mono - float(rec.get("at", now_mono)) > GRAPH_PATCH_REQUEST_TTL_SECONDS:
                    _graph_patch_results.pop(rid, None)
            cached = _graph_patch_results.get(request_id)
            if cached is not None:
                _graph_patch_results.move_to_end(request_id)
                return jsonify(cached.get("body") or {})
        if not _storage_exists():
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
        # v1780: no Graph -> Schedule write exists here by design.
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
        _maybe_periodic_backup_async()
        save_store(store, backup=False, durable=False, refresh_public=False, refresh_status=False)
        response_body = {"ok": True, "token": sync_meta["saveId"], "rows": returned_rows, "schedule": None, "_syncMeta": sync_meta}
        if request_id:
            _graph_patch_results[request_id] = {"at": time.monotonic(), "body": response_body}
            _graph_patch_results.move_to_end(request_id)
            while len(_graph_patch_results) > 2000:
                _graph_patch_results.popitem(last=False)
    return jsonify(response_body)


@app.post("/api/storage-graph-merge/<key>")
def storage_graph_merge(key):
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных графика"}), 400
    with _lock:
        if not _storage_exists():
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
        store[key] = merged
        # v1610: draft autosave must not block on a multi-MB backup nor touch the
        # public contour. SQLite NORMAL is still transactional, while avoiding
        # an fsync/public-status penalty on every small editor change.
        _maybe_periodic_backup_async()
        save_store(store, backup=False, durable=False, refresh_public=False, refresh_status=False)
    return jsonify({"ok": True, "token": str(merged["_syncMeta"]["saveId"]), "value": merged})



def _schedule_patch_request_cleanup_locked(now=None):
    now = time.monotonic() if now is None else now
    expired = []
    for rid, rec in list(_schedule_patch_requests.items()):
        finished = rec.get("finished_at")
        created = rec.get("created_at", now)
        stamp = finished if finished is not None else created
        if now - stamp > SCHEDULE_PATCH_REQUEST_TTL_SECONDS:
            expired.append(rid)
    for rid in expired:
        _schedule_patch_requests.pop(rid, None)
    while len(_schedule_patch_requests) > 2000:
        _schedule_patch_requests.popitem(last=False)


def _schedule_patch_wait_result(record, timeout=45.0):
    if not record.get("event").wait(timeout=timeout):
        return {"status": 503, "body": {"error": "Сервер не успел подтвердить сохранение ячейки"}}
    return record.get("result") or {"status": 500, "body": {"error": "Неизвестная ошибка сохранения"}}


# v1565: immediate atomic timetable-cell patch.  This endpoint intentionally
# updates only explicitly named instance/assignment/locked/unplaced entries under the
# storage lock.  It never replaces the whole schedule object.
@app.post("/api/storage-schedule-patch/<key>")
def storage_schedule_patch(key):
    if not _schedule_client_build_ok():
        return _stale_schedule_client_response()
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
    # v1755: statistics are derived presentation data, not a concurrency unit.
    # A stale browser moving lesson B must never overwrite totals computed by a
    # colleague who just moved lesson A. Keep semantic cell data authoritative.
    patch = dict(patch)
    patch.pop("stats", None)

    touched_ids = set(map(str, (patch.get("assignmentSet") or {}).keys())) | set(map(str, (patch.get("instancesSet") or {}).keys()))
    for field in ("assignmentRemove", "instancesRemove", "lockedAdd", "lockedRemove", "unplacedAdd", "unplacedRemove"):
        touched_ids.update(str(x) for x in (patch.get(field) or []) if str(x))
    _protect_manual_schedule_ids(touched_ids)

    request_id = str((meta or {}).get("requestId") or "").strip()
    request_record = None
    if request_id:
        with _schedule_patch_request_lock:
            _schedule_patch_request_cleanup_locked()
            request_record = _schedule_patch_requests.get(request_id)
            if request_record is not None:
                _schedule_patch_requests.move_to_end(request_id)

    if request_record is not None:
        result = _schedule_patch_wait_result(request_record, timeout=45.0)
        return jsonify(result.get("body") or {}), int(result.get("status") or 500)

    event = threading.Event()
    item = {
        "key": str(key),
        "patch": patch,
        "meta": meta if isinstance(meta, dict) else {},
        "event": event,
        "result": None,
        "requestId": request_id,
    }
    if request_id:
        with _schedule_patch_request_lock:
            existing = _schedule_patch_requests.get(request_id)
            if existing is not None:
                request_record = existing
            else:
                request_record = {
                    "event": event,
                    "result": None,
                    "created_at": time.monotonic(),
                    "finished_at": None,
                }
                _schedule_patch_requests[request_id] = request_record
    if request_id and request_record is not None and request_record.get("event") is not event:
        result = _schedule_patch_wait_result(request_record, timeout=45.0)
        return jsonify(result.get("body") or {}), int(result.get("status") or 500)

    _ensure_schedule_patch_worker()
    with _schedule_patch_queue_lock:
        _schedule_patch_queue.append(item)
        _schedule_patch_queue_event.set()
    if not event.wait(timeout=45.0):
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



def _schedule_inst_teacher_ids_py(inst):
    out=[]
    for value in [inst.get("teacherId"), *((inst.get("teacherIds") or []) if isinstance(inst.get("teacherIds"), list) else [])]:
        sid=str(value or "")
        if sid and sid not in out:
            out.append(sid)
    return sorted(out)


def _assignment_identity_py(inst, assignment):
    if not isinstance(assignment, dict):
        return None
    if str(inst.get("format") or "inperson") == "remote":
        rooms=("DO",)
    else:
        rooms=tuple(sorted(str(x) for x in (assignment.get("roomId"), assignment.get("extraRoomId")) if str(x or "")))
    return (
        int(assignment.get("day", -1)), int(assignment.get("period", -1)),
        assignment.get("half", None), rooms,
    )


def _safe_started_duplicate_cleanup_ids_py(project, before_schedule, patch, started_ids):
    """Allow deleting ONE proven technical duplicate while a lesson is running.

    The exemption is deliberately narrow: the request must explicitly opt in,
    remove the whole duplicate instance, and an equivalent placed occurrence
    must survive in the same slot/week. This never permits moving/deleting the
    last real occurrence of a started lesson.
    """
    if not isinstance(patch, dict) or patch.get("allowStartedDuplicateCleanup") is not True:
        return set()
    removed={str(x) for x in (patch.get("instancesRemove") or []) if str(x)}
    if not removed:
        return set()
    # A cleanup request may not rewrite the same id into a new lesson.
    if removed.intersection(str(x) for x in (patch.get("instancesSet") or {}).keys()):
        return set()

    after_schedule, _ = _apply_schedule_patch_py(before_schedule, patch)
    before_by={str(x.get("instId") or ""):x for x in (before_schedule.get("instances") or []) if isinstance(x,dict)}
    after_by={str(x.get("instId") or ""):x for x in (after_schedule.get("instances") or []) if isinstance(x,dict)}
    before_assignment=before_schedule.get("assignment") or {}
    after_assignment=after_schedule.get("assignment") or {}
    cfg=(project or {}).get("config") or {}
    current_week=_current_semester_week_py(cfg)
    safe=set()

    for sid in removed.intersection(set(map(str, started_ids or set()))):
        inst=before_by.get(sid)
        a=before_assignment.get(sid)
        if not isinstance(inst,dict) or not isinstance(a,dict) or inst.get("isScheduleEvent") is True:
            continue
        load_id=str(inst.get("loadId") or "")
        if not load_id:
            continue
        groups=set(_schedule_instance_group_ids(inst))
        teachers=_schedule_inst_teacher_ids_py(inst)
        identity=_assignment_identity_py(inst,a)
        try:
            source_weeks={int(w) for w in _instance_weeks_py(cfg,inst)}
        except Exception:
            source_weeks=set()
        if int(current_week) not in source_weeks:
            continue

        for oid, other in after_by.items():
            if oid == sid or not isinstance(other,dict) or other.get("isScheduleEvent") is True:
                continue
            oa=after_assignment.get(oid)
            if not isinstance(oa,dict):
                continue
            if str(other.get("loadId") or "") != load_id:
                continue
            if str(other.get("subjectId") or "") != str(inst.get("subjectId") or ""):
                continue
            if str(other.get("typeId") or "") != str(inst.get("typeId") or ""):
                continue
            if str(other.get("format") or "inperson") != str(inst.get("format") or "inperson"):
                continue
            if int(other.get("subgroup") or 0) != int(inst.get("subgroup") or 0):
                continue
            if set(_schedule_instance_group_ids(other)) != groups:
                continue
            if _schedule_inst_teacher_ids_py(other) != teachers:
                continue
            if _assignment_identity_py(other,oa) != identity:
                continue
            try:
                other_weeks={int(w) for w in _instance_weeks_py(cfg,other)}
            except Exception:
                other_weeks=set()
            if int(current_week) not in other_weeks:
                continue
            safe.add(sid)
            break
    return safe


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
                    if not _storage_exists():
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

                    # v1758: one durable revision counter per instId/cell. Read only
                    # the cells touched by this batch. The schedule JSON is still
                    # the canonical payload, so this layer can be disabled/removed
                    # without migrating existing timetable data.
                    batch_touched_ids = set()
                    for _item in items:
                        _p = _item.get("patch") or {}
                        batch_touched_ids.update(str(x) for x in (_p.get("assignmentSet") or {}).keys())
                        batch_touched_ids.update(str(x) for x in (_p.get("instancesSet") or {}).keys())
                        for _field in ("assignmentRemove","instancesRemove","lockedAdd","lockedRemove","unplacedAdd","unplacedRemove"):
                            batch_touched_ids.update(str(x) for x in (_p.get(_field) or []) if str(x))
                    cell_revisions = _schedule_cell_revisions_get(storage_key, batch_touched_ids)
                    pending_revision_writes = {}
                    revision_meta_by_id = {}

                    for item in items:
                        patch = item["patch"]
                        before_schedule = project.get("schedule") if isinstance(project.get("schedule"), dict) else {}

                        # v1755: compare only the logical cells this request was
                        # based on. Concurrent edits of different lessons merge;
                        # concurrent edits of the same instId never silently win
                        # by request timing. A network retry is accepted when its
                        # desired target state is already present.
                        # v1758: fast optimistic concurrency check using a tiny
                        # per-cell revision instead of comparing the whole schedule.
                        # baseById below remains as a compatibility/safety net.
                        expected_revisions = patch.get("baseRevisionById") or {}
                        safe_offgraph_purge_ids = _safe_offgraph_purge_ids_py(project, before_schedule, patch)
                        safe_offgraph_trim_ids = _safe_offgraph_trim_ids_py(project, before_schedule, patch)
                        safe_explicit_delete_ids = _safe_explicit_manual_occurrence_delete_ids_py(project, before_schedule, patch)
                        safe_offgraph_operation_ids = safe_offgraph_purge_ids | safe_offgraph_trim_ids | safe_explicit_delete_ids
                        revision_conflicts = []
                        if isinstance(expected_revisions, dict):
                            for sid, expected_rev in expected_revisions.items():
                                sid = str(sid or "")
                                if not sid:
                                    continue
                                try:
                                    expected_rev = int(expected_rev)
                                except Exception:
                                    continue
                                current_rev = int(cell_revisions.get(sid, 0))
                                if sid in safe_offgraph_operation_ids:
                                    continue
                                if current_rev != expected_rev and not _schedule_patch_cell_target_already_applied(before_schedule, patch, sid):
                                    revision_conflicts.append(sid)
                        if revision_conflicts:
                            item["result"] = {
                                "status": 409,
                                "body": {
                                    "error": "Эта ячейка уже изменена другим пользователем. Сервер не откатил чужое изменение; загружена актуальная версия.",
                                    "reason": "concurrent_cell_edit",
                                    "conflict": True,
                                    "instIds": revision_conflicts[:50],
                                    "cellRevisions": {sid: int(cell_revisions.get(sid, 0)) for sid in revision_conflicts[:50]},
                                },
                            }
                            continue

                        concurrent_conflicts = _schedule_patch_concurrent_conflicts(project, before_schedule, patch)
                        if concurrent_conflicts:
                            item["result"] = {
                                "status": 409,
                                "body": {
                                    "error": "Эта пара уже изменена другим пользователем. Загружена актуальная версия сервера; повторите действие при необходимости.",
                                    "reason": "concurrent_cell_edit",
                                    "conflict": True,
                                    "instIds": concurrent_conflicts[:50],
                                },
                            }
                            continue

                        # v1635/v1858: completed/started cells remain protected, but
                        # an atomic drag touches only one or a few ids. Do not scan
                        # every placed semester assignment merely to decide whether
                        # those touched ids are historical.
                        requested = set(map(str, (patch.get("assignmentSet") or {}).keys()))
                        requested.update(map(str, (patch.get("instancesSet") or {}).keys()))
                        for _field in ("assignmentRemove","instancesRemove","lockedAdd","lockedRemove","unplacedAdd","unplacedRemove"):
                            requested.update(str(x) for x in (patch.get(_field) or []) if str(x))
                        past_frozen_ids = _placed_past_ids_py(project, before_schedule, requested)
                        started_frozen_ids = _placed_started_today_ids_py(project, before_schedule, requested)
                        frozen_ids = past_frozen_ids | started_frozen_ids
                        blocked = sorted(requested.intersection(frozen_ids))
                        # v1834: an explicit purge of a technical off-graph snapshot
                        # is cleanup metadata, not a historical timetable rewrite.
                        blocked = [sid for sid in blocked if sid not in safe_offgraph_operation_ids]

                        # v1857: a deliberate manual timetable correction may edit a
                        # completed week or a lesson that has already started today.
                        # This flag is emitted only by explicit manual UI actions
                        # (drag/drop, room/format placement, detach, remove). Background
                        # graph sync / generation / recovery jobs must never receive it.
                        manual_historical_override = (
                            patch.get("manualHistoricalOverride") is True
                            and not patch.get("graphSync")
                            and not patch.get("generationPatch")
                            and not patch.get("autoGenerated")
                        )
                        manual_override_ids = set(requested).intersection(frozen_ids) if manual_historical_override else set()
                        if manual_override_ids:
                            blocked = [sid for sid in blocked if sid not in manual_override_ids]

                        # v1619: разрешён единственный безопасный вид изменения
                        # архивного recurring instId — отделить от него текущие/
                        # будущие недели, оставив под старым id ТОЧНО все прошлые
                        # недели и прежнее назначение. Это нужно, чтобы менять
                        # текущую неделю, не двигая историю.
                        safe_trim_ids = set()
                        requested_trim_ids = {str(x) for x in (patch.get("pastTrimIds") or []) if str(x)}
                        if requested_trim_ids:
                            by_before = {
                                str(x.get("instId") or ""): x
                                for x in (before_schedule.get("instances") or [])
                                if isinstance(x, dict)
                            }
                            inst_set = patch.get("instancesSet") or {}
                            current_sem_week = _current_semester_week_py(project.get("config") or {})
                            for sid in requested_trim_ids.intersection(past_frozen_ids):
                                before_inst = by_before.get(sid)
                                after_inst = inst_set.get(sid)
                                if not isinstance(before_inst, dict) or not isinstance(after_inst, dict):
                                    continue
                                before_weeks = sorted(int(w) for w in _instance_weeks_py(project.get("config") or {}, before_inst))
                                expected_past = sorted(int(w) for w in before_weeks if int(w) < int(current_sem_week) and int(w) != 1)
                                after_weeks = sorted(int(w) for w in _instance_weeks_py(project.get("config") or {}, after_inst))
                                before_set, after_set = set(before_weeks), set(after_weeks)
                                removed_weeks = before_set - after_set
                                added_weeks = after_set - before_set
                                # v1882: two safe recurrence-only trims are allowed
                                # for a placed row that contains archived weeks:
                                #  1) legacy archive split: keep exactly all immutable
                                #     past weeks under the old id;
                                #  2) week-copy trim: keep every archived week and
                                #     remove ONLY one or more strictly future weeks.
                                # In both cases assignment/day/room are untouched and
                                # no new week may be added to the archived id.
                                legacy_archive_trim = bool(expected_past) and after_weeks == expected_past
                                future_only_trim = (
                                    bool(expected_past)
                                    and set(expected_past).issubset(after_set)
                                    and not added_weeks
                                    and bool(removed_weeks)
                                    and all(int(w) > int(current_sem_week) for w in removed_weeks)
                                )
                                if not (legacy_archive_trim or future_only_trim):
                                    continue
                                # placement/lock of the archived id may not change
                                assignment_touched = (
                                    sid in (patch.get("assignmentSet") or {}) or
                                    sid in {str(x) for x in (patch.get("assignmentRemove") or [])} or
                                    sid in {str(x) for x in (patch.get("lockedRemove") or [])} or
                                    sid in {str(x) for x in (patch.get("unplacedAdd") or [])}
                                )
                                if assignment_touched:
                                    continue
                                # All semantic fields except recurrence/split metadata
                                # must remain identical.
                                ignored = {
                                    "weekPattern","customWeeks","manualStreamSnapshot",
                                    "manualSplit","userManualSplit","blockId","blockIndex",
                                    "blockSize","blockMode"
                                }
                                bcmp = {k:v for k,v in before_inst.items() if k not in ignored}
                                acmp = {k:v for k,v in after_inst.items() if k not in ignored}
                                if bcmp != acmp:
                                    continue
                                safe_trim_ids.add(sid)

                        blocked = [sid for sid in blocked if sid not in safe_trim_ids]

                        # v1641: a started lesson is still immutable, except for
                        # deleting one PROVEN technical duplicate while an
                        # equivalent placed occurrence survives unchanged.
                        safe_duplicate_cleanup_ids = _safe_started_duplicate_cleanup_ids_py(
                            project, before_schedule, patch, started_frozen_ids
                        )
                        blocked = [sid for sid in blocked if sid not in safe_duplicate_cleanup_ids]

                        # v1774: an explicit Loads -> Schedule projection may
                        # correct only teacher metadata of an already-started
                        # lesson while preserving its day/period/weeks and all
                        # other semantics. This fixes data-entry corrections
                        # (e.g. Bogomolova -> Sorokina) without opening a hole
                        # for moving/deleting a lesson that has already begun.
                        safe_started_graph_teacher_ids = set()
                        if patch.get("graphSync") and str(patch.get("graphSyncSource") or "") in {"loads", "teacher_repair"}:
                            by_before_started = {
                                str(x.get("instId") or ""): x
                                for x in (before_schedule.get("instances") or [])
                                if isinstance(x, dict)
                            }
                            inst_set = patch.get("instancesSet") or {}
                            changed_load_ids = {str(x) for x in (patch.get("graphChangedLoadIds") or []) if str(x)}
                            assignment_set_ids = {str(x) for x in (patch.get("assignmentSet") or {}).keys()}
                            assignment_remove_ids = {str(x) for x in (patch.get("assignmentRemove") or [])}
                            instance_remove_ids = {str(x) for x in (patch.get("instancesRemove") or [])}
                            lock_mutation_ids = {str(x) for x in (patch.get("lockedAdd") or []) + (patch.get("lockedRemove") or [])}
                            unplaced_mutation_ids = {str(x) for x in (patch.get("unplacedAdd") or []) + (patch.get("unplacedRemove") or [])}
                            allowed_teacher_fields = {
                                "teacherId", "teacherIds", "isVacancyTeacher",
                                # These are derived from the teacher record and
                                # may change together with teacherId. They do not
                                # move the already-started lesson or alter its
                                # current room assignment.
                                "preferredRoomIds", "dedicatedRoomId",
                                "startedTodayProtected", "startedProtectedFromInstId",
                                "startedOrphanProtected"
                            }
                            for sid in set(blocked).intersection(started_frozen_ids):
                                before_inst = by_before_started.get(sid)
                                after_inst = inst_set.get(sid)
                                if not isinstance(before_inst, dict) or not isinstance(after_inst, dict):
                                    continue
                                if sid in assignment_set_ids or sid in assignment_remove_ids or sid in instance_remove_ids:
                                    continue
                                if sid in lock_mutation_ids or sid in unplaced_mutation_ids:
                                    continue
                                load_id = str(before_inst.get("loadId") or after_inst.get("loadId") or "")
                                if changed_load_ids and load_id and load_id not in changed_load_ids:
                                    continue
                                keys = set(before_inst.keys()) | set(after_inst.keys())
                                changed_fields = {k for k in keys if before_inst.get(k) != after_inst.get(k)}
                                if changed_fields and changed_fields.issubset(allowed_teacher_fields):
                                    safe_started_graph_teacher_ids.add(sid)
                            blocked = [sid for sid in blocked if sid not in safe_started_graph_teacher_ids]
                        if blocked:
                            started_blocked = [sid for sid in blocked if sid in started_frozen_ids]
                            is_started = bool(started_blocked)
                            item["result"] = {
                                "status": 409,
                                "body": {
                                    "error": (
                                        "Занятие уже началось и зафиксировано. Обычное удаление или перенос во время проведения запрещены. Если это технический дубль, используйте «Удалить технический дубль»."
                                        if is_started else
                                        "Прошедшая неделя зафиксирована. Уже размещённую пару нельзя изменить."
                                    ),
                                    "reason": "started_lesson_immutable" if is_started else "past_week_immutable",
                                    "instIds": (started_blocked if is_started else blocked)[:50],
                                    "currentWeek": _current_semester_week_py(project.get("config") or {}),
                                },
                            }
                            continue

                        next_schedule, touched = _apply_schedule_patch_py(before_schedule, patch)
                        # v1857: do not silently restore a cell that the user has
                        # explicitly corrected in manual mode, even if it is historical
                        # or its start time has passed. All untouched frozen cells remain
                        # protected exactly as before.
                        preserve_exceptions = set(safe_trim_ids) | set(manual_override_ids)
                        next_schedule = _preserve_placed_past_schedule_py(project, before_schedule, next_schedule, preserve_exceptions)
                        # v1883: even the fast manual path must pass the slot-local
                        # server overlap barrier. `_schedule_group_overlap_pairs`
                        # receives only `touched`, so this is not a semester-wide
                        # O(N²) audit; it checks the changed cell against occupants.
                        # manualForceOverlap still permits deliberate teacher overlap
                        # inside that validator, but can no longer bypass group/
                        # subgroup occupancy.
                        overlap_pairs = _schedule_group_overlap_pairs(project, next_schedule, touched)
                        if overlap_pairs:
                            sid, oid, gid = overlap_pairs[0]
                            by_inst = {str(x.get("instId") or ""): x for x in (next_schedule.get("instances") or []) if isinstance(x, dict)}
                            inst_a = by_inst.get(sid) or {}
                            inst_b = by_inst.get(oid) or {}
                            overlap_weeks = sorted(
                                set(_instance_weeks_py(project.get("config") or {}, inst_a))
                                .intersection(_instance_weeks_py(project.get("config") or {}, inst_b))
                            )

                            groups_by_id = {
                                str(x.get("id") or ""): str(x.get("name") or x.get("code") or x.get("id") or "")
                                for x in (project.get("groups") or []) if isinstance(x, dict)
                            }
                            teachers_by_id = {
                                str(x.get("id") or ""): str(x.get("name") or x.get("fio") or x.get("id") or "")
                                for x in (project.get("teachers") or []) if isinstance(x, dict)
                            }

                            gid_text = str(gid or "")
                            teacher_id = ""
                            teacher_name = ""
                            if gid_text.startswith("teacher:"):
                                teacher_id = gid_text.split(":", 1)[1]
                                teacher_name = teachers_by_id.get(teacher_id, teacher_id)
                                reason = "teacher"
                                reason_text = f"преподаватель {teacher_name} уже занят"
                                group_name = ""
                            else:
                                group_name = groups_by_id.get(gid_text, gid_text)
                                reason = "group"
                                reason_text = f"группа {group_name or gid_text} уже имеет занятие в этом слоте"

                            weeks_text = ", ".join(str(x) for x in overlap_weeks) if overlap_weeks else "не определены"
                            subjects_by_id = {
                                str(x.get("id") or ""): str(x.get("name") or x.get("id") or "")
                                for x in (project.get("subjects") or []) if isinstance(x, dict)
                            }
                            conflict_subject = subjects_by_id.get(str(inst_b.get("subjectId") or ""), str(inst_b.get("subjectId") or ""))
                            conflict_a = (next_schedule.get("assignment") or {}).get(oid) or {}
                            try:
                                conflict_day = int(conflict_a.get("day", -1)) + 1
                                conflict_period = int(conflict_a.get("period", -1)) + 1
                            except Exception:
                                conflict_day, conflict_period = 0, 0
                            extra = []
                            if conflict_subject: extra.append(f"занятие «{conflict_subject}»")
                            if conflict_day > 0 and conflict_period > 0: extra.append(f"день {conflict_day}, пара {conflict_period}")
                            extra_text = ("; " + ", ".join(extra)) if extra else ""
                            error_text = (
                                f"Нельзя наложить конфликтующие пары в одну ячейку: "
                                f"{reason_text}; пересечение по неделям: {weeks_text}{extra_text}"
                            )

                            item["result"] = {
                                "status": 409,
                                "body": {
                                    "error": error_text,
                                    "reason": reason,
                                    "groupId": "" if reason == "teacher" else gid_text,
                                    "groupName": group_name,
                                    "teacherId": teacher_id,
                                    "teacherName": teacher_name,
                                    "instId": sid,
                                    "conflictInstId": oid,
                                    "weeks": overlap_weeks,
                                    "loadId": str(inst_a.get("loadId") or ""),
                                    "conflictLoadId": str(inst_b.get("loadId") or ""),
                                },
                            }
                            continue
                        project["schedule"] = next_schedule
                        # v1758: increment only the independently touched instIds.
                        # A colleague editing a different cell cannot change these counters.
                        item_revisions = {}
                        for sid in sorted(str(x) for x in touched if str(x)):
                            next_rev = int(cell_revisions.get(sid, 0)) + 1
                            cell_revisions[sid] = next_rev
                            pending_revision_writes[sid] = next_rev
                            revision_meta_by_id[sid] = item.get("meta") or {}
                            item_revisions[sid] = next_rev
                        item["cellRevisions"] = item_revisions
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
                        # v1837 PERF CORE: one hot-key SQLite transaction instead of
                        # generic whole-store replacement + a second revision-ledger
                        # transaction. This is the critical path for every drag/drop.
                        compacted_project, revision_ledger_ok = _save_store_key_fast(
                            storage_key, project, durable=False,
                            revision_map=pending_revision_writes,
                            revision_meta_by_id=revision_meta_by_id,
                        )
                        store[storage_key] = compacted_project
                        for item in successful:
                            item["result"] = {
                                "status": 200,
                                "body": {
                                    "ok": True,
                                    "token": sync_meta["saveId"],
                                    "patch": _confirmed_schedule_patch(item["patch"]),
                                    "cellRevisions": (item.get("cellRevisions") or {}) if revision_ledger_ok else {},
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
                    rid = str(item.get("requestId") or "")
                    if rid:
                        with _schedule_patch_request_lock:
                            rec = _schedule_patch_requests.get(rid)
                            if rec is not None:
                                rec["result"] = item.get("result")
                                rec["finished_at"] = time.monotonic()
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
    """Return the effective group membership exactly like the client.

    v1603: when explicit streamParticipants exist, they are authoritative.
    Legacy streamGroupIds can contain stale groups after a stream was edited/
    split. The client already ignores streamGroupIds in that case, but the
    server previously still counted them and produced invisible group conflicts.
    """
    if not isinstance(inst, dict):
        return set()

    out = set()
    gid = str(inst.get("groupId") or "")
    if gid:
        out.add(gid)

    # groupIds is an explicit multi-group field for ordinary/event rows.  But
    # v1887/v1888 transfer-split lessons may still carry the PRE-SPLIT legacy
    # list even though their current streamParticipants/groupId were already
    # scoped to one ownership side.  Counting that stale list here makes both
    # groups "own" the same card in group-slice saves and causes edits to appear
    # to jump/snap back.  For transfer/manual stream-break fragments the scoped
    # participant fields below are authoritative.
    transfer_scoped = bool(
        inst.get("manualStreamBreak") or inst.get("transferSubgroupDetached") or
        inst.get("transferSubgroupRetained") or inst.get("transferredLoadRemainder")
    )
    rows = inst.get("groupIds") or []
    if isinstance(rows, list) and (not transfer_scoped or inst.get("isScheduleEvent") is True):
        for x in rows:
            sx = str(x or "")
            if sx:
                out.add(sx)

    parts = inst.get("streamParticipants") or []
    explicit_parts = isinstance(parts, list) and any(
        isinstance(part, dict) and str(part.get("groupId") or "")
        for part in parts
    )

    if explicit_parts:
        # Mirror frontend streamParticipants()/streamGroups(): once detailed
        # participants exist, old streamGroupIds is fallback metadata only.
        for part in parts:
            if isinstance(part, dict):
                sx = str(part.get("groupId") or "")
                if sx:
                    out.add(sx)
    else:
        rows = inst.get("streamGroupIds") or []
        if isinstance(rows, list):
            for x in rows:
                sx = str(x or "")
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



def _schedule_participants_detailed_py(project, inst):
    """Detailed participant list compatible with frontend streamParticipants()."""
    if not isinstance(inst, dict):
        return []
    parts = []

    def add(group_id, subgroup=0, load_id=""):
        gid = str(group_id or "")
        if not gid:
            return
        row = {"groupId": gid, "subgroup": int(subgroup or 0), "loadId": str(load_id or "")}
        key = (row["groupId"], row["subgroup"], row["loadId"])
        if not any((x["groupId"], x["subgroup"], x["loadId"]) == key for x in parts):
            parts.append(row)

    add(inst.get("groupId"), inst.get("subgroup"), inst.get("loadId"))

    explicit = inst.get("streamParticipants")
    if isinstance(explicit, list):
        for part in explicit:
            if isinstance(part, dict):
                add(part.get("groupId"), part.get("subgroup"), part.get("loadId"))

    # Legacy/fallback multi-group fields.
    if not isinstance(explicit, list) or not explicit:
        for gid in (inst.get("streamGroupIds") or []):
            add(gid, inst.get("subgroup"), "")

    for gid in (inst.get("groupIds") or []):
        add(gid, 0, "")

    # Recover a missing target loadId when older published stream metadata only
    # stored group ids.
    loads = (project or {}).get("loads") or []
    for row in parts:
        if row["loadId"]:
            continue
        matches = [
            l for l in loads
            if isinstance(l, dict)
            and str(l.get("groupId") or "") == row["groupId"]
            and str(l.get("subjectId") or "") == str(inst.get("subjectId") or "")
            and str(l.get("teacherId") or "") == str(inst.get("teacherId") or "")
            and str(l.get("typeId") or "") == str(inst.get("typeId") or "")
            and int(l.get("subgroup") or 0) == int(row["subgroup"])
        ]
        if len(matches) == 1:
            row["loadId"] = str(matches[0].get("id") or "")
    return parts


def _instance_without_group_py(project, inst, group_id):
    """Remove one group's participation while preserving every other participant."""
    group_id = str(group_id or "")
    parts = _schedule_participants_detailed_py(project, inst)
    remaining = [p for p in parts if p["groupId"] != group_id]
    if not remaining:
        return None

    old_primary = str(inst.get("groupId") or "")
    primary = next((p for p in remaining if p["groupId"] == old_primary), None) or remaining[0]
    rest = []
    skipped = False
    for p in remaining:
        if not skipped and p == primary:
            skipped = True
            continue
        rest.append(dict(p))

    out = copy.deepcopy(inst)
    out["groupId"] = primary["groupId"]
    out["subgroup"] = int(primary["subgroup"])
    out["loadId"] = primary["loadId"]
    out["streamParticipants"] = rest
    out["streamGroupIds"] = sorted({p["groupId"] for p in rest if p["groupId"] != primary["groupId"]})
    if out.get("isScheduleEvent") is True:
        out["groupIds"] = sorted({p["groupId"] for p in remaining})
    elif isinstance(out.get("groupIds"), list):
        out["groupIds"] = [x for x in out.get("groupIds") or [] if str(x or "") != group_id]

    # Explicit scope is authoritative: never let legacy load.streamId re-add the
    # restored-away group on the next render.
    out["manualStreamBreak"] = True
    out["manualStreamSnapshot"] = True
    out["restorePublishedPeer"] = True
    if len({p["groupId"] for p in remaining}) <= 1:
        out["streamId"] = ""
    return out


def _instance_only_group_py(project, inst, group_id, new_id):
    """Clone a published occurrence containing only the requested group's participants."""
    group_id = str(group_id or "")
    target = [p for p in _schedule_participants_detailed_py(project, inst) if p["groupId"] == group_id]
    if not target:
        return None

    old_primary = str(inst.get("groupId") or "")
    primary = next((p for p in target if p["groupId"] == old_primary), None) or target[0]
    rest = []
    skipped = False
    for p in target:
        if not skipped and p == primary:
            skipped = True
            continue
        rest.append(dict(p))

    out = copy.deepcopy(inst)
    out["instId"] = str(new_id)
    out["groupId"] = primary["groupId"]
    out["subgroup"] = int(primary["subgroup"])
    out["loadId"] = primary["loadId"]
    out["streamParticipants"] = rest
    out["streamGroupIds"] = []
    out["streamId"] = ""
    if out.get("isScheduleEvent") is True:
        out["groupIds"] = [group_id]
    else:
        out["groupIds"] = []
    out["manualStreamBreak"] = True
    out["manualStreamSnapshot"] = True
    out["manualSplit"] = True
    out["userManualSplit"] = True
    out["restoredFromPublished"] = True
    out["restoredPublishedSourceInstId"] = str(inst.get("instId") or "")
    # syncScheduleInstancesToGraph will remap this to a current canonical base.
    out["splitFromInstId"] = str(inst.get("instId") or "")
    out["blockId"] = ""
    out["blockIndex"] = 0
    out["blockSize"] = 1
    out["blockMode"] = ""
    return out



def _project_from_backup_file_py(path):
    name = os.path.basename(path)
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            store = json.load(f)
    else:
        with open(path, "r", encoding="utf-8") as f:
            store = json.load(f)
    raw = store.get("schedule-data-v2")
    project = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(project, dict):
        return None
    return project


def _group_restore_snapshot_py(project, group_id):
    """Small exact per-group undo snapshot, cheap enough to save synchronously."""
    schedule = (project or {}).get("schedule") or {}
    rows = []
    assignment = schedule.get("assignment") or {}
    locked = {str(x) for x in (schedule.get("locked") or [])}
    unplaced = {str(x) for x in (schedule.get("unplaced") or [])}

    for row in schedule.get("instances") or []:
        if not isinstance(row, dict) or str(group_id) not in _schedule_instance_group_ids(row):
            continue
        sid = str(row.get("instId") or "")
        rows.append({
            "row": copy.deepcopy(row),
            "assignment": copy.deepcopy(assignment.get(sid)) if sid in assignment else None,
            "locked": sid in locked,
            "unplaced": sid in unplaced,
        })
    return {
        "groupId": str(group_id),
        "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "rows": rows,
    }


def _apply_group_snapshot_py(project, group_id, snapshot, marker_field="restoredFromGroupUndo"):
    """Replace only one group's current participation with rows from snapshot."""
    schedule = copy.deepcopy((project or {}).get("schedule") or {})
    instances = schedule.get("instances") or []
    assignment = dict(schedule.get("assignment") or {})
    locked = {str(x) for x in (schedule.get("locked") or [])}
    unplaced = {str(x) for x in (schedule.get("unplaced") or [])}

    # Remove current participation of target group, preserve all peers.
    kept = []
    for row in instances:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("instId") or "")
        if str(group_id) not in _schedule_instance_group_ids(row):
            kept.append(copy.deepcopy(row))
            continue
        peer = _instance_without_group_py(project, row, group_id)
        if peer is not None:
            kept.append(peer)
        else:
            assignment.pop(sid, None)
            locked.discard(sid)
            unplaced.discard(sid)

    used = {str(x.get("instId") or "") for x in kept if isinstance(x, dict)}
    safe_gid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(group_id))[:48] or "group"
    restored_count = 0

    for idx, item in enumerate((snapshot or {}).get("rows") or []):
        source = item.get("row") if isinstance(item, dict) else None
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("instId") or f"undo_{idx}")
        candidate = f"{source_id}__GROUPUNDO_{safe_gid}"
        if candidate in used:
            candidate = f"{candidate}_{uuid.uuid4().hex[:8]}"
        used.add(candidate)

        restored = _instance_only_group_py(project, source, group_id, candidate)
        if restored is None:
            continue
        restored["restoredFromPublished"] = False
        restored[marker_field] = True
        restored["restoredGroupUndoSourceInstId"] = source_id
        kept.append(restored)
        restored_count += 1

        if item.get("assignment") is not None:
            assignment[candidate] = copy.deepcopy(item.get("assignment"))
        if item.get("locked"):
            locked.add(candidate)
        if item.get("unplaced") and candidate not in assignment:
            unplaced.add(candidate)

    unplaced = {x for x in unplaced if x not in assignment}
    schedule["instances"] = kept
    schedule["assignment"] = assignment
    schedule["locked"] = sorted(locked)
    schedule["unplaced"] = sorted(unplaced)
    project["schedule"] = schedule
    return restored_count


def _find_pre_restore_group_snapshot_in_backups_py(project, group_id, restore_history_id):
    """Fallback for a restore performed before v1634 existed.

    The synchronous backup created immediately before the restore cannot contain
    the restore history entry itself. Pick the newest valid backup without that
    exact action id and extract only the requested group.
    """
    if not os.path.isdir(BACKUP_DIR):
        return None, None
    names = sorted(
        (n for n in os.listdir(BACKUP_DIR)
         if n.startswith("storage_") and (n.endswith(".json") or n.endswith(".json.gz"))),
        reverse=True,
    )
    for name in names:
        path = os.path.join(BACKUP_DIR, name)
        try:
            candidate = _project_from_backup_file_py(path)
            if not isinstance(candidate, dict):
                continue
            history_ids = {
                str(x.get("id") or "")
                for x in (candidate.get("changeHistory") or [])
                if isinstance(x, dict)
            }
            if restore_history_id and restore_history_id in history_ids:
                continue
            snap = _group_restore_snapshot_py(candidate, group_id)
            if snap.get("rows"):
                return name, snap
        except Exception:
            continue
    return None, None


@app.post("/api/storage-undo-published-group/<key>")
def storage_undo_published_group(key):
    body = request.get_json(force=True, silent=True) or {}
    group_id = str(body.get("groupId") or "").strip()
    meta = body.get("meta") or {}
    if not group_id:
        return jsonify({"error": "Не указана группа"}), 400

    with _lock:
        store = load_store()
        raw = store.get(key)
        project = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(project, dict):
            return jsonify({"error": "Рабочие данные не найдены"}), 404

        group_name = next(
            (str(g.get("name") or group_id) for g in (project.get("groups") or [])
             if isinstance(g, dict) and str(g.get("id") or "") == group_id),
            group_id,
        )

        undo_map = project.get("_groupRestoreUndo") if isinstance(project.get("_groupRestoreUndo"), dict) else {}
        snapshot = undo_map.get(group_id) if isinstance(undo_map.get(group_id), dict) else None
        backup_name = None

        # Fallback specifically covers the restore the user already performed
        # before deploying v1634.
        if not snapshot:
            restore_entries = [
                x for x in (project.get("changeHistory") or [])
                if isinstance(x, dict)
                and str(x.get("action") or "") == "Группа возвращена к опубликованному"
                and group_name in str(x.get("details") or "")
            ]
            restore_entry = restore_entries[-1] if restore_entries else None
            restore_id = str((restore_entry or {}).get("id") or "")
            backup_name, snapshot = _find_pre_restore_group_snapshot_in_backups_py(project, group_id, restore_id)

        if not snapshot or not snapshot.get("rows"):
            return jsonify({
                "error": "Не найдено состояние этой группы непосредственно до возврата к опубликованному"
            }), 404

        # Safety: keep the current broken state too, so undo is itself reversible.
        current_snapshot = _group_restore_snapshot_py(project, group_id)

        restored_count = _apply_group_snapshot_py(project, group_id, snapshot, "restoredFromGroupUndo")

        undo_map = dict(undo_map)
        undo_map[group_id] = current_snapshot
        project["_groupRestoreUndo"] = undo_map

        at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        by = str(meta.get("by") or "Сотрудник")
        history = [x for x in (project.get("changeHistory") or []) if isinstance(x, dict)]
        history.append({
            "id": "undo-restore-published-group-" + uuid.uuid4().hex,
            "at": at,
            "user": by,
            "section": "Расписание",
            "action": "Отменён возврат группы к опубликованному",
            "details": f"{group_name}: возвращено состояние до аварийного отката",
        })
        project["changeHistory"] = history[-1000:]

        sync_meta = {
            "at": at,
            "saveId": "undo-restore-published-group-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": by,
            "scope": "undo-restore-published-group",
            "groupId": group_id,
            "sessionId": str(meta.get("sessionId") or ""),
        }
        project["_syncMeta"] = sync_meta
        store[key] = project
        save_store(store, backup=False, durable=True, refresh_public=False, refresh_status=True)

    return jsonify({
        "ok": True,
        "groupId": group_id,
        "groupName": group_name,
        "restoredInstances": restored_count,
        "backup": backup_name,
        "token": sync_meta["saveId"],
        "_syncMeta": sync_meta,
    })


@app.post("/api/storage-restore-published-group/<key>")
def storage_restore_published_group(key):
    """Restore exactly one group's working timetable from the active publication.

    Other groups' current draft rows are preserved. For shared occurrences the
    requested group is surgically detached from the current row; other
    participants stay exactly where they are. Published shared occurrences are
    restored as an explicit group-only snapshot, so restoring one group never
    rolls another group backwards.
    """
    body = request.get_json(force=True, silent=True) or {}
    group_id = str(body.get("groupId") or "").strip()
    meta = body.get("meta") or {}
    if not group_id:
        return jsonify({"error": "Не указана группа для восстановления"}), 400

    with _lock:
        if not _storage_exists():
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

        published = project.get("publishedSnapshot")
        if not isinstance(published, dict) or not isinstance(published.get("schedule"), dict):
            return jsonify({"error": "Опубликованная версия расписания отсутствует"}), 409

        # v1634: store an exact per-group undo snapshot synchronously.
        # This is tiny and fast; the old full JSON backup under the global lock
        # could make the UI hang for minutes.
        undo_map = project.get("_groupRestoreUndo") if isinstance(project.get("_groupRestoreUndo"), dict) else {}
        undo_map = dict(undo_map)
        undo_map[group_id] = _group_restore_snapshot_py(project, group_id)
        project["_groupRestoreUndo"] = undo_map

        current_schedule = copy.deepcopy(project.get("schedule") or {})
        published_schedule = published.get("schedule") or {}

        current_instances = current_schedule.get("instances") or []
        current_assignment = dict(current_schedule.get("assignment") or {})
        current_locked = {str(x) for x in (current_schedule.get("locked") or []) if str(x)}
        current_unplaced = {str(x) for x in (current_schedule.get("unplaced") or []) if str(x)}

        # 1) Remove ONLY the requested group from the current draft.
        kept_rows = []
        current_group_ids = set()
        for row in current_instances:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("instId") or "")
            if group_id not in _schedule_instance_group_ids(row):
                kept_rows.append(copy.deepcopy(row))
                continue

            current_group_ids.add(sid)
            peer_row = _instance_without_group_py(project, row, group_id)
            if peer_row is not None:
                kept_rows.append(peer_row)
                # Shared row keeps the same id/placement for every OTHER group.
                continue

            current_assignment.pop(sid, None)
            current_locked.discard(sid)
            current_unplaced.discard(sid)

        # 2) Restore published occurrences as explicit group-only rows.
        published_assignment = published_schedule.get("assignment") or {}
        published_locked = {str(x) for x in (published_schedule.get("locked") or []) if str(x)}
        published_unplaced = {str(x) for x in (published_schedule.get("unplaced") or []) if str(x)}
        used_ids = {str(x.get("instId") or "") for x in kept_rows if isinstance(x, dict)}
        restored_rows = []
        restored_placed = 0

        safe_gid = re.sub(r"[^A-Za-z0-9_-]+", "_", group_id)[:48] or "group"
        for index, source in enumerate(published_schedule.get("instances") or []):
            if not isinstance(source, dict) or group_id not in _schedule_instance_group_ids(source):
                continue
            source_id = str(source.get("instId") or f"published_{index}")
            candidate = f"{source_id}__PUBRESTORE_{safe_gid}"
            if candidate in used_ids:
                candidate = f"{candidate}_{uuid.uuid4().hex[:8]}"
            used_ids.add(candidate)

            restored = _instance_only_group_py(published, source, group_id, candidate)
            if restored is None:
                continue
            restored_rows.append(restored)

            if source_id in published_assignment:
                current_assignment[candidate] = copy.deepcopy(published_assignment[source_id])
                restored_placed += 1
            if source_id in published_locked:
                current_locked.add(candidate)
            if source_id in published_unplaced and candidate not in current_assignment:
                current_unplaced.add(candidate)

        restored_ids = {str(x.get("instId") or "") for x in restored_rows}
        current_unplaced = {x for x in current_unplaced if x not in current_assignment}

        next_schedule = dict(current_schedule)
        next_schedule["instances"] = kept_rows + restored_rows
        next_schedule["assignment"] = current_assignment
        next_schedule["locked"] = sorted(x for x in current_locked if x in current_assignment or x in restored_ids or any(str(r.get("instId") or "") == x for r in kept_rows))
        next_schedule["unplaced"] = sorted(current_unplaced)

        # Do not run normal merge/past-week preservation here: this is an
        # explicit administrative recovery FROM the authoritative public snapshot.
        project["schedule"] = next_schedule

        group_name = next(
            (str(g.get("name") or group_id) for g in (project.get("groups") or [])
             if isinstance(g, dict) and str(g.get("id") or "") == group_id),
            group_id,
        )
        at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        by = str(meta.get("by") or "Сотрудник")
        history = [x for x in (project.get("changeHistory") or []) if isinstance(x, dict)]
        history.append({
            "id": "restore-published-group-" + uuid.uuid4().hex,
            "at": at,
            "user": by,
            "section": "Расписание",
            "action": "Группа возвращена к опубликованному",
            "details": f"{group_name}: восстановлено размещённых карточек {restored_placed}",
        })
        project["changeHistory"] = history[-1000:]

        sync_meta = {
            "at": at,
            "saveId": "restore-published-group-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": by,
            "scope": "restore-published-group",
            "groupId": group_id,
            "sessionId": str(meta.get("sessionId") or ""),
        }
        project["_syncMeta"] = sync_meta
        store[key] = project
        save_store(store, backup=False, durable=True, refresh_public=False, refresh_status=True)

    return jsonify({
        "ok": True,
        "groupId": group_id,
        "groupName": group_name,
        "restoredInstances": len(restored_rows),
        "restoredPlaced": restored_placed,
        "token": sync_meta["saveId"],
        "_syncMeta": sync_meta,
    })



def _schedule_snapshot_week_only_py(snapshot, week):
    """Return an immutable snapshot containing only one semester week."""
    if not isinstance(snapshot, dict):
        return None
    try:
        week = int(week)
    except Exception:
        return None
    out = copy.deepcopy(snapshot)
    schedule = copy.deepcopy((snapshot.get("schedule") or {}))
    assignment = schedule.get("assignment") or {}
    unplaced = set(str(x) for x in (schedule.get("unplaced") or []))
    locked = set(str(x) for x in (schedule.get("locked") or []))

    kept = []
    kept_assignment = {}
    kept_unplaced = []
    kept_locked = []

    cfg = snapshot.get("config") or {}
    for row in schedule.get("instances") or []:
        if not isinstance(row, dict):
            continue
        weeks = _instance_weeks_py(cfg, row)
        if week not in set(int(w) for w in weeks):
            continue
        item = copy.deepcopy(row)
        item["weekPattern"] = "custom"
        item["customWeeks"] = [week]
        sid = str(item.get("instId") or "")
        kept.append(item)
        if sid and sid in assignment:
            kept_assignment[sid] = copy.deepcopy(assignment[sid])
        if sid in unplaced:
            kept_unplaced.append(sid)
        if sid in locked:
            kept_locked.append(sid)

    schedule["instances"] = kept
    schedule["assignment"] = kept_assignment
    schedule["unplaced"] = kept_unplaced
    schedule["locked"] = kept_locked
    out["schedule"] = schedule
    out["publishedWeeks"] = [week]
    out["archiveWeek"] = week
    return out


def _archive_publication_insert_py(store_key, version, snapshot, publish_change):
    """Append one successful publication to the permanent archive."""
    archive_id = "publication-" + str(version.get("id") or uuid.uuid4().hex)
    scope = str((publish_change or {}).get("scope") or "all")
    weeks = (publish_change or {}).get("weekNumbers") or []
    try:
        weeks = sorted(set(int(x) for x in weeks if int(x) > 0))
    except Exception:
        weeks = []
    if not weeks:
        weeks = sorted(set(int(x) for x in (snapshot.get("publishedWeeks") or []) if int(x) > 0))
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    conn = _sqlite_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR IGNORE INTO schedule_publication_archive
               (archive_id,store_key,version_id,published_at,published_by,note,scope,week_numbers_json,snapshot_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                archive_id,
                str(store_key),
                str(version.get("id") or ""),
                str(version.get("at") or datetime.utcnow().isoformat(timespec="milliseconds") + "Z"),
                str(version.get("by") or "Сотрудник"),
                str(version.get("note") or "Публикация"),
                scope,
                json.dumps(weeks, ensure_ascii=False),
                payload,
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return archive_id


def _archive_publication_delete_py(archive_id):
    if not archive_id:
        return
    conn = _sqlite_connect()
    try:
        conn.execute("DELETE FROM schedule_publication_archive WHERE archive_id=?", (str(archive_id),))
        conn.commit()
    finally:
        conn.close()


@app.get("/api/schedule-archive/<key>")
def schedule_archive_list(key):
    conn = _sqlite_connect()
    try:
        pub_rows = conn.execute(
            """SELECT archive_id,version_id,published_at,published_by,note,scope,week_numbers_json,created_at
               FROM schedule_publication_archive
               WHERE store_key=?
               ORDER BY created_at DESC
               LIMIT 500""",
            (str(key),),
        ).fetchall()
        final_rows = conn.execute(
            """SELECT archive_id,week_number,revision,source_version_id,finalized_at,finalized_by,created_at
               FROM schedule_week_final_archive
               WHERE store_key=?
               ORDER BY week_number DESC, revision DESC""",
            (str(key),),
        ).fetchall()
    finally:
        conn.close()

    publications = []
    for row in pub_rows:
        try:
            weeks = json.loads(row[6] or "[]")
        except Exception:
            weeks = []
        publications.append({
            "archiveId": row[0], "versionId": row[1], "publishedAt": row[2],
            "publishedBy": row[3], "note": row[4], "scope": row[5],
            "weekNumbers": weeks, "createdAt": row[7],
        })
    week_finals = [{
        "archiveId": row[0], "week": row[1], "revision": row[2],
        "sourceVersionId": row[3], "finalizedAt": row[4], "finalizedBy": row[5],
        "createdAt": row[6],
    } for row in final_rows]
    return jsonify({"publications": publications, "weekFinals": week_finals})


@app.get("/api/schedule-archive/<key>/<kind>/<archive_id>")
def schedule_archive_get(key, kind, archive_id):
    table = "schedule_publication_archive" if kind == "publication" else (
        "schedule_week_final_archive" if kind == "week-final" else None
    )
    if not table:
        return jsonify({"error": "Неизвестный тип архива"}), 400
    conn = _sqlite_connect()
    try:
        row = conn.execute(
            f"SELECT snapshot_json FROM {table} WHERE store_key=? AND archive_id=?",
            (str(key), str(archive_id)),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Архивная запись не найдена"}), 404
    try:
        snapshot = json.loads(row[0])
    except Exception:
        return jsonify({"error": "Архивная запись повреждена"}), 500
    return jsonify({"archiveId": archive_id, "kind": kind, "snapshot": snapshot})


@app.post("/api/schedule-archive/<key>/finalize-week")
def schedule_archive_finalize_week(key):
    body = request.get_json(force=True, silent=True) or {}
    try:
        week = int(body.get("week") or 0)
    except Exception:
        week = 0
    meta = body.get("meta") or {}
    if week <= 0:
        return jsonify({"error": "Не указана неделя"}), 400

    with _lock:
        store = load_store()
        raw = store.get(key)
        project = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(project, dict):
            return jsonify({"error": "Рабочие данные не найдены"}), 404
        snapshot = project.get("publishedSnapshot")
        if not isinstance(snapshot, dict):
            return jsonify({"error": "Сначала опубликуйте расписание этой недели"}), 409
        published_weeks = set(int(x) for x in (project.get("publishedWeeks") or []) if str(x).isdigit())
        if published_weeks and week not in published_weeks:
            return jsonify({"error": f"Неделя {week} не входит в опубликованные недели"}), 409
        week_snapshot = _schedule_snapshot_week_only_py(snapshot, week)
        if not week_snapshot:
            return jsonify({"error": "Не удалось сформировать итог недели"}), 422
        versions = [x for x in (project.get("scheduleVersions") or []) if isinstance(x, dict)]
        source_version_id = str(versions[-1].get("id") or "") if versions else ""
        by = str(meta.get("by") or project.get("publishedBy") or "Сотрудник")

    conn = _sqlite_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(revision),0) FROM schedule_week_final_archive WHERE store_key=? AND week_number=?",
            (str(key), week),
        ).fetchone()
        revision = int((row or [0])[0] or 0) + 1
        archive_id = f"week-{week}-rev-{revision}-{uuid.uuid4().hex[:10]}"
        at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        conn.execute(
            """INSERT INTO schedule_week_final_archive
               (archive_id,store_key,week_number,revision,source_version_id,finalized_at,finalized_by,snapshot_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                archive_id, str(key), week, revision, source_version_id, at, by,
                json.dumps(week_snapshot, ensure_ascii=False, separators=(",", ":")),
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return jsonify({
        "ok": True, "archiveId": archive_id, "week": week, "revision": revision,
        "finalizedAt": at, "finalizedBy": by, "sourceVersionId": source_version_id,
    })


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
    base_published_at = str(body.get("basePublishedAt") or "")
    archive_publication_id = None
    if not isinstance(version, dict) or not version.get("id") or not isinstance(snapshot, dict):
        return jsonify({"error": "Неверные данные публикации"}), 400
    if not isinstance(weeks_raw, list) or not isinstance(locked_add, list):
        return jsonify({"error": "Неверный формат публикации"}), 400

    with _lock:
        if not _storage_exists():
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

        # v1853: never allow a stale browser tab to replace a newer public
        # snapshot. Partial publication is based on publishedSnapshot, so without
        # this compare-and-swap check an operator who kept the page open for days
        # could publish one change and silently roll other weeks back.
        current_published_at = str(project.get("publishedAt") or "")
        if base_published_at != current_published_at:
            return jsonify({
                "error": "Опубликованная версия изменилась в другой вкладке или у другого сотрудника. Обновите данные и повторите публикацию.",
                "code": "STALE_PUBLICATION_BASE",
                "currentPublishedAt": current_published_at,
            }), 409

        # v1609: publication is only successful when BOTH canonical metadata and
        # the public shard/index files are ready. Keep the old public state so a
        # failed shard build can be rolled back instead of falsely returning OK.
        # v1744: keep references to the OLD immutable branches instead of deep-
        # copying a multi-megabyte publication while holding the global storage
        # lock. All branches below are replaced (not mutated in place), so these
        # references are safe rollback material for the async activation worker.
        publication_before = {
            "publishedSnapshot": project.get("publishedSnapshot"),
            "publishedWeeks": project.get("publishedWeeks", []),
            "publicWeekSelectionEnabled": project.get("publicWeekSelectionEnabled"),
            "publishedAt": project.get("publishedAt", ""),
            "publishedBy": project.get("publishedBy", ""),
            "scheduleVersions": project.get("scheduleVersions", []),
            "changeHistory": project.get("changeHistory", []),
            "_syncMeta": project.get("_syncMeta"),
            "locked": ((project.get("schedule") or {}).get("locked", [])) if isinstance(project.get("schedule"), dict) else [],
        }

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
        _maybe_periodic_backup_async()
        # v1704: do not expose the new public index/bootstrap before the matching
        # shard snapshot is completely built. Otherwise a public client can see
        # new publication metadata while /api/public-schedule still points at the
        # previous snapshot. The public files are switched only after shard build.
        # Do not move public status/index ahead of the actual schedule shards.
        save_store(store, backup=False, refresh_public=False, refresh_status=False)
        # public_build_project is constructed below from immutable request data.

        # v1744: permanent archive serialization is also moved out of the HTTP
        # critical path. Canonical SQLite already contains the publication and
        # version metadata; the append-only archive is written after activation.
        archive_publication_id = "publication-" + str(version.get("id") or uuid.uuid4().hex)

        # Build an immutable/lightweight wrapper for the background public worker.
        # The published snapshot itself came from this request and will not be
        # mutated by later editor saves, so no multi-megabyte deepcopy is needed.
        public_build_project = {
            "publishedSnapshot": snapshot,
            "publishedWeeks": list(cleaned),
            "publicWeekSelectionEnabled": bool(project.get("publicWeekSelectionEnabled")),
            "publishedAt": str(project.get("publishedAt") or ""),
            "publishedBy": str(project.get("publishedBy") or ""),
            "publicIntroText": project.get("publicIntroText", ""),
            "announcements": list(project.get("announcements") or []),
            "consultations": list(project.get("consultations") or []),
            "_syncMeta": dict(project.get("_syncMeta") or {}),
        }

    # v1744 STABLE SERVER: canonical publication commit returns immediately.
    # Public index/manifest activation is intentionally moved to a daemon thread so
    # reverse-proxy/availability monitors cannot time out while a large timetable is
    # being serialised. The browser polls /api/public-status until publishedAt matches.
    marker = _public_marker(public_build_project)

    def activate_publication_async():
        try:
            with _public_activation_lock:
                public_store = {"schedule-data-v2": public_build_project}
                _refresh_public_index_from_store(public_store, force=True)

                manifest_local = _activate_public_snapshot_lazy(public_build_project, marker)
                if not isinstance(manifest_local, dict) or str(manifest_local.get("marker") or "") != str(marker):
                    raise RuntimeError("не удалось активировать новый публичный снимок")

                _write_public_status_sidecar(public_build_project)

                loaded_manifest = _load_public_shard_manifest() or {}
                if str(loaded_manifest.get("marker") or "") != str(marker):
                    raise RuntimeError("публичный manifest не подтвердил новую версию")
                with open(PUBLIC_INDEX_META_FILE, "r", encoding="utf-8") as f:
                    index_meta = json.load(f)
                if str(index_meta.get("marker") or "") != str(marker):
                    raise RuntimeError("public-index не подтвердил новую версию")
                with open(PUBLIC_STATUS_FILE, "r", encoding="utf-8") as f:
                    status_payload = json.load(f)
                if str(status_payload.get("publishedAt") or "") != str(public_build_project.get("publishedAt") or ""):
                    raise RuntimeError("public-status не подтвердил время публикации")

            # Archive is intentionally outside the public activation lock; it may
            # serialize a large snapshot but must never block public readers.
            try:
                _archive_publication_insert_py(key, version, snapshot, publish_change)
            except Exception:
                app.logger.exception("publication activated but archive append failed")
        except Exception:
            app.logger.exception("async public snapshot build failed after publication")
            try:
                _archive_publication_delete_py(archive_publication_id)
            except Exception:
                app.logger.exception("failed publication archive rollback")
            # Roll back only if this publication is still the latest canonical save.
            try:
                with _lock:
                    rollback_store = load_store()
                    rollback_raw = rollback_store.get(key)
                    rollback_project = json.loads(rollback_raw) if isinstance(rollback_raw, str) else rollback_raw
                    current_meta = (rollback_project or {}).get("_syncMeta") if isinstance(rollback_project, dict) else {}
                    if isinstance(rollback_project, dict) and str((current_meta or {}).get("saveId") or "") == str(save_id):
                        for k in ("publishedSnapshot","publishedWeeks","publicWeekSelectionEnabled","publishedAt","publishedBy","scheduleVersions","changeHistory"):
                            rollback_project[k] = copy.deepcopy(publication_before.get(k))
                        if publication_before.get("_syncMeta") is None:
                            rollback_project.pop("_syncMeta", None)
                        else:
                            rollback_project["_syncMeta"] = copy.deepcopy(publication_before.get("_syncMeta"))
                        if isinstance(rollback_project.get("schedule"), dict):
                            rs = dict(rollback_project.get("schedule") or {})
                            rs["locked"] = copy.deepcopy(publication_before.get("locked") or [])
                            rollback_project["schedule"] = rs
                        rollback_store[key] = rollback_project
                        save_store(rollback_store, backup=False, refresh_public=False, refresh_status=False)
            except Exception:
                app.logger.exception("async publication rollback failed")

    threading.Thread(
        target=activate_publication_async,
        name=f"public-activate-{save_id[-8:]}",
        daemon=True,
    ).start()

    return jsonify({
        "ok": True,
        "accepted": True,
        "pending": True,
        "publicReady": False,
        "token": save_id,
        "versionId": version.get("id"),
        "publishedWeeks": cleaned,
        "publishedAt": public_build_project.get("publishedAt") or "",
        "marker": marker,
    }), 202


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
        if not _storage_exists():
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
        _maybe_periodic_backup_async()
        # v1704: same atomic public switch for week-selection publication.
        save_store(store, backup=False, refresh_public=False, refresh_status=False)
        public_build_project = {
            "publishedSnapshot": project.get("publishedSnapshot"),
            "publishedWeeks": list(cleaned),
            "publicWeekSelectionEnabled": True,
            "publishedAt": str(project.get("publishedAt") or ""),
            "publishedBy": str(project.get("publishedBy") or ""),
            "publicIntroText": project.get("publicIntroText", ""),
            "announcements": list(project.get("announcements") or []),
            "consultations": list(project.get("consultations") or []),
            "_syncMeta": dict(project.get("_syncMeta") or {}),
        }

    marker = _public_marker(public_build_project)

    def activate_weeks_async():
        try:
            with _public_activation_lock:
                public_store = {"schedule-data-v2": public_build_project}
                _refresh_public_index_from_store(public_store, force=True)
                manifest_local = _activate_public_snapshot_lazy(public_build_project, marker)
                if not isinstance(manifest_local, dict) or str(manifest_local.get("marker") or "") != str(marker):
                    raise RuntimeError("не удалось активировать публичную версию выбранных недель")
                _write_public_status_sidecar(public_build_project)
        except Exception:
            app.logger.exception("async public snapshot build failed after publish-weeks")

    threading.Thread(
        target=activate_weeks_async,
        name=f"public-weeks-{save_id[-8:]}",
        daemon=True,
    ).start()

    return jsonify({
        "ok": True,
        "accepted": True,
        "pending": True,
        "publicReady": False,
        "weeks": cleaned,
        "token": save_id,
        "publishedAt": at,
        "publishedBy": project.get("publishedBy"),
        "marker": marker,
    }), 202


@app.post("/api/storage-schedule-group-merge/<key>")
def storage_schedule_group_merge(key):
    if not _schedule_client_build_ok():
        return _stale_schedule_client_response()
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
        if not _storage_exists():
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
        # v1636: broad/group merge is not a conflict resolver. Never remove a
        # placement as a side effect of autosave/three-way merge.
        remote["schedule"] = _preserve_placed_past_schedule_py(remote, remote_schedule, remote["schedule"])

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
        _maybe_periodic_backup_async()
        save_store(store, backup=False, durable=False, refresh_public=False, refresh_status=False)

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
    if not _schedule_client_build_ok():
        return _stale_schedule_client_response()
    body = request.get_json(force=True, silent=True) or {}
    base = body.get("base") or {}
    local = body.get("local") or {}
    meta = body.get("meta") or {}
    if not isinstance(base, dict) or not isinstance(local, dict):
        return jsonify({"error": "Неверный формат данных расписания"}), 400
    _protect_manual_schedule_ids(_manual_schedule_touched_ids(base.get("schedule"), local.get("schedule")))
    with _lock:
        if not _storage_exists():
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
            # v1636: broad merge must preserve placements verbatim; conflict
            # resolution belongs to explicit atomic cell operations.
            merged["schedule"] = _preserve_placed_past_schedule_py(merged, remote.get("schedule") or {}, merged.get("schedule") or {})
        merged["_syncMeta"] = {
            "at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "saveId": "schedule-" + uuid.uuid4().hex,
            "userId": str(meta.get("userId") or ""),
            "by": str(meta.get("by") or "Пользователь"),
            "scope": "schedule",
        }
        store[key] = merged
        # v1610: draft autosave must not block on a multi-MB backup nor touch the
        # public contour. SQLite NORMAL is still transactional, while avoiding
        # an fsync/public-status penalty on every small editor change.
        _maybe_periodic_backup_async()
        save_store(store, backup=False, durable=False, refresh_public=False, refresh_status=False)
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


@app.post("/api/storage-backups/create")
def storage_backup_create():
    """Create an immediate consistent backup before an explicit destructive admin action."""
    if _request_admin_user() is None:
        return jsonify({"error": "admin authorization required"}), 403
    try:
        with _lock:
            name = _backup_current_store()
        return jsonify({"ok": True, "name": name or ""})
    except Exception as exc:
        app.logger.exception("manual backup failed")
        return jsonify({"error": f"backup failed: {exc}"}), 500


@app.get("/api/storage-backups")
def storage_backups():
    if not os.path.isdir(BACKUP_DIR):
        return jsonify({"backups": []})
    items = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not (name.startswith("storage_") and (name.endswith(".json") or name.endswith(".json.gz"))):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            valid = False
            try:
                if name.endswith(".gz"):
                    with gzip.open(path, "rt", encoding="utf-8") as f:
                        backup_store = json.load(f)
                else:
                    with open(path, "r", encoding="utf-8") as f:
                        backup_store = json.load(f)
                valid = _is_valid_working_value(backup_store.get("schedule-data-v2"))
            except (OSError, json.JSONDecodeError, TypeError, EOFError):
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
    if "/" in name or "\\" in name or not name.startswith("storage_") or not (name.endswith(".json") or name.endswith(".json.gz")):
        return jsonify({"error": "invalid backup name"}), 400
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    try:
        # Refuse to download a truncated/corrupt JSON backup by mistake.
        if name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                candidate = json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                candidate = json.load(f)
        if not _is_valid_working_value(candidate.get("schedule-data-v2")):
            return jsonify({"error": "backup does not contain valid working data"}), 422
    except (OSError, json.JSONDecodeError, EOFError) as exc:
        return jsonify({"error": f"backup is invalid: {exc}"}), 422
    mimetype = "application/gzip" if name.endswith(".gz") else "application/json"
    return send_from_directory(BACKUP_DIR, name, as_attachment=True, download_name=name, mimetype=mimetype)


@app.post("/api/storage-backups/<name>/restore")
def storage_backup_restore(name):
    if "/" in name or "\\" in name or not name.startswith("storage_") or not (name.endswith(".json") or name.endswith(".json.gz")):
        return jsonify({"error": "invalid backup name"}), 400
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    try:
        if name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                candidate = json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                candidate = json.load(f)
    except (OSError, json.JSONDecodeError, EOFError) as exc:
        return jsonify({"error": f"backup is invalid: {exc}"}), 422
    if not _is_valid_working_value(candidate.get("schedule-data-v2")):
        return jsonify({"error": "backup does not contain valid working data"}), 422
    with _lock:
        _backup_current_store()
        save_store(candidate, backup=False, refresh_public=True)
    return jsonify({"ok": True, "restored": name, "storage": "sqlite"})


# v1681: hashed Vite assets are immutable. With many public/admin users, let
# browsers/proxies keep them instead of asking Flask for the same JS/CSS again.
@app.after_request
def _performance_cache_headers(response):
    path = request.path or ""
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.startswith("/admin") or path.startswith("/group/") or path.startswith("/teacher/") or path.startswith("/specialty/"):
        # HTML shell must notice a new deploy, but avoid legacy proxy caching.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

@app.get("/api/app-build")
def app_build():
    # Read-only endpoint. The UI calls it only while an administrator is logged in.
    # no-store is critical: an intermediary must never hide a fresh deployment.
    response = jsonify({
        "buildId": APP_BUILD_ID,
        "scheduleClientBuild": CURRENT_SCHEDULE_CLIENT_BUILD,
        "minScheduleClientBuild": MIN_SCHEDULE_CLIENT_BUILD,
        "startedAt": PROCESS_STARTED_AT if "PROCESS_STARTED_AT" in globals() else None,
        "forceReload": True,
        "reloadAfterMs": 4000,
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/healthz")
def healthz():
    """Ultra-cheap liveness/readiness endpoint for hosting monitors.

    v1744: never enumerate backups, count publication snapshots, or wait on a
    SQLite read here. Those diagnostics were useful interactively but can make
    the monitor itself time out exactly when the server is busy publishing.
    """
    ready = bool(os.path.isdir(DATA_DIR) and (os.path.exists(SQLITE_PATH) or os.path.exists(LEGACY_DATA_FILE)))
    response = jsonify({
        "ok": ready,
        "storageReady": ready,
        "buildId": APP_BUILD_ID if "APP_BUILD_ID" in globals() else "",
        "startedAt": PROCESS_STARTED_AT if "PROCESS_STARTED_AT" in globals() else None,
    })
    response.headers["Cache-Control"] = "no-store"
    return response, (200 if ready else 503)



@app.get("/api/storage-diagnostics")
def storage_diagnostics():
    """Heavy storage diagnostics moved out of /healthz."""
    ready = _storage_exists()
    sqlite_ok = False
    sqlite_bytes = 0
    snapshot_count = 0
    error = ""
    try:
        if os.path.exists(SQLITE_PATH):
            conn = _sqlite_connect()
            try:
                conn.execute("SELECT 1").fetchone()
                snapshot_count = int(conn.execute("SELECT COUNT(*) FROM publication_snapshots").fetchone()[0])
                sqlite_ok = True
            finally:
                conn.close()
            sqlite_bytes = os.path.getsize(SQLITE_PATH)
    except Exception as exc:
        error = str(exc)
    backup_count = 0
    backup_bytes = 0
    try:
        if os.path.isdir(BACKUP_DIR):
            backup_files = [os.path.join(BACKUP_DIR, n) for n in os.listdir(BACKUP_DIR) if n.startswith("storage_") and (n.endswith(".json") or n.endswith(".json.gz"))]
            backup_count = len(backup_files)
            backup_bytes = sum(os.path.getsize(x) for x in backup_files if os.path.isfile(x))
    except Exception:
        pass
    try:
        disk = shutil.disk_usage(DATA_DIR)
        disk_free = disk.free
    except Exception:
        disk_free = None
    return jsonify({
        "ok": bool(ready and (sqlite_ok or os.path.exists(LEGACY_DATA_FILE))),
        "storage": "sqlite", "sqlitePath": SQLITE_PATH, "storageReady": ready,
        "sqliteOk": sqlite_ok, "sqliteBytes": sqlite_bytes,
        "publicationSnapshots": snapshot_count,
        "backupCount": backup_count, "backupBytes": backup_bytes,
        "dataDir": DATA_DIR, "dataDirWritable": os.access(DATA_DIR, os.W_OK),
        "diskFreeBytes": disk_free, "error": error,
    })


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
