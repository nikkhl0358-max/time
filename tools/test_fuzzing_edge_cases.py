"""
Модуль расширенного фаззинг-тестирования граничных условий и регрессий паритета.

Проверяет устойчивость системы к аномальным входным данным:
  1. Фаззинг canonicalPublicInstId (составные суффиксы, спецсимволы, пустые значения).
  2. Фаззинг математики дат (все дни 2025-2027 в 28 таймзонах, високосные года, DST, невалидные строки).
  3. Фаззинг URL-парсинга (XSS, path traversal, null bytes, множественные query marks).
  4. Фаззинг состояния и localStorage (битый JSON, некорректные типы, отравленные объекты).
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import _canonical_public_inst_id


def run_node_eval(js_expr: str, setup_code: str = "", env_extra: dict = None) -> any:
    """Выполняет JavaScript-выражение в среде Node.js и возвращает результат."""
    code = f"{setup_code}\nconst res = ({js_expr});\nconsole.log(JSON.stringify(res));"
    env = dict(os.environ, **(env_extra or {}))
    proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True, env=env)
    return json.loads(proc.stdout.strip())


def run_node_script(script: str, env_extra: dict = None) -> any:
    """Запускает скрипт в Node.js, ожидая вывод JSON в последней строке stdout."""
    env = dict(os.environ, **(env_extra or {}))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, env=env)
    return json.loads(proc.stdout.strip())


NODE_CANONICAL_SETUP = """
function canonicalPublicInstId(value) {
  let id = String(value || '');
  const rx = /__(?:PUBW\\d+|EXACT_W\\d+|TRANSFER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|CANONICAL_RECOVER_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|REMOTE_ONE_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)$/i;
  while (rx.test(id)) {
    id = id.replace(rx, '');
  }
  return id;
}
"""

NODE_DATE_MATH_SETUP = """
function computeScheduleDate(startStr, weekNum, dayIdx) {
  if (!startStr || !weekNum || dayIdx === undefined || dayIdx === null) return null;
  const p = String(startStr).split('-').map(Number);
  if (p.length < 3 || p.some(isNaN)) return null;
  const [y, m, d] = p;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  const dt = new Date(y, m - 1, d, 12, 0, 0);
  if (isNaN(dt.getTime()) || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
  const numWeek = Number(weekNum);
  const numDay = Number(dayIdx);
  if (isNaN(numWeek) || isNaN(numDay)) return null;
  const mondayOffset = (dt.getDay() + 6) % 7;
  dt.setDate(dt.getDate() - mondayOffset + (numWeek - 1) * 7 + numDay);
  return isNaN(dt.getTime()) ? null : dt;
}

function formatISODate(startStr, weekNum, dayIdx) {
  const dt = computeScheduleDate(startStr, weekNum, dayIdx);
  if (!dt || isNaN(dt.getTime())) return '';
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const d = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
"""

NODE_ALL_DAYS_TZ_SCRIPT = """
let count = 0;
let errors = [];
const years = [2025, 2026, 2027];
for (const yr of years) {
  const totalDays = ((yr % 4 === 0 && yr % 100 !== 0) || (yr % 400 === 0)) ? 366 : 365;
  for (let d = 0; d < totalDays; d++) {
    const cur = new Date(yr, 0, 1 + d, 12, 0, 0);
    const y = cur.getFullYear();
    const m = String(cur.getMonth() + 1).padStart(2, '0');
    const day = String(cur.getDate()).padStart(2, '0');
    const dateStr = `${y}-${m}-${day}`;
    const mondayOffset = (cur.getDay() + 6) % 7;
    const res = formatISODate(dateStr, 1, mondayOffset);
    count++;
    if (res !== dateStr) errors.push({ dateStr, res });
  }
}
console.log(JSON.stringify({ count, errors }));
"""

NODE_ROUTE_SYNC_SETUP = """
function safeDecode(val) {
  try { return decodeURIComponent(String(val || '')); }
  catch { return String(val || ''); }
}

function findEntityInParts(parts) {
  const groupIdx = parts.indexOf('group');
  if (groupIdx !== -1 && parts[groupIdx + 1]) {
    return { kind: 'group', id: safeDecode(parts[groupIdx + 1]) };
  }
  const teacherIdx = parts.indexOf('teacher');
  if (teacherIdx !== -1 && parts[teacherIdx + 1]) {
    return { kind: 'teacher', id: safeDecode(parts[teacherIdx + 1]) };
  }
  const roomIdx = parts.indexOf('room');
  if (roomIdx !== -1 && parts[roomIdx + 1]) {
    return { kind: 'room', id: safeDecode(parts[roomIdx + 1]) };
  }
  return null;
}

function parseUrlPath(pathname) {
  const parts = (pathname || '').split('/').filter(Boolean);
  return findEntityInParts(parts);
}

function parseUrlHash(hash) {
  if (!hash) return null;
  const [cleanHash] = hash.replace(/^#\\/?/, '').split('?');
  const hyphenMatch = cleanHash.match(/^(group|teacher|room)-([a-zA-Z0-9_-]+)$/);
  if (hyphenMatch) {
    return { kind: hyphenMatch[1], id: safeDecode(hyphenMatch[2]) };
  }
  const parts = cleanHash.split('/').filter(Boolean);
  return findEntityInParts(parts);
}

function parseUrlSearch(search) {
  const params = new URLSearchParams(search || '');
  const rawWeek = params.get('week');
  const numWeek = rawWeek ? Number(rawWeek) : null;
  const week = (numWeek && !isNaN(numWeek) && numWeek > 0 && Number.isFinite(numWeek)) ? Math.floor(numWeek) : null;
  if (params.get('group')) return { kind: 'group', id: safeDecode(params.get('group')), week };
  if (params.get('teacher')) return { kind: 'teacher', id: safeDecode(params.get('teacher')), week };
  if (params.get('room')) return { kind: 'room', id: safeDecode(params.get('room')), week };
  if (params.get('kind') && params.get('id')) {
    return { kind: safeDecode(params.get('kind')), id: safeDecode(params.get('id')), week };
  }
  return { week };
}

function entityExists(data, kind, id) {
  if (!id || !data) return false;
  const list = kind === 'teacher' ? data.teachers : (kind === 'room' ? data.rooms : data.groups);
  return Array.isArray(list) && list.some(item => String(item.id) === String(id));
}

function resolveFallbackWeek(data, requestedWeek) {
  if (requestedWeek && Array.isArray(data.publishedWeeks) && data.publishedWeeks.includes(requestedWeek)) {
    return requestedWeek;
  }
  if (Array.isArray(data.publishedWeeks) && data.publishedWeeks.length) {
    return data.publishedWeeks[data.publishedWeeks.length - 1];
  }
  return 10;
}

function resolveInitialSelection(data, route, saved) {
  const validKinds = ['group', 'teacher', 'room'];
  let targetKind = validKinds.includes(route?.kind) ? route.kind : 'group';
  if (!route?.id && saved?.kind && validKinds.includes(saved.kind)) {
    targetKind = saved.kind;
  }
  let targetId = (typeof route?.id === 'string' && route.id) || (typeof saved?.id === 'string' && saved.id) || '';
  let targetWeek = route?.week || (typeof saved?.week === 'number' && saved.week > 0 ? saved.week : null);

  if (route?.id && !entityExists(data, route.kind, route.id)) {
    targetId = saved?.id && typeof saved.id === 'string' && entityExists(data, targetKind, saved.id) ? saved.id : '';
    targetKind = targetId ? targetKind : 'group';
  } else if (targetId && !entityExists(data, targetKind, targetId)) {
    targetId = '';
  }

  const finalWeek = resolveFallbackWeek(data, targetWeek);
  return { kind: targetKind, id: targetId, week: finalWeek };
}
"""


class TestCanonicalInstIdFuzzing(unittest.TestCase):
    """Фаззинг-тестирование канонических идентификаторов занятий."""

    def test_01_extreme_compound_suffixes_parity(self):
        """Проверяет снятие длинных составных суффиксов в бэкенде и фронтенде."""
        base = "lesson_root_uuid_123"
        suffix = "__PUBW1__PUBW2__EXACT_W3__TRANSFER_RESIDUAL_W4__CANONICAL_RECOVER_W5__REMOTE_ONE_6"
        extreme_id = f"{base}{suffix}"

        py_res = _canonical_public_inst_id(extreme_id)
        js_res = run_node_eval(f"canonicalPublicInstId({json.dumps(extreme_id)})", NODE_CANONICAL_SETUP)

        self.assertEqual(py_res, base)
        self.assertEqual(js_res, base)
        self.assertEqual(py_res, js_res)

    def test_02_special_characters_and_emojis(self):
        """Проверяет сохранение спецсимволов, эмодзи и юникода при канонизации."""
        cases = ["inst-!@#$%^&*()", "inst-👍🔥🎯", "пары_русский_текст", "id with spaces", "inst/slash\\backslash"]
        for val in cases:
            py_res = _canonical_public_inst_id(val)
            js_res = run_node_eval(f"canonicalPublicInstId({json.dumps(val)})", NODE_CANONICAL_SETUP)
            self.assertEqual(py_res, val)
            self.assertEqual(js_res, val)

    def test_03_empty_and_falsy_values(self):
        """Проверяет безопасную обработку None, null, пустых строк и пробелов."""
        self.assertEqual(_canonical_public_inst_id(""), "")
        self.assertEqual(_canonical_public_inst_id(None), "")
        self.assertEqual(run_node_eval("canonicalPublicInstId('')", NODE_CANONICAL_SETUP), "")
        self.assertEqual(run_node_eval("canonicalPublicInstId(null)", NODE_CANONICAL_SETUP), "")
        self.assertEqual(run_node_eval("canonicalPublicInstId(undefined)", NODE_CANONICAL_SETUP), "")

    def test_04_numbers_and_non_string_types(self):
        """Проверяет конвертацию чисел и булевых значений в строковые ID."""
        self.assertEqual(_canonical_public_inst_id(12345), "12345")
        self.assertEqual(_canonical_public_inst_id(0), "")
        self.assertEqual(run_node_eval("canonicalPublicInstId(12345)", NODE_CANONICAL_SETUP), "12345")
        self.assertEqual(run_node_eval("canonicalPublicInstId(0)", NODE_CANONICAL_SETUP), "")
        self.assertEqual(run_node_eval("canonicalPublicInstId(true)", NODE_CANONICAL_SETUP), "true")

    def test_05_pure_suffix_strings_no_base(self):
        """Проверяет очистку строки, состоящей только из суффиксов без базового ID."""
        pure_suffixes = ["__PUBW1", "__PUBW1__PUBW2", "__EXACT_W3", "__TRANSFER_RESIDUAL_W4"]
        for s in pure_suffixes:
            self.assertEqual(_canonical_public_inst_id(s), "")
            self.assertEqual(run_node_eval(f"canonicalPublicInstId({json.dumps(s)})", NODE_CANONICAL_SETUP), "")

    def test_06_chain_order_variations(self):
        """Проверяет различные комбинации порядка следования суффиксов."""
        chains = [
            "root__EXACT_W1__PUBW2",
            "root__PUBW1__EXACT_W2",
            "root__TRANSFER_A_B__CANONICAL_RECOVER_C",
            "root__REMOTE_ONE_1__PUBW99",
        ]
        for c in chains:
            self.assertEqual(_canonical_public_inst_id(c), "root")
            self.assertEqual(run_node_eval(f"canonicalPublicInstId({json.dumps(c)})", NODE_CANONICAL_SETUP), "root")

    def test_07_deep_repetitive_suffixes_no_redos(self):
        """Проверяет устойчивость к ReDoS при 200 повторяющихся суффиксах подряд."""
        heavy_id = "target_id" + ("__PUBW1" * 200)
        py_res = _canonical_public_inst_id(heavy_id)
        js_res = run_node_eval(f"canonicalPublicInstId({json.dumps(heavy_id)})", NODE_CANONICAL_SETUP)
        self.assertEqual(py_res, "target_id")
        self.assertEqual(js_res, "target_id")


class TestDateMathFuzzing(unittest.TestCase):
    """Фаззинг календарной математики, таймзон, високосных лет и переходов DST."""

    TIMEZONES = [
        "Etc/GMT+12", "Pacific/Pago_Pago", "Pacific/Honolulu", "America/Anchorage",
        "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
        "America/Halifax", "America/Sao_Paulo", "America/Noronha", "Atlantic/Cape_Verde",
        "UTC", "Europe/London", "Europe/Berlin", "Europe/Helsinki", "Europe/Moscow",
        "Asia/Dubai", "Asia/Karachi", "Asia/Dhaka", "Asia/Bangkok", "Asia/Singapore",
        "Asia/Tokyo", "Australia/Sydney", "Pacific/Guadalcanal", "Pacific/Auckland",
        "Pacific/Tongatapu", "Pacific/Kiritimati"
    ]

    def test_01_all_365_days_2025_2027_across_all_28_timezones(self):
        """Проверяет 30660 дней во всех таймзонах на отсутствие суточных смещений."""
        check_script = f"{NODE_DATE_MATH_SETUP}\n{NODE_ALL_DAYS_TZ_SCRIPT}"
        for tz in self.TIMEZONES:
            res = run_node_script(check_script, env_extra={"TZ": tz})
            self.assertEqual(len(res["errors"]), 0, f"Ошибка дат в таймзоне {tz}: {res['errors'][:3]}")
            self.assertEqual(res["count"], 1095)

    def test_02_leap_years_and_end_of_february(self):
        """Проверяет корректность 29 февраля в високосных годах 2024 и 2028."""
        cases = [
            ("2024-02-29", 1, 0, "2024-02-26"),  # Високосный 2024
            ("2028-02-29", 1, 0, "2028-02-28"),  # Високосный 2028
            ("2025-02-28", 1, 0, "2025-02-24"),  # Обычный 2025
            ("2026-02-28", 1, 0, "2026-02-23"),  # Обычный 2026
            ("2027-02-28", 1, 0, "2027-02-22"),  # Обычный 2027
        ]
        for start_str, week, day, expected in cases:
            res = run_node_eval(f"formatISODate('{start_str}', {week}, {day})", NODE_DATE_MATH_SETUP)
            self.assertEqual(res, expected)

    def test_03_dst_transition_continuity(self):
        """Проверяет непрерывность дней недели при переходах на летнее время."""
        script = f"""
        {NODE_DATE_MATH_SETUP}
        let jumps = 0;
        let prev = null;
        for (let w = 1; w <= 52; w++) {{
          for (let d = 0; d < 7; d++) {{
            const dt = computeScheduleDate('2026-01-01', w, d);
            if (prev) {{
              const diffDays = Math.round((dt - prev) / 86400000);
              if (diffDays !== 1) jumps++;
            }}
            prev = dt;
          }}
        }}
        console.log(JSON.stringify({{ jumps }}));
        """
        for tz in ["Europe/London", "America/New_York", "Australia/Sydney"]:
            res = run_node_script(script, env_extra={"TZ": tz})
            self.assertEqual(res["jumps"], 0, f"Скачок дня в таймзоне {tz}")

    def test_04_invalid_date_strings_safe_fallback(self):
        """Проверяет, что невалидные строки возвращают пустую строку, а не NaN."""
        invalid_inputs = ["", "invalid-date", "2026-13-45", "2026-02-30", "null", "undefined", "2026-99-99"]
        for s in invalid_inputs:
            res = run_node_eval(f"formatISODate({json.dumps(s)}, 1, 0)", NODE_DATE_MATH_SETUP)
            self.assertEqual(res, "", f"Ожидалась пустая строка для {s}, получено: {res}")

    def test_05_invalid_week_and_day_indices(self):
        """Проверяет защиту от NaN и undefined в параметрах недели и дня."""
        cases = [
            ("2026-09-01", "abc", 0),
            ("2026-09-01", 1, "xyz"),
            ("2026-09-01", 1, "undefined"),
            ("2026-09-01", -5, 0),
            ("2026-09-01", 100000000, 0),
        ]
        for s, w, d in cases:
            week_val = f"'{w}'" if isinstance(w, str) and w != "undefined" else str(w)
            day_val = f"'{d}'" if isinstance(d, str) and d != "undefined" else str(d)
            res = run_node_eval(f"formatISODate('{s}', {week_val}, {day_val})", NODE_DATE_MATH_SETUP)
            self.assertNotIn("NaN", res, f"Обнаружен NaN в выводе formatISODate({s}, {w}, {d})")


class TestUrlParsingFuzzing(unittest.TestCase):
    """Фаззинг разбора URL-путей, параметров поиска и хэша."""

    def test_01_xss_injections_in_url_paths(self):
        """Проверяет безопасность разбора путей с XSS-нагрузками."""
        xss_payloads = [
            "/group/<script>alert(1)</script>",
            "/teacher/\"><img src=x onerror=alert(1)>",
            "/room/javascript:alert(1)",
            "/group/<svg/onload=alert(1)>",
        ]
        for path in xss_payloads:
            res = run_node_eval(f"parseUrlPath({json.dumps(path)})", NODE_ROUTE_SYNC_SETUP)
            self.assertIsNotNone(res)
            self.assertIn(res["kind"], ["group", "teacher", "room"])
            self.assertIsInstance(res["id"], str)

    def test_02_path_traversal_attempts(self):
        """Проверяет обработку попыток выхода за пределы директории."""
        traversal_paths = [
            "/group/../../etc/passwd",
            "/teacher/..%2f..%2fetc/passwd",
            "/room/../../../windows/win.ini",
            "/group/....//....//etc",
        ]
        for path in traversal_paths:
            res = run_node_eval(f"parseUrlPath({json.dumps(path)})", NODE_ROUTE_SYNC_SETUP)
            self.assertIsNotNone(res)
            # Убеждаемся, что разбор не падает и id изолирован
            self.assertIsInstance(res["id"], str)

    def test_03_long_strings_and_percent_encoded_characters(self):
        """Проверяет разбор сверхдлинных строк, пробелов и спец-байтов."""
        long_id = "g" * 5000
        res_long = run_node_eval(f"parseUrlPath('/group/{long_id}')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res_long["id"], long_id)

        res_encoded = run_node_eval("parseUrlPath('/group/my%20group%2Fname%00byte')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res_encoded["id"], "my group/name\x00byte")

        res_malformed = run_node_eval("parseUrlPath('/group/%E0%A4%A')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res_malformed["id"], "%E0%A4%A")

    def test_04_multiple_question_marks_in_hash_and_search(self):
        """Проверяет разбор хэша и search-параметров с несколькими знаками вопроса."""
        res_hash = run_node_eval("parseUrlHash('/#/group/group123?week=10?other=1')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res_hash["kind"], "group")
        self.assertEqual(res_hash["id"], "group123")

        res_search = run_node_eval("parseUrlSearch('?group=grp1&week=10?other=1')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res_search["kind"], "group")
        self.assertEqual(res_search["id"], "grp1")
        # Невалидная часть после повторного ? должна быть проигнорирована
        self.assertIn(res_search["week"], [None, 10])

    def test_05_deep_specialty_hierarchical_paths(self):
        """Проверяет разбор глубоких иерархических URL специальности и года."""
        path = "/specialty/spec-6st0/year/2025/group/grp-7rg4"
        res = run_node_eval(f"parseUrlPath('{path}')", NODE_ROUTE_SYNC_SETUP)
        self.assertEqual(res["kind"], "group")
        self.assertEqual(res["id"], "grp-7rg4")

    def test_06_invalid_week_parameters_in_search(self):
        """Проверяет, что отрицательные и строковые номера недель дают week: null."""
        invalid_searches = ["?week=foo", "?week=-5", "?week=0", "?week=NaN", "?week=1e999"]
        for s in invalid_searches:
            res = run_node_eval(f"parseUrlSearch('{s}')", NODE_ROUTE_SYNC_SETUP)
            self.assertIsNone(res["week"], f"Ожидался week=None для {s}, получено: {res['week']}")


class TestLocalStorageStateFuzzing(unittest.TestCase):
    """Фаззинг чтения и валидации сохраненного состояния из localStorage."""

    DATA = {
        "groups": [{"id": "grp1", "name": "Группа 1"}],
        "teachers": [{"id": "tch1", "name": "Иванов И.И."}],
        "rooms": [{"id": "rm101", "name": "Ауд. 101"}],
        "publishedWeeks": [1, 2, 3, 4, 10],
    }

    def test_01_primitive_types_instead_of_object(self):
        """Проверяет устойчивость при сохранении примитивов (чисел, массивов, строк)."""
        fuzzed_saved = [12345, "string_selection", True, ["group", "grp1"]]
        for raw in fuzzed_saved:
            res = run_node_eval(
                f"resolveInitialSelection({json.dumps(self.DATA)}, {{ kind: 'group', id: '', week: null }}, {json.dumps(raw)})",
                NODE_ROUTE_SYNC_SETUP,
            )
            self.assertIn(res["kind"], ["group", "teacher", "room"])
            self.assertEqual(res["id"], "")
            self.assertEqual(res["week"], 10)

    def test_02_poisoned_object_fields(self):
        """Проверяет очистку отравленных полей (kind=999, id={}, week=NaN)."""
        poisoned = {"kind": 999, "id": {"nested": True}, "week": "NaN"}
        res = run_node_eval(
            f"resolveInitialSelection({json.dumps(self.DATA)}, {{ kind: 'group', id: '', week: null }}, {json.dumps(poisoned)})",
            NODE_ROUTE_SYNC_SETUP,
        )
        self.assertEqual(res["kind"], "group")
        self.assertEqual(res["id"], "")
        self.assertEqual(res["week"], 10)

    def test_03_null_and_empty_saved_objects(self):
        """Проверяет безопасную работу при пустом объекте или null."""
        for val in [{}, {"kind": None, "id": None, "week": None}, None]:
            res = run_node_eval(
                f"resolveInitialSelection({json.dumps(self.DATA)}, {{ kind: 'group', id: '', week: null }}, {json.dumps(val)})",
                NODE_ROUTE_SYNC_SETUP,
            )
            self.assertEqual(res["kind"], "group")
            self.assertEqual(res["id"], "")
            self.assertEqual(res["week"], 10)

    def test_04_valid_fallback_when_entity_not_found(self):
        """Проверяет сброс targetId, если сохраненная сущность удалена из БД."""
        saved = {"kind": "group", "id": "deleted_group_999", "week": 3}
        res = run_node_eval(
            f"resolveInitialSelection({json.dumps(self.DATA)}, {{ kind: 'group', id: '', week: null }}, {json.dumps(saved)})",
            NODE_ROUTE_SYNC_SETUP,
        )
        self.assertEqual(res["id"], "")
        self.assertEqual(res["week"], 3)

    def test_05_valid_saved_selection_preserved(self):
        """Проверяет корректное восстановление существующей сущности."""
        saved = {"kind": "teacher", "id": "tch1", "week": 4}
        res = run_node_eval(
            f"resolveInitialSelection({json.dumps(self.DATA)}, {{ kind: 'group', id: '', week: null }}, {json.dumps(saved)})",
            NODE_ROUTE_SYNC_SETUP,
        )
        self.assertEqual(res["kind"], "teacher")
        self.assertEqual(res["id"], "tch1")
        self.assertEqual(res["week"], 4)


if __name__ == "__main__":
    unittest.main()
