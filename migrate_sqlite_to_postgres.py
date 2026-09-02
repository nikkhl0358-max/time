#!/usr/bin/env python3
"""One-time migration of timetable SQLite storage to PostgreSQL.

Usage:
  DATABASE_URL='postgresql://...' python migrate_sqlite_to_postgres.py /path/to/raspisanie.db

Supports v1711/v1712 SQLite layout (kv_store + optional publication_snapshots).
Historical publication snapshots are copied to PostgreSQL separately so the hot
working project stays compact and restore-version functionality is preserved.
"""
import json, os, sqlite3, sys, time, gzip

try:
    import psycopg
except Exception as exc:
    raise SystemExit(f"psycopg is required: {exc}")

if len(sys.argv) < 2:
    raise SystemExit("Usage: migrate_sqlite_to_postgres.py /path/to/raspisanie.db")
SQLITE_PATH = sys.argv[1]
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set")
if not os.path.isfile(SQLITE_PATH):
    raise SystemExit(f"SQLite file not found: {SQLITE_PATH}")

src = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
src.row_factory = sqlite3.Row
try:
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "kv_store" not in tables:
        raise SystemExit(f"Unsupported SQLite layout. Tables: {sorted(tables)}")
    rows = src.execute("SELECT key, value_json FROM kv_store").fetchall()
    store = {}
    for row in rows:
        store[str(row["key"])] = json.loads(row["value_json"])

    sqlite_snapshots = []
    if "publication_snapshots" in tables:
        sqlite_snapshots = src.execute("SELECT store_key, version_id, snapshot_json FROM publication_snapshots").fetchall()
finally:
    src.close()

if not store:
    raise SystemExit("SQLite kv_store is empty; migration stopped")
if "schedule-data-v2" not in store:
    raise SystemExit("schedule-data-v2 key is missing; migration stopped")

conn = psycopg.connect(DATABASE_URL, connect_timeout=20)
try:
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS app_kv (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("CREATE TABLE IF NOT EXISTS app_backups (name TEXT PRIMARY KEY, payload_gzip BYTEA NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("CREATE TABLE IF NOT EXISTS publication_snapshots (store_key TEXT NOT NULL, version_id TEXT NOT NULL, payload_gzip BYTEA NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(store_key, version_id))")
        cur.execute("SELECT COUNT(*) FROM app_kv")
        existing = int(cur.fetchone()[0])
        if existing:
            raise RuntimeError(f"PostgreSQL app_kv is not empty ({existing} rows). Refusing to overwrite existing data.")
        # Copy snapshots already externalized by v1712 SQLite.
        for row in sqlite_snapshots:
            raw = row["snapshot_json"].encode("utf-8")
            cur.execute(
                "INSERT INTO publication_snapshots(store_key,version_id,payload_gzip,created_at) VALUES(%s,%s,%s,NOW()) ON CONFLICT(store_key,version_id) DO UPDATE SET payload_gzip=EXCLUDED.payload_gzip",
                (str(row["store_key"]), str(row["version_id"]), gzip.compress(raw, compresslevel=3)),
            )

        portable_store = json.loads(json.dumps(store, ensure_ascii=False))
        # Make the migration backup fully portable by hydrating SQLite's
        # external publication snapshots back into scheduleVersions.
        if sqlite_snapshots and isinstance(portable_store.get("schedule-data-v2"), dict):
            ps = portable_store["schedule-data-v2"]
            versions = ps.get("scheduleVersions") if isinstance(ps.get("scheduleVersions"), list) else []
            snap_map = {}
            for row in sqlite_snapshots:
                if str(row["store_key"]) != "schedule-data-v2": continue
                try: snap_map[str(row["version_id"])] = json.loads(row["snapshot_json"])
                except Exception: pass
            hydrated = []
            for raw_version in versions:
                if not isinstance(raw_version, dict): continue
                version = dict(raw_version); vid = str(version.get("id") or "")
                if not isinstance(version.get("snapshot"), dict) and vid in snap_map:
                    version["snapshot"] = snap_map[vid]
                hydrated.append(version)
            ps = dict(ps); ps["scheduleVersions"] = hydrated
            portable_store["schedule-data-v2"] = ps

        for key, value in list(store.items()):
            compact_value = value
            if key == "schedule-data-v2" and isinstance(value, dict) and isinstance(value.get("scheduleVersions"), list):
                project = dict(value); versions = []
                for raw_version in value.get("scheduleVersions")[-40:]:
                    if not isinstance(raw_version, dict): continue
                    version = dict(raw_version); vid = str(version.get("id") or "")
                    snapshot = version.pop("snapshot", None)
                    if vid and isinstance(snapshot, dict):
                        snap_raw = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        cur.execute(
                            "INSERT INTO publication_snapshots(store_key,version_id,payload_gzip,created_at) VALUES(%s,%s,%s,NOW()) ON CONFLICT(store_key,version_id) DO UPDATE SET payload_gzip=EXCLUDED.payload_gzip",
                            (key, vid, gzip.compress(snap_raw, compresslevel=3)),
                        )
                        version["hasSnapshot"] = True
                    versions.append(version)
                project["scheduleVersions"] = versions; compact_value = project
            payload = json.dumps(compact_value, ensure_ascii=False, separators=(",", ":"))
            cur.execute("INSERT INTO app_kv(key,value_json,updated_at) VALUES(%s,%s,NOW())", (key, payload))

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_payload = gzip.compress(json.dumps(portable_store, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=3)
        cur.execute("INSERT INTO app_backups(name,payload_gzip,created_at) VALUES(%s,%s,NOW())", (f"storage_migrated_{stamp}.json", backup_payload))
    conn.commit()
finally:
    conn.close()

print(f"OK: migrated {len(store)} keys from {SQLITE_PATH} to PostgreSQL")
print("A migration backup was created in app_backups.")
