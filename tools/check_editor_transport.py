"""Проверяет поэтапный private transport на изолированных Flask данных."""
import copy
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, Response
from auth import install_auth
from editor_transport import DEFERRED_SECTIONS, install_editor_transport


def fixture_project():
    """Создаёт разные группы, недельные шаблоны и непустую историю изменений."""
    instances = [dict(instId="a", groupId="g1", weekPattern="custom", customWeeks=[1]),
        dict(instId="b", groupId="g2", weekPattern="all"),
        dict(instId="c", groupId="g1", weekPattern="perWeek", loadId="l1"),
        dict(instId="d", groupId="g1", weekPattern="even")]
    return dict(config=dict(weekParityOverrides={"1": "even"}),
        changeHistory=[dict(id=str(index), details="история" * 50) for index in range(20)],
        loads=[dict(id="l1", weeklyPairs={"2": 1})], graphState={"week": 2},
        publishedSnapshot={"schedule": "old publication"}, _groupRestoreUndo={"restore": True},
        schedule=dict(instances=instances, assignment={row["instId"]: {"day": 1} for row in instances},
            locked=["a", "b", "c"], unplaced=["b", "c"], stats={"placed": 4}),
        users=[], extraField={"unknown": "must survive"})


class EditorTransportTests(unittest.TestCase):
    """Проверяет целостность частей, ревизии, scope, HTTP кеш и auth."""

    def setUp(self):
        """Подключает тот же auth guard, что закрывает реальные private API."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = fixture_project()
        self.revision = "revision-1"
        self.lock = threading.Lock()
        self.app = Flask(__name__)
        users = [dict(id="one", login="admin", name="Admin", role="admin",
            passwordHash=hashlib.sha256(b"password").hexdigest())]
        install_auth(self.app, self.directory.name, lambda: users, lambda value: None, self.lock)
        install_editor_transport(self.app, self.read, self.lock, cell_revisions=lambda key: {"a": 1, "b": 2, "c": 3})
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            from auth import password_stamp
            session.update(user_id="one", password_stamp=password_stamp(users[0]))

    def read(self, key):
        """Подтверждает, что сериализация читает единый снимок под writer lock."""
        self.assertTrue(self.lock.locked())
        return (self.project, self.revision) if key == "test" else None

    def get(self, suffix="", **kwargs):
        """Запрашивает один этап редакторской загрузки."""
        return self.client.get("/api/editor/test" + suffix, **kwargs)

    def test_anonymous_denied(self):
        """Даже bootstrap не является публичным справочником."""
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/editor/test").status_code, 401)
        self.assertEqual(anonymous.get("/api/editor/test/schedule").status_code, 401)

    def test_lossless_reassembly(self):
        """Все канонические поля, включая историю, восстанавливаются без потерь."""
        original = copy.deepcopy(self.project)
        bootstrap = self.get().get_json()
        restored = bootstrap["value"]
        self.assertEqual(restored.pop("_editorDeferred"), list(DEFERRED_SECTIONS))
        for section in bootstrap["deferredSections"]:
            result = self.get(f"?section={section}&token={bootstrap['token']}").get_json()
            self.assertEqual(result["token"], bootstrap["token"])
            restored[section] = result["value"]
        self.assertEqual(restored, original)
        self.assertEqual(self.project, original)
        self.assertEqual(restored["changeHistory"], original["changeHistory"])

    def test_same_identity_revision_change(self):
        """Nested mutation с новой ревизией отвергает токен старого bootstrap."""
        first = self.get().get_json()
        self.project["schedule"]["locked"].append("d")
        self.revision = "revision-2"
        response = self.get(f"?section=schedule&token={first['token']}")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["staleSnapshot"])
        self.assertNotEqual(response.get_json()["token"], first["token"])

    def test_gzip_and_etag(self):
        """Один weak ETag работает для gzip и обычного представления JSON."""
        plain = self.get()
        compressed = self.get(headers={"Accept-Encoding": "gzip"})
        self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(compressed.data), plain.data)
        self.assertEqual(compressed.headers["ETag"], plain.headers["ETag"])
        cached = self.get(headers={"If-None-Match": plain.headers["ETag"], "Accept-Encoding": "gzip"})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.data, b"")
        self.assertIn("Accept-Encoding", compressed.headers["Vary"])
        self.assertEqual(compressed.headers["Cache-Control"], "private, no-cache, must-revalidate")
        disabled = self.get(headers={"Accept-Encoding": "gzip;q=0"})
        self.assertNotIn("Content-Encoding", disabled.headers)

    def test_streaming_and_attachment_are_not_buffered(self):
        """Экспорт остаётся streaming даже когда клиент запрашивает gzip."""
        self.app.add_url_rule("/api/editor/test/export", "editor_export",
            lambda: Response(iter([b"[", b"0," * 2000, b"0]"]), content_type="application/json"))
        self.app.add_url_rule("/api/editor/test/download", "editor_download",
            lambda: Response(b"x" * 2000, content_type="application/json",
                headers={"Content-Disposition": "attachment; filename=test.json"}))
        for suffix in ("/export", "/download"):
            result = self.get(suffix, headers={"Accept-Encoding": "gzip"})
            self.assertNotIn("Content-Encoding", result.headers)
            self.assertGreater(len(result.data), 1024)

    def test_scope_filters_related_indexes(self):
        """Рабочая группа/неделя отбирает assignment, locked, unplaced и revisions."""
        before = copy.deepcopy(self.project)
        response = self.get("/schedule?groupId=g1&week=2").get_json()
        schedule = response["value"]
        self.assertEqual([row["instId"] for row in schedule["instances"]], ["c", "d"])
        self.assertEqual(set(schedule["assignment"]), {"c", "d"})
        self.assertEqual(schedule["locked"], ["c"])
        self.assertEqual(schedule["unplaced"], ["c"])
        self.assertEqual(response["cellRevisions"], {"c": 3})
        self.assertEqual(self.project, before)
        all_rows = self.get("?section=schedule").get_json()
        self.assertEqual(all_rows["cellRevisions"], {"a": 1, "b": 2, "c": 3})

    def test_scope_uses_callback(self):
        """Production может передать authoritative правила потоков и переносов."""
        app = Flask("scoped")
        install_editor_transport(app, lambda key: (self.project, "1"), threading.Lock(),
            matches_scope=lambda project, row, group, week: row["instId"] == "b")
        result = app.test_client().get("/api/editor/test/schedule?groupId=anything&week=9").get_json()
        self.assertEqual([row["instId"] for row in result["value"]["instances"]], ["b"])

    def test_defaults_and_validation(self):
        """Отсутствующие части имеют стабильные defaults без записи в проект."""
        self.project = {"name": "minimal"}
        for section, expected in (("loads", []), ("schedule", None), ("graphState", {})):
            self.assertEqual(self.get(f"?section={section}").get_json()["value"], expected)
        self.assertEqual(self.project, {"name": "minimal"})
        self.assertEqual(self.get("?section=unknown").status_code, 400)
        for week in ("bad", "0", "-1"):
            self.assertEqual(self.get(f"/schedule?week={week}").status_code, 400)
        self.assertEqual(self.client.get("/api/editor/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
