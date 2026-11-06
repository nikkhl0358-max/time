#!/usr/bin/env python3
"""Offline regression: schema DDL runs once/process, never creates empty storage."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_compat as pg

class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows

class FakeConnection:
    def __init__(self, tables):
        self.tables = tables
        self.statements = []
        self.commits = 0
        self.closed = False
    def execute(self, sql, params=None):
        self.statements.append(sql)
        if sql.startswith('SELECT tablename FROM pg_tables'):
            return Result([(t,) for t in self.tables])
        return Result([])
    def commit(self): self.commits += 1
    def rollback(self): pass
    def close(self): self.closed = True

class FakePsycopg:
    def __init__(self, tables):
        self.tables = tables
        self.connections = []
    def connect(self, url, connect_timeout=10):
        obj = FakeConnection(self.tables)
        self.connections.append(obj)
        return obj

pg._schema_ready = False
pg.psycopg = FakePsycopg(['kv_store', 'storage_meta'])
first = pg.pg_connect('fake')
assert first.conn.commits == 1
assert len(first.conn.statements) == 1 + len(pg.SCHEMA)
first.close()
second = pg.pg_connect('fake')
assert second.conn.statements == [], 'Normal connection must not read schema or issue DDL'
assert second.conn.commits == 0
second.close()

pg._schema_ready = False
pg.psycopg = FakePsycopg([])
try:
    pg.pg_connect('fake')
except RuntimeError as err:
    assert 'Refusing to start with an empty database' in str(err)
else:
    raise AssertionError('Empty database must be rejected')
assert not pg._schema_ready
assert pg.psycopg.connections[-1].closed

pg.psycopg = FakePsycopg(['wrong_table'])
try:
    pg.pg_connect('fake')
except RuntimeError as err:
    assert 'unknown database format' in str(err)
else:
    raise AssertionError('Unknown schema must be rejected')
assert not pg._schema_ready
assert pg.psycopg.connections[-1].closed

pg.psycopg = FakePsycopg(['kv_store'])
recovered = pg.pg_connect('fake')
assert pg._schema_ready
assert recovered.conn.commits == 1
recovered.close()
print('PASS PostgreSQL one-time schema checks + empty/unknown-db guards')
