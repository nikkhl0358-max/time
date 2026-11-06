"""Standalone auth storage read regression tests; no Flask or production DB required."""
import ast
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import auth_store
module = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_auth_current_users")
namespace = {"json": json, "os": os, "sqlite3": sqlite3, "auth_store": auth_store}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "app.py"), "exec"), namespace)


class TestAuthProjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        namespace.update(USE_POSTGRES=False,
                         SQLITE_PATH=os.path.join(self.tmp.name, "users.db"),
                         LEGACY_DATA_FILE=os.path.join(self.tmp.name, "storage.json"),
                         _auth_project=lambda _: self.fail("full project read is forbidden"),
                         load_store=lambda: self.fail("load_store() is forbidden"))

    def db(self, content):
        with sqlite3.connect(namespace["SQLITE_PATH"]) as conn:
            conn.execute("CREATE TABLE kv_store (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
            conn.execute("INSERT INTO kv_store VALUES (?,?)", ("schedule-data-v2", json.dumps(content)))

    def test_users_only_from_large_project(self):
        users = [{"id": "a", "login": "test", "role": "admin", "passwordHash": "hash"}]
        self.db({"users": users, "classes": ["a" * 500000]})
        self.assertEqual(namespace["_auth_current_users"](), users)

    def test_legacy_double_encoded_project(self):
        users = [{"id": "legacy", "role": "superadmin"}]
        self.db(json.dumps({"users": users}))
        self.assertEqual(namespace["_auth_current_users"](), users)

    def test_empty_sqlite_does_not_create_db(self):
        self.assertEqual(namespace["_auth_current_users"](), [])
        self.assertFalse(os.path.exists(namespace["SQLITE_PATH"]))

    def test_postgres_failure_not_treated_as_empty_users(self):
        namespace["USE_POSTGRES"] = True
        class OperationalError(Exception):
            pass
        fake_pg = types.SimpleNamespace(connect=Mock(side_effect=OperationalError("timeout")))
        with patch.dict(sys.modules, {"psycopg": fake_pg}), patch.dict(os.environ, {"DATABASE_URL": "postgres://fake"}):
            with self.assertRaises(OperationalError):
                namespace["_auth_current_users"]()
            fake_pg.connect.assert_called_once_with("postgres://fake", connect_timeout=5)

    def test_postgres_reads_small_table_not_legacy_json(self):
        namespace["USE_POSTGRES"] = True
        users = [{"id": "a", "role": "admin"}]
        fake_pg = types.SimpleNamespace(connect=Mock())
        with patch.object(auth_store, "read_users", return_value=users) as read, \
             patch.dict(sys.modules, {"psycopg": fake_pg}), \
             patch.dict(os.environ, {"DATABASE_URL": "postgres://fake"}):
            self.assertEqual(namespace["_auth_current_users"](), users)
        read.assert_called_once_with("postgres://fake", fake_pg.connect)
        fake_pg.connect.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
