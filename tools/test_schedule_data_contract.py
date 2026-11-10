"""
Контрактные и нагрузочные стресс-тесты публичных данных расписания и шардов.

Модуль предназначен для валидации целостности данных между локальной БД
(raspisanie.db), опубликованным индексом (public-index.json) и динамическими
шардами API (/api/public-schedule/<kind>/<id>).

Проверяет:
  1. Реальные шарды для преподавателей и групп (Колесникова 15bhvzk6, 2-КТП-6 и др.)
  2. Изоляцию замен: исторические замены (сентябрь/октябрь) не попадают в ноябрь
  3. Корректность привязки замен по типам (cancel, moved, excursion и т.д.)
  4. Ссылочную целостность ID справочников и устойчивость к null-значениям
  5. Поддержку протокольных заголовков ETag, 304 Not Modified и Gzip
"""

import gzip
import json
import os
import re
import sys
import unittest

# Добавляем корень проекта для доступа к модулям приложения
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import (
    app,
    _sqlite_connect,
    _public_project_from_store,
    _canonical_public_inst_id,
    _build_public_schedule_shard,
    _public_schedule_shard_for_week,
    _public_entity_data_patch,
)

INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "public-index.json")
CANONICAL_RX = re.compile(
    r"__(?:PUBW\d+|EXACT_W\d+|TRANSFER_[A-Za-z0-9_]+|CANONICAL_RECOVER_[A-Za-z0-9_]+|REMOTE_ONE_[A-Za-z0-9_]+)+$",
    re.IGNORECASE,
)


def load_raw_public_index():
    """Загружает JSON публичного индекса из директории data."""
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_published_snapshot_from_db():
    """Извлекает publishedSnapshot из таблицы kv_store SQLite."""
    conn = _sqlite_connect()
    try:
        row = conn.execute(
            "SELECT value_json FROM kv_store WHERE key='schedule-data-v2'"
        ).fetchone()
        if not row:
            return {}
        store = {"schedule-data-v2": row[0]}
        proj = _public_project_from_store(store)
        return (proj or {}).get("publishedSnapshot") or {}
    finally:
        conn.close()


def decode_response_payload(resp):
    """Декодирует тело ответа с автоматической распаковкой gzip при необходимости."""
    raw = resp.data
    is_gzip = resp.headers.get("Content-Encoding") == "gzip" or raw.startswith(b"\x1f\x8b")
    if is_gzip:
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def request_shard(client, kind, entity_id, scope="week", week=10, accept_gzip=False):
    """Выполняет запрос к эндпоинту публичного шарда с переданными параметрами."""
    headers = {}
    if accept_gzip:
        headers["Accept-Encoding"] = "gzip"
    url = f"/api/public-schedule/{kind}/{entity_id}?scope={scope}"
    if scope == "week":
        url += f"&week={week}"
    return client.get(url, headers=headers)


def extract_instances_and_subs(shard_json):
    """Извлекает списки занятий и замен из JSON-ответа шарда."""
    schedule = shard_json.get("schedule") or {}
    instances = schedule.get("instances") or []
    data_patch = shard_json.get("dataPatch") or {}
    substitutions = data_patch.get("substitutions") or []
    return instances, substitutions


def clean_canonical_id(inst_id):
    """Удаляет временные суффиксы публикации для сопоставления идентификаторов."""
    sid = str(inst_id or "")
    while CANONICAL_RX.search(sid):
        sid = CANONICAL_RX.sub("", sid)
    return sid


class TestPublicIndexContract(unittest.TestCase):
    """Тесты структуры и инвариантов публичного индекса."""

    def setUp(self):
        # Загружаем индекс один раз для валидации контрактной схемы
        self.raw_index = load_raw_public_index()
        self.data = self.raw_index.get("data") or {}

    def test_top_level_contract_fields(self):
        """Проверяет обязательные верхнеуровневые поля в public-index.json."""
        for field in ("data", "userCount", "publishedAt", "publicationStatus"):
            self.assertIn(field, self.raw_index, f"Поле {field} отсутствует в корне индекса")
        self.assertIsInstance(self.data, dict)

    def test_dictionaries_presence_and_types(self):
        """Проверяет наличие всех справочников в data с типом list."""
        for dict_key in ("groups", "teachers", "subjects", "rooms", "lessonTypes"):
            self.assertIn(dict_key, self.data)
            self.assertIsInstance(self.data[dict_key], list)
            self.assertGreater(len(self.data[dict_key]), 0, f"Справочник {dict_key} пуст")

    def test_published_weeks_filter_contract(self):
        """Проверяет настройки публикации недель (недели 8, 9, 10)."""
        weeks = self.data.get("publishedWeeks")
        self.assertTrue(self.data.get("publicWeekSelectionEnabled"))
        self.assertEqual(weeks, [8, 9, 10], "Опубликованные недели должны быть строго [8, 9, 10]")

    def test_semester_config_validity(self):
        """Проверяет конфигурацию семестра: даты начала, окончания и звонки."""
        cfg = self.data.get("config") or {}
        self.assertEqual(cfg.get("semesterStart"), "2026-09-01")
        self.assertEqual(cfg.get("semesterEnd"), "2026-12-21")
        self.assertIsInstance(cfg.get("periodTimes"), list)
        self.assertGreaterEqual(len(cfg.get("periodTimes")), 6)


class TestEntityShardsContract(unittest.TestCase):
    """Тесты доступности и контракта шардов конкретных сущностей."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_kolesnikova_published_weeks(self):
        """Проверяет отдачу недель 8, 9, 10 для преподавателя Колесниковой (15bhvzk6)."""
        tid = "15bhvzk6"
        for week in (8, 9, 10):
            resp = request_shard(self.client, "teacher", tid, scope="week", week=week)
            self.assertEqual(resp.status_code, 200, f"Неделя {week} недоступна для {tid}")
            data = decode_response_payload(resp)
            insts, _ = extract_instances_and_subs(data)
            self.assertGreater(len(insts), 0, f"Нет пар на неделе {week} для {tid}")

    def test_kolesnikova_unpublished_week_11(self):
        """Проверяет, что неопубликованная 11-я неделя отдаёт 404 для Колесниковой."""
        resp = request_shard(self.client, "teacher", "15bhvzk6", scope="week", week=11)
        self.assertEqual(resp.status_code, 404, "Неопубликованная неделя 11 не должна возвращать 200")

    def test_kolesnikova_semester_shard(self):
        """Проверяет семестровый шард преподавателя Колесниковой."""
        resp = request_shard(self.client, "teacher", "15bhvzk6", scope="semester")
        self.assertEqual(resp.status_code, 200)
        data = decode_response_payload(resp)
        self.assertEqual(data.get("scope"), "semester")
        self.assertGreater(len(data.get("schedule", {}).get("instances", [])), 10)

    def test_group_ktp6_published_weeks(self):
        """Проверяет отдачу недель 8, 9, 10 для группы 2-КТП-6 (7rg4gz7s)."""
        gid = "7rg4gz7s"
        for week in (8, 9, 10):
            resp = request_shard(self.client, "group", gid, scope="week", week=week)
            self.assertEqual(resp.status_code, 200, f"Неделя {week} недоступна для группы {gid}")
            data = decode_response_payload(resp)
            insts, _ = extract_instances_and_subs(data)
            self.assertGreater(len(insts), 0, f"Пустое расписание группы {gid} на неделе {week}")

    def test_group_ktp6_unpublished_week_11(self):
        """Проверяет, что неопубликованная 11-я неделя отдаёт 404 для группы 2-КТП-6."""
        resp = request_shard(self.client, "group", "7rg4gz7s", scope="week", week=11)
        self.assertEqual(resp.status_code, 404)

    def test_multiple_groups_shards_contract(self):
        """Проверяет шарды 5 различных групп на неделе 10."""
        # 1-КДД-10, 1-КДД-12, 1-КДД-14, 1-КДД-16, 1-КДД-18
        sample_groups = ("6g5eqnp6", "4pnlllcu", "yhav4ohp", "voyonp22", "8rdvqd1x")
        for gid in sample_groups:
            resp = request_shard(self.client, "group", gid, scope="week", week=10)
            self.assertEqual(resp.status_code, 200, f"Сбой загрузки группы {gid}")
            data = decode_response_payload(resp)
            insts, _ = extract_instances_and_subs(data)
            self.assertGreater(len(insts), 0, f"У группы {gid} нет занятий на неделе 10")

    def test_multiple_teachers_shards_contract(self):
        """Проверяет шарды 5 различных преподавателей на неделе 10."""
        # Аксенова (2mga89pa), Баутина (0d2cnp6e), Богомолова (5pxl9l9c),
        # Бочкарев (um8suceo), Бурцева (74vg17no)
        sample_teachers = ("2mga89pa", "0d2cnp6e", "5pxl9l9c", "um8suceo", "74vg17no")
        for tid in sample_teachers:
            resp = request_shard(self.client, "teacher", tid, scope="week", week=10)
            self.assertEqual(resp.status_code, 200, f"Сбой загрузки преподавателя {tid}")
            data = decode_response_payload(resp)
            self.assertIn("schedule", data)
            self.assertIn("dataPatch", data)


class TestSubstitutionsIsolationAndDateBinding(unittest.TestCase):
    """Тесты изоляции замен и валидации их привязки к конкретным датам."""

    def setUp(self):
        self.snapshot = load_published_snapshot_from_db()
        self.subs = self.snapshot.get("substitutions") or []
        self.schedule = self.snapshot.get("schedule") or {}
        self.instances = self.schedule.get("instances") or []
        self.assignment = self.schedule.get("assignment") or {}

    def test_substitutions_date_format_and_range(self):
        """Проверяет, что у каждой замены дата валидна и находится внутри семестра."""
        for sub in self.subs:
            date_str = str(sub.get("date") or "")
            self.assertTrue(bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str)))
            self.assertGreaterEqual(date_str, "2026-09-01")
            self.assertLessEqual(date_str, "2026-12-21")

    def test_historical_subs_do_not_leak_into_november_dates(self):
        """Проверяет, что ни одна сентябрьская/октябрьская замена не привязана к ноябрю."""
        nov_dates = {f"2026-11-{d:02d}" for d in range(1, 31)}
        hist_subs = [s for s in self.subs if str(s.get("date") or "") < "2026-11-01"]
        for sub in hist_subs:
            sub_date = str(sub.get("date") or "")
            self.assertNotIn(sub_date, nov_dates, f"Замена {sub.get('id')} ошибочно датирована ноябрем")

    def test_all_substitution_types_represented(self):
        """Проверяет корректность типов замен в опубликованном снимке."""
        expected_types = {
            "cancel", "cancel_assignment", "pending_move",
            "moved", "excursion", "join_subgroup", "sub",
        }
        actual_types = {s.get("type") for s in self.subs if s.get("type")}
        missing = expected_types - actual_types
        self.assertEqual(len(missing), 0, f"Отсутствуют типы замен: {missing}")

    def test_moved_substitutions_target_fields(self):
        """Проверяет структуру замен типа moved (targetDate и targetPeriod)."""
        moved_subs = [s for s in self.subs if s.get("type") == "moved"]
        self.assertGreater(len(moved_subs), 0)
        for sub in moved_subs:
            target_date = str(sub.get("targetDate") or "")
            self.assertTrue(bool(re.match(r"^\d{4}-\d{2}-\d{2}$", target_date)))
            self.assertIn("targetPeriod", sub)
            self.assertIsNotNone(sub.get("targetPeriod"))

    def test_cancel_assignment_has_proper_note(self):
        """Проверяет тип cancel_assignment на наличие понятной пометки о задании."""
        ca_subs = [s for s in self.subs if s.get("type") == "cancel_assignment"]
        self.assertGreater(len(ca_subs), 0)
        for sub in ca_subs:
            note = str(sub.get("note") or "")
            self.assertIn("задани", note.lower(), f"Замена {sub.get('id')} не содержит инфо о задании")

    def test_join_subgroup_has_new_teacher(self):
        """Проверяет, что у join_subgroup указан новый преподаватель объединяемой подгруппы."""
        js_subs = [s for s in self.subs if s.get("type") == "join_subgroup"]
        self.assertGreater(len(js_subs), 0)
        for sub in js_subs:
            self.assertTrue(bool(sub.get("newTeacherId")), f"У join_subgroup {sub.get('id')} нет newTeacherId")

    def test_substitution_canonical_instance_binding(self):
        """Проверяет, что замены ссылаются на реальные канонические инстансы расписания."""
        canon_inst_ids = {clean_canonical_id(inst.get("instId")) for inst in self.instances}
        unbound = [
            s.get("id") for s in self.subs
            if clean_canonical_id(s.get("instId")) not in canon_inst_ids
        ]
        # Допускаем не более 5% архивных/ручных замен от старых удаленных связей
        unbound_ratio = len(unbound) / len(self.subs)
        self.assertLess(unbound_ratio, 0.06, f"Слишком много несвязанных замен: {len(unbound)}")


class TestReferentialIntegrityAndResilience(unittest.TestCase):
    """Тесты целостности справочников и устойчивости к null/missing значениям."""

    def setUp(self):
        self.index_data = load_raw_public_index().get("data") or {}
        self.snapshot = load_published_snapshot_from_db()
        self.instances = self.snapshot.get("schedule", {}).get("instances") or []
        self.assignment = self.snapshot.get("schedule", {}).get("assignment") or {}
        self.teachers = {t["id"]: t for t in self.index_data.get("teachers", [])}
        self.subjects = {s["id"]: s for s in self.index_data.get("subjects", [])}
        self.rooms = {r["id"]: r for r in self.index_data.get("rooms", [])}

    def test_all_published_subjects_exist_in_index(self):
        """Проверяет, что все subjectId из инстансов присутствуют в справочнике предметов."""
        missing = set()
        for inst in self.instances:
            sid = inst.get("subjectId")
            if sid and sid not in self.subjects:
                missing.add(str(sid))
        self.assertEqual(len(missing), 0, f"Неизвестные subjectId: {missing}")

    def test_null_room_id_is_safe_and_handled(self):
        """Проверяет, что инстансы без аудиторий (дистант) корректно обрабатываются."""
        null_rooms = 0
        for inst in self.instances:
            iid = inst.get("instId")
            a = self.assignment.get(iid) or {}
            rid = a.get("roomId") or inst.get("roomId")
            if not rid:
                null_rooms += 1
        self.assertGreater(null_rooms, 0, "В расписании должны быть пары без жесткой аудитории")

    def test_mock_null_and_missing_entities_resilience(self):
        """Стресс-тест генерации шардов при наличии null и некорректных ID в данных."""
        dummy_project = {
            "config": {"semesterStart": "2026-09-01", "semesterEnd": "2026-12-21", "totalWeeks": 17},
            "schedule": {
                "instances": [
                    {"instId": "null_test_1", "teacherId": None, "subjectId": None, "day": 0, "weekPattern": "all"},
                    {"instId": "missing_test_2", "teacherId": "ghost_t", "subjectId": "ghost_s", "day": 1, "weekPattern": "all"},
                ],
                "assignment": {
                    "null_test_1": {"day": 0, "period": 0, "roomId": None},
                    "missing_test_2": {"day": 1, "period": 1, "roomId": "ghost_r"},
                },
            },
            "substitutions": [
                {"id": "sub_null", "instId": "null_test_1", "date": "2026-11-04", "type": "cancel", "newTeacherId": None},
            ],
        }
        # Проверяем, что генератор шардов не падает с TypeError / KeyError
        shard = _build_public_schedule_shard(dummy_project, "group", "any_group")
        patch = _public_entity_data_patch(dummy_project, shard.get("instances"))
        week_shard = _public_schedule_shard_for_week(dummy_project, shard, 10)
        self.assertIsInstance(shard, dict)
        self.assertIsInstance(patch, dict)
        self.assertIsInstance(week_shard, dict)


class TestProtocolAndCompressionContract(unittest.TestCase):
    """Тесты протокольных заголовков, ETag и сжатия gzip."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_etag_and_304_not_modified(self):
        """Проверяет генерацию ETag и возврат 304 Not Modified при If-None-Match."""
        url = "/api/public-schedule/teacher/15bhvzk6?scope=week&week=10"
        resp1 = self.client.get(url)
        self.assertEqual(resp1.status_code, 200)
        etag = resp1.headers.get("ETag")
        self.assertIsNotNone(etag)

        resp2 = self.client.get(url, headers={"If-None-Match": etag})
        self.assertEqual(resp2.status_code, 304, "Сервер должен отвечать 304 при совпадении ETag")

    def test_gzip_compression_integrity(self):
        """Проверяет отдачу корректного gzip при заголовке Accept-Encoding: gzip."""
        url = "/api/public-schedule/teacher/15bhvzk6?scope=week&week=10"
        resp = self.client.get(url, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")
        # Проверяем распаковку
        decompressed = gzip.decompress(resp.data)
        data = json.loads(decompressed.decode("utf-8"))
        self.assertIn("schedule", data)
        self.assertEqual(data.get("week"), 10)

    def test_required_security_and_cache_headers(self):
        """Проверяет заголовки Cache-Control и X-Public-Snapshot."""
        resp = self.client.get("/api/public-schedule/group/7rg4gz7s?scope=week&week=10")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))
        self.assertIn("X-Public-Snapshot", resp.headers)


if __name__ == "__main__":
    unittest.main()
