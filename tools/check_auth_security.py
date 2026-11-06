"""Проверяет границу API локально, не обращаясь к production и его данным."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, jsonify
import auth


class AuthSecurityTests(unittest.TestCase):
    """Проверяет сессии, legacy-вход, CSRF и закрытость новых API."""

    def setUp(self):
        """Создаёт независимые ключи и пользователей для каждого сценария."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.users = [dict(id="one", name="Admin", login="admin", role="admin",
            passwordHash=hashlib.sha256(b"old-pass").hexdigest())]
        self.app = Flask(__name__)
        auth._attempts.clear()
        auth.install_auth(self.app, self.directory.name, lambda: copy.deepcopy(self.users), self.update, threading.Lock())
        self.app.add_url_rule("/api/private", "private", lambda: jsonify(ok=True), methods=["GET", "POST"])
        self.app.add_url_rule("/api/public-index", "public_index", lambda: jsonify(ok=True))
        self.client = self.app.test_client()

    def update(self, users):
        """Сохраняет копию, чтобы тест обнаруживал зависимость от изменяемых ссылок."""
        self.users = copy.deepcopy(users)

    def login(self, **kwargs):
        """Входит старым паролем через реальный HTTP обработчик Flask."""
        return self.client.post("/api/auth/login", json=dict(login="admin", password="old-pass"), **kwargs)

    def test_private_defaults(self):
        """Незнакомый API и поддельный admin header не открывают доступ."""
        self.assertEqual(self.client.get("/api/private").status_code, 401)
        self.assertEqual(self.client.get("/api/private", headers={"X-Admin-User-Id": "one"}).status_code, 401)
        self.assertEqual(self.client.get("/api/new-unregistered").status_code, 401)
        self.assertEqual(self.client.get("/api/public-index").status_code, 200)
        result = self.client.get("/api/auth/session").get_json()
        self.assertEqual(result, dict(user=None, setupRequired=False))

    def test_legacy_login_and_cookie(self):
        """Вход мигрирует SHA256, выдаёт HttpOnly cookie и не раскрывает хеш."""
        result = self.login()
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("passwordHash", result.get_json()["user"])
        self.assertTrue(self.users[0]["passwordHash"].startswith("scrypt:"))
        self.assertIn("HttpOnly", result.headers["Set-Cookie"])
        self.assertIn("Secure", result.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", result.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/api/private").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/private").status_code, 401)

    def test_cross_origin_and_proxy(self):
        """CSRF запрещён, но HTTPS origin работает за TLS termination."""
        body = dict(login="admin", password="old-pass")
        for headers in ({"Origin": "https://evil.example"}, {"Referer": "https://evil.example/a"},
                {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}):
            self.assertEqual(self.client.post("/api/auth/login", json=body, headers=headers).status_code, 403)
        self.assertEqual(self.login(headers={"Origin": "https://localhost"}).status_code, 200)
        self.assertEqual(self.client.post("/api/private", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/private", headers={"Sec-Fetch-Site": "same-origin"}).status_code, 200)

    def test_password_revokes_cookie(self):
        """Смена пароля отзывает прежнюю подпись даже при известном user ID."""
        self.assertEqual(self.login().status_code, 200)
        previous = self.client.get_cookie("timetable_session").value
        result = self.client.post("/api/auth/password", json=dict(oldPassword="old-pass", password="new-password"))
        self.assertEqual(result.status_code, 200)
        self.client.set_cookie("timetable_session", previous)
        self.assertEqual(self.client.get("/api/private").status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        result = self.client.post("/api/auth/login", json=dict(login="admin", password="new-password"))
        self.assertEqual(result.status_code, 200)

    def test_rate_limit(self):
        """Повторные неверные пароли ограничиваются до дорогой проверки хеша."""
        for _ in range(auth.LOGIN_ATTEMPTS):
            result = self.client.post("/api/auth/login", json=dict(login="admin", password="wrong"))
            self.assertEqual(result.status_code, 401)
        self.assertEqual(self.login().status_code, 429)

    def test_successful_logins_do_not_exhaust_limit(self):
        """Успешные входы коллег за общим proxy не накапливают блокировку."""
        for _ in range(auth.LOGIN_ATTEMPTS + 1):
            self.assertEqual(self.login().status_code, 200)

    def test_setup_requires_operator_secret(self):
        """Нельзя создать superadmin через публичный экран первой настройки."""
        self.users = []
        body = dict(name="First", login="first", password="new-password", setupToken="test-token")
        old_token = os.environ.get("AUTH_SETUP_TOKEN")
        os.environ["AUTH_SETUP_TOKEN"] = "test-token"
        try:
            self.assertEqual(self.client.post("/api/auth/setup", json={**body, "setupToken": "bad"}).status_code, 403)
            self.assertEqual(self.client.post("/api/auth/setup", json=body).status_code, 200)
            self.assertEqual(self.users[0]["role"], "superadmin")
            self.assertEqual(self.client.post("/api/auth/setup", json=body).status_code, 409)
        finally:
            if old_token is None:
                os.environ.pop("AUTH_SETUP_TOKEN", None)
            else:
                os.environ["AUTH_SETUP_TOKEN"] = old_token

    def test_persistent_key(self):
        """Перезапуск процесса сохраняет сессии, а ключ доступен только владельцу."""
        key = auth.session_key(self.directory.name)
        self.assertEqual(key, auth.session_key(self.directory.name))
        path = Path(self.directory.name) / ".auth-session-key"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class ApplicationAuthTests(unittest.TestCase):
    """Проверяет реальные endpoints и SQLite на одноразовой базе."""

    def setUp(self):
        """Загружает настоящий backend с изолированными путями и без PostgreSQL."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(__file__).resolve().parents[1]
        environment = dict(DATA_DIR=self.directory.name, DATABASE_URL="", SQLITE_PATH=str(Path(self.directory.name) / "test.db"))
        with patch.dict(os.environ, environment):
            spec = importlib.util.spec_from_file_location("isolated_timetable", root / "app.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.users = [dict(id="admin", name="Admin", login="admin", role="admin",
            passwordHash=hashlib.sha256(b"old-pass").hexdigest()),
            dict(id="root", name="Root", login="root", role="superadmin", passwordHash=hashlib.sha256(b"root-pass").hexdigest())]
        self.module.save_store({"schedule-data-v2": dict(users=self.users, schedule={})}, backup=False, refresh_public=False, refresh_status=False)
        self.client = self.module.app.test_client()
        auth._attempts.clear()

    def login(self):
        """Получает проверенную сессию обычного администратора."""
        result = self.client.post("/api/auth/login", json=dict(login="admin", password="old-pass"))
        self.assertEqual(result.status_code, 200)

    def test_actual_private_routes(self):
        """Production storage/restore endpoints закрыты до чтения базы и файлов."""
        for url in ("/api/storage/schedule-data-v2?editor=1", "/api/storage-backups", "/api/storage-diagnostics"):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post("/api/storage-backups/storage_fake.json/restore").status_code, 401)
        self.login()
        result = self.client.get("/api/storage/schedule-data-v2?editor=1")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/api/storage-backups/storage_fake.json/restore").status_code, 403)
        self.assertEqual(self.client.get("/api/storage-backups/storage_fake.json/download").status_code, 403)
        self.assertEqual(self.client.post("/api/storage-backups/create").status_code, 403)

    def test_editor_bootstrap_does_not_leak_password_hashes(self):
        """Bootstrap и экспорт редактора не раскрывают хэши паролей."""
        self.login()
        res = self.client.get("/api/editor/schedule-data-v2?section=bootstrap")
        self.assertEqual(res.status_code, 200)
        for u in res.get_json()["value"].get("users", []):
            self.assertNotIn("passwordHash", u)
        res_exp = self.client.get("/api/editor/schedule-data-v2/export")
        self.assertEqual(res_exp.status_code, 200)
        exp_data = json.loads(res_exp.data.decode("utf-8"))
        for u in exp_data.get("users", []):
            self.assertNotIn("passwordHash", u)

    def test_storage_role_escalation(self):
        """Обычный admin не повышает роль ни merge, ни полной заменой."""
        self.login()
        project = self.client.get("/api/storage/schedule-data-v2?editor=1").get_json()["value"]
        forged = copy.deepcopy(project)
        forged["users"][0]["role"] = "superadmin"
        result = self.client.post("/api/storage-section-merge/schedule-data-v2", json=dict(base=project, local=dict(users=forged["users"])))
        self.assertEqual(result.status_code, 403)
        result = self.client.post("/api/storage/schedule-data-v2?allow_create=1", json=dict(value=forged))
        self.assertEqual(result.status_code, 403)
        saved = self.module.load_store()["schedule-data-v2"]
        self.assertEqual(saved["users"][0]["role"], "admin")

    def test_fast_save_role_escalation(self):
        """Горячая запись не обходит ту же проверку при мутации кеша по ссылке."""
        def fake_fast_write():
            project = self.module.load_store()["schedule-data-v2"]
            project["users"][0]["role"] = "superadmin"
            self.module._save_store_key_fast("schedule-data-v2", project)
            return jsonify(ok=True)
        self.module.app.add_url_rule("/api/test-fast", "test_fast", fake_fast_write, methods=["POST"])
        self.login()
        self.assertEqual(self.client.post("/api/test-fast").status_code, 403)
        self.module._invalidate_store_cache()
        saved = self.module.load_store()["schedule-data-v2"]
        self.assertEqual(saved["users"][0]["role"], "admin")

    def test_autosave_preserves_concurrent_password_change(self):
        """Запрос со старым users snapshot сохраняет свежие credentials из БД."""
        def concurrent_save():
            stale = copy.deepcopy(self.module.load_store())
            fresh = copy.deepcopy(stale)
            fresh["schedule-data-v2"]["users"][0]["passwordHash"] = "changed-concurrently"
            conn = self.module._sqlite_connect()
            try:
                conn.execute("UPDATE kv_store SET value_json=? WHERE key=?", (json.dumps(fresh["schedule-data-v2"]), "schedule-data-v2"))
                conn.commit()
            finally:
                conn.close()
            self.module.save_store(stale, backup=False, refresh_public=False, refresh_status=False)
            return jsonify(ok=True)
        self.module.app.add_url_rule("/api/test-race", "test_race", concurrent_save, methods=["POST"])
        self.login()
        self.assertEqual(self.client.post("/api/test-race").status_code, 200)
        saved = self.module.load_store()["schedule-data-v2"]
        self.assertEqual(saved["users"][0]["passwordHash"], "changed-concurrently")
        self.assertEqual(self.client.get("/api/storage/schedule-data-v2?editor=1").status_code, 401)

    def test_bounded_gzip(self):
        """Лимит применяется к распакованным байтам, даже если HTTP тело мало."""
        import gzip
        from werkzeug.exceptions import RequestEntityTooLarge
        with patch.dict(os.environ, {"MAX_STORAGE_BYTES": "1024"}):
            with self.assertRaises(RequestEntityTooLarge):
                self.module._decompress_storage_body(gzip.compress(b"x" * 1025))

    def test_logout_revokes_stolen_cookie(self):
        """Выход отзывает сессию на сервере, делая сохранённую cookie невалидной."""
        self.login()
        cookie_val = self.client.get_cookie("timetable_session").value
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.client.set_cookie("timetable_session", cookie_val)
        self.assertEqual(self.client.get("/api/storage/schedule-data-v2?editor=1").status_code, 401)



if __name__ == "__main__":
    unittest.main()
