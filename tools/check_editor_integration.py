"""Проверяет, что быстрый вход и частичные сохранения не теряют серверные разделы."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_auth_security as security_tests


class EditorIntegrationTests(unittest.TestCase):
    """Проверяет новый transport на настоящих обработчиках и временной SQLite."""

    def setUp(self):
        """Добавляет разделы, которые нельзя затереть пустыми данными клиента."""
        security_tests.ApplicationAuthTests.setUp(self)
        self.project = dict(users=self.users, config={"semesterStart": "2026-09-01"},
            loads=[{"id": "l1", "groupId": "g1"}], graphState={"l1": {"1": 1}},
            schedule={"instances": [], "assignment": {}, "locked": [], "unplaced": []},
            publishedSnapshot={"important": "published"}, _groupRestoreUndo={"important": "undo"},
            changeHistory=[{"id": "h1", "details": "must survive"}], scheduleVersions=[])
        self.module.save_store({"schedule-data-v2": self.project}, backup=False,
            refresh_public=False, refresh_status=False)
        self.login()
        self.deferred = "schedule,loads,graphState,publishedSnapshot,_groupRestoreUndo"

    def login(self):
        """Использует проверенный вход обычного администратора."""
        security_tests.ApplicationAuthTests.login(self)

    def test_default_read_is_small_bootstrap(self):
        """Обычный storage URL больше не отправляет весь проект."""
        response = self.client.get("/api/storage/schedule-data-v2", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("schedule", response.get_json()["value"])
        self.assertEqual(response.get_json()["value"]["changeHistory"], self.project["changeHistory"])

    def test_scoped_save_preserves_deferred_and_projects_reply(self):
        """Смена настройки не отправляет публикацию назад и не очищает её в БД."""
        local = {"config": {"semesterStart": "2026-09-02"}}
        body = dict(base={"config": self.project["config"]}, local=local)
        response = self.client.post("/api/storage-section-merge/schedule-data-v2",
            json=body, headers={"X-Editor-Deferred": self.deferred})
        self.assertEqual(response.status_code, 200)
        value = response.get_json()["value"]
        self.assertEqual(value["config"], local["config"])
        self.assertNotIn("schedule", value)
        self.assertNotIn("publishedSnapshot", value)
        self.assertEqual(set(value["_editorDeferred"]), set(self.deferred.split(",")))
        saved = self.module.load_store()["schedule-data-v2"]
        for key in self.deferred.split(","):
            self.assertEqual(saved[key], self.project[key])
        self.assertNotIn("_editorDeferred", saved)

    def test_partial_full_save_and_unloaded_section_are_rejected(self):
        """Даже подмена заголовка не превращает неполный проект в полную замену."""
        response = self.client.post("/api/storage/schedule-data-v2", json={"value": self.project},
            headers={"X-Editor-Deferred": self.deferred})
        self.assertEqual(response.status_code, 409)
        partial = {**self.project, "_editorDeferred": ["schedule"]}
        response = self.client.post("/api/storage/schedule-data-v2", json={"value": partial})
        self.assertEqual(response.status_code, 409)
        response = self.client.post("/api/storage-section-merge/schedule-data-v2",
            json={"base": {}, "local": {"schedule": {}}}, headers={"X-Editor-Deferred": self.deferred})
        self.assertEqual(response.status_code, 409)

    def test_full_export_is_explicit_and_portable(self):
        """Экспорт содержит публикацию и вынесенные снимки без transport маркера."""
        response = self.client.get("/api/editor/schedule-data-v2/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        exported = json.loads(response.data)
        self.assertEqual(exported["publishedSnapshot"], self.project["publishedSnapshot"])
        self.assertEqual(exported["schedule"], self.project["schedule"])
        self.assertNotIn("_editorDeferred", exported)

    def test_real_revision_changes_after_save(self):
        """Части разных транзакций нельзя незаметно собрать в один проект."""
        token = self.client.get("/api/editor/schedule-data-v2").get_json()["token"]
        changed = copy.deepcopy(self.module.load_store()["schedule-data-v2"])
        changed["config"]["new"] = True
        self.module.save_store({"schedule-data-v2": changed}, backup=False,
            refresh_public=False, refresh_status=False)
        response = self.client.get(f"/api/editor/schedule-data-v2?section=schedule&token={token}")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["staleSnapshot"])


if __name__ == "__main__":
    unittest.main()
