"""Small PostgreSQL compatibility layer for the original SQLite-style storage API.

No migrations, drops, truncates or overwrites of existing PostgreSQL schemas.
"""
import re
import psycopg

SCHEMA = [
    'CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)',
    'CREATE TABLE IF NOT EXISTS storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS publication_snapshots (store_key TEXT NOT NULL, version_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(store_key,version_id))',
    '''CREATE TABLE IF NOT EXISTS schedule_publication_archive (archive_id TEXT PRIMARY KEY, store_key TEXT NOT NULL, version_id TEXT NOT NULL, published_at TEXT NOT NULL, published_by TEXT NOT NULL, note TEXT NOT NULL, scope TEXT NOT NULL, week_numbers_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS idx_schedule_publication_archive_store_time ON schedule_publication_archive(store_key, created_at DESC)',
    '''CREATE TABLE IF NOT EXISTS schedule_week_final_archive (archive_id TEXT PRIMARY KEY, store_key TEXT NOT NULL, week_number INTEGER NOT NULL, revision INTEGER NOT NULL, source_version_id TEXT NOT NULL, finalized_at TEXT NOT NULL, finalized_by TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS idx_schedule_week_final_store_week ON schedule_week_final_archive(store_key,week_number,revision DESC)',
    '''CREATE TABLE IF NOT EXISTS schedule_cell_revision (store_key TEXT NOT NULL, inst_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, updated_at DOUBLE PRECISION NOT NULL, updated_by TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(store_key,inst_id))''',
    'CREATE INDEX IF NOT EXISTS idx_schedule_cell_revision_store ON schedule_cell_revision(store_key,revision)',
    '''CREATE TABLE IF NOT EXISTS schedule_cell_change_log (id BIGSERIAL PRIMARY KEY,store_key TEXT NOT NULL,inst_id TEXT NOT NULL,revision INTEGER NOT NULL,changed_at DOUBLE PRECISION NOT NULL,changed_by TEXT NOT NULL DEFAULT '',session_id TEXT NOT NULL DEFAULT '',request_id TEXT NOT NULL DEFAULT '')''',
    'CREATE INDEX IF NOT EXISTS idx_schedule_cell_change_store_time ON schedule_cell_change_log(store_key,changed_at DESC)',
]

def pg_storage_exists(url):
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            return bool(conn.execute("SELECT to_regclass('public.kv_store')").fetchone()[0])
    except Exception:
        return False

class PgConnection:
    def __init__(self, url):
        self.conn = psycopg.connect(url, connect_timeout=10)
        try:
            tables = {r[0] for r in self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()}
            expected = {'kv_store','storage_meta','publication_snapshots','schedule_publication_archive','schedule_week_final_archive','schedule_cell_revision','schedule_cell_change_log'}
            if tables and 'kv_store' not in tables:
                raise RuntimeError('PostgreSQL has existing tables but no kv_store; unknown database format. Refusing automatic initialization to protect existing data.')
            if not tables:
                raise RuntimeError('PostgreSQL contains no tables. Refusing to start with an empty database: existing timetable data must be located first.')
            for sql in SCHEMA:
                self.conn.execute(sql)
            self.conn.commit()
        except Exception:
            self.conn.close()
            raise

    def execute(self, sql, params=None):
        q=sql.strip()
        if q.upper().startswith('PRAGMA ') or q.upper() == 'BEGIN IMMEDIATE':
            return EmptyResult()
        if q.upper().startswith('INSERT OR REPLACE INTO KV_STORE'):
            q='INSERT INTO kv_store(key,value_json,updated_at) VALUES(%s,%s,%s) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at'
        elif q.upper().startswith('INSERT OR REPLACE INTO STORAGE_META'):
            q="INSERT INTO storage_meta(key,value) VALUES('migrated_from_storage_json', %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        elif q.upper().startswith('INSERT OR IGNORE INTO '):
            q=re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', q, count=1, flags=re.I).rstrip(';')+' ON CONFLICT DO NOTHING'
        q=q.replace('LIMIT -1 OFFSET', 'OFFSET')
        q=q.replace('?', '%s')
        return self.conn.execute(q, params)

    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self): self.conn.close()

class EmptyResult:
    def fetchone(self): return None
    def fetchall(self): return []

def pg_connect(url):
    return PgConnection(url)
