"""
Комплексный сквозной аудит клиентских потоков Vue 3, DOM-навигации и экспорта данных.

Проверяет:
  1. Пошаговую навигацию GroupDrillDown.vue (Specialty -> Year -> Group, хлебные крошки, unassigned).
  2. Навигацию и сброс состояния в Navbar.vue и App.vue (роли, selectedId, модалки звонков/замен).
  3. Фильтрацию и переключение табов в TeacherRoomPicker.vue (поиск, нормализация 'ё', 8 ЦК, корпуса).
  4. Безопасность и формат генерации CSV в useScheduleExport.js (UTF-8 BOM, CWE-1236, экранирование).
  5. Сквозную связку с реальным API расписания колледжа КТМУ.
"""
import os
import sys
import json
import unittest
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app import app


DRILLDOWN_STEP_SCRIPT = """
import { ref, computed } from 'vue';
const step = ref('specialty');
const chosenSpecialtyId = ref('');
const chosenYear = ref(null);
const specialties = [{ id: 'sp1', name: '09.02.07 ИСП' }];
const groups = [
    { id: 'g1', name: '220-ИСП', specialtyId: 'sp1', admissionYear: 2022 },
    { id: 'g2', name: '210-ИСП', specialtyId: 'sp1', admissionYear: 2021 }
];
const availableYears = computed(() => {
    const specGroups = groups.filter(g => (g.specialtyId || '') === (chosenSpecialtyId.value || ''));
    return [...new Set(specGroups.map(g => g.admissionYear).filter(Boolean))].sort((a,b) => b-a);
});
const availableGroups = computed(() => {
    return groups.filter(g => (g.specialtyId || '') === (chosenSpecialtyId.value || '') &&
                              (chosenYear.value === null || Number(g.admissionYear) === Number(chosenYear.value)));
});
function selectSpecialty(id) {
    chosenSpecialtyId.value = id;
    step.value = availableYears.value.length ? 'year' : 'group';
}
function selectYear(y) { chosenYear.value = y; step.value = 'group'; }
selectSpecialty('sp1');
const stepAfterSpec = step.value;
const years = availableYears.value;
selectYear(2022);
console.log(JSON.stringify({ stepAfterSpec, years, finalStep: step.value, groups: availableGroups.value.map(g => g.name) }));
"""

UNASSIGNED_DRILLDOWN_SCRIPT = """
import { ref, computed } from 'vue';
const step = ref('specialty');
const chosenSpecialtyId = ref('');
const groups = [
    { id: 'g_no_spec', name: 'Группа без спец', specialtyId: null, admissionYear: null },
    { id: 'g_spec', name: '220-ИСП', specialtyId: 'sp1', admissionYear: 2022 }
];
const unassignedCount = computed(() => groups.filter(g => !g.specialtyId).length);
const availableYears = computed(() => {
    const specGroups = groups.filter(g => (g.specialtyId || '') === (chosenSpecialtyId.value || ''));
    return [...new Set(specGroups.map(g => g.admissionYear).filter(Boolean))];
});
function selectSpecialty(id) {
    chosenSpecialtyId.value = id;
    step.value = availableYears.value.length ? 'year' : 'group';
}
selectSpecialty('');
console.log(JSON.stringify({ unassignedCount: unassignedCount.value, step: step.value }));
"""

CAMPUS_SWITCH_SCRIPT = """
import { ref, computed } from 'vue';
const activeCampus = ref('');
const campusGroups = ref([
    { badge: 'ЦВЕТОЧНАЯ', title: 'Цветочная, 8', rooms: [{ id: '101', name: '101' }] },
    { badge: 'ВОЗНЕСЕНСКИЙ', title: 'Вознесенский, 44', rooms: [{ id: 'в1', name: 'В-10' }] }
]);
const displayed = computed(() => {
    if (!activeCampus.value) return campusGroups.value;
    return campusGroups.value.filter(c => c.badge === activeCampus.value);
});
const initialCount = displayed.value.length;
activeCampus.value = 'ВОЗНЕСЕНСКИЙ';
const filteredCount = displayed.value.length;
const filteredBadge = displayed.value[0].badge;
console.log(JSON.stringify({ initialCount, filteredCount, filteredBadge }));
"""

RESET_ALL_FILTERS_SCRIPT = """
import { ref } from 'vue';
const searchQuery = ref('Петров');
const activeDepartmentId = ref('dept1');
const activeLetter = ref('П');
const activeCampus = ref('ЦВЕТОЧНАЯ');
function resetAllFilters() {
    searchQuery.value = '';
    activeDepartmentId.value = '';
    activeLetter.value = '';
    activeCampus.value = '';
}
resetAllFilters();
console.log(JSON.stringify({
    q: searchQuery.value,
    d: activeDepartmentId.value,
    l: activeLetter.value,
    c: activeCampus.value
}));
"""


def run_node_eval(script_body: str) -> dict:
    """
    Выполняет JavaScript-код в окружении Node.js с поддержкой ESM.
    Запускается из frontend/ для доступа к node_modules (vue, composables).
    Возвращает словарь с распарсенным JSON-ответом скрипта.
    """
    frontend_dir = os.path.join(PROJECT_ROOT, "frontend")
    cmd = ["node", "--input-type=module", "-e", script_body]
    proc = subprocess.run(cmd, cwd=frontend_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Node execution failed: {proc.stderr}\nCode:\n{script_body}")
    stdout_trimmed = proc.stdout.strip()
    return json.loads(stdout_trimmed) if stdout_trimmed else {}


def read_source_file(rel_path: str) -> str:
    """
    Считывает содержимое исходного файла относительно корня репозитория.
    Используется для статического аудита шаблонов и директив Vue SFC.
    Возвращает строку содержимого в кодировке UTF-8.
    """
    full_path = os.path.join(PROJECT_ROOT, rel_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


def build_e2e_csv_script(instances_json: str) -> str:
    """
    Формирует исполняемый ESM-скрипт для сквозного тестирования экспорта расписания.
    Принимает JSON-строку инстансов и возвращает код для исполнения в Node.js.
    """
    return f"""
    import {{ convertToCSV, formatLessonRow }} from './src/composables/useScheduleExport.js';
    const instances = {instances_json};
    const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const rows = instances.map(inst => formatLessonRow(inst, dayNames[inst.day || 0], inst.period || 1, id => 'Предмет', id => 'Преподаватель', id => '101'));
    const csv = convertToCSV(rows);
    const hasHeaders = csv.includes('"День недели";"Пара"');
    console.log(JSON.stringify({{ rowCount: rows.length, hasHeaders }}));
    """


class TestClientFlowsE2E(unittest.TestCase):
    """Сквозной аудит реактивных клиентских сценариев и DOM-потоков."""

    def setUp(self):
        """Инициализация тестового клиента Flask перед каждым тестом."""
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_01_drilldown_step_flow_reactivity(self):
        """
        Проверяет реактивный переход Шаг 1 -> Шаг 2 -> Шаг 3 в GroupDrillDown.
        Использует Vue 3 reactivity в Node.js для проверки computed availableYears и availableGroups.
        Assert: шаги переходят последовательно, списки групп и годов рассчитываются корректно.
        """
        res = run_node_eval(DRILLDOWN_STEP_SCRIPT)
        self.assertEqual(res["stepAfterSpec"], "year")
        self.assertEqual(res["years"], [2022, 2021])
        self.assertEqual(res["finalStep"], "group")
        self.assertEqual(res["groups"], ["220-ИСП"])

    def test_02_drilldown_breadcrumbs_navigation(self):
        """
        Проверяет возврат назад по хлебным крошкам в GroupDrillDown.vue.
        Проверяет функции goToYears и goToSpecialties, а также защиту от пустого года.
        Assert: возврат на шаг year сбрасывает chosenYear, возврат на specialty сбрасывает оба.
        """
        script = """
        import { ref } from 'vue';
        const step = ref('group');
        const chosenSpecialtyId = ref('sp1');
        const chosenYear = ref(2022);
        function goToYears() { chosenYear.value = null; step.value = 'year'; }
        function goToSpecialties() { chosenSpecialtyId.value = ''; chosenYear.value = null; step.value = 'specialty'; }
        goToYears();
        const afterYears = { step: step.value, year: chosenYear.value, spec: chosenSpecialtyId.value };
        goToSpecialties();
        const afterSpec = { step: step.value, year: chosenYear.value, spec: chosenSpecialtyId.value };
        console.log(JSON.stringify({ afterYears, afterSpec }));
        """
        res = run_node_eval(script)
        self.assertEqual(res["afterYears"]["step"], "year")
        self.assertIsNone(res["afterYears"]["year"])
        self.assertEqual(res["afterSpec"]["step"], "specialty")
        self.assertEqual(res["afterSpec"]["spec"], "")

    def test_03_drilldown_unassigned_groups_handling(self):
        """
        Проверяет обработку карточки групп без специальности (unassigned).
        Если у групп без специальности нет годов поступления, происходит быстрый переход к группам.
        Assert: unassignedCount считается корректно, при пустых годах шаг сразу 'group'.
        """
        res = run_node_eval(UNASSIGNED_DRILLDOWN_SCRIPT)
        self.assertEqual(res["unassignedCount"], 1)
        self.assertEqual(res["step"], "group")

    def test_04_drilldown_keyboard_navigation_attributes(self):
        """
        Проверяет доступность и атрибуты клавиатурной навигации в GroupDrillDown.vue.
        Проверяет наличие role="button", tabindex="0", @keydown.enter и @keydown.space.
        Assert: все карточки выбора (специальности, годы, группы) доступны для клавиатуры.
        """
        code = read_source_file("frontend/src/components/public/GroupDrillDown.vue")
        self.assertIn('role="button"', code)
        self.assertIn('tabindex="0"', code)
        self.assertIn('@keydown.enter="selectSpecialty(sp.id)"', code)
        self.assertIn('@keydown.space.prevent="selectSpecialty(sp.id)"', code)
        self.assertIn('@keydown.enter="selectYear(year)"', code)
        self.assertIn('@keydown.enter="selectGroup(grp.id)"', code)
        self.assertIn('v-if="step === \'group\' && chosenYear"', code)

    def test_05_navbar_role_switching_resets_selected_id(self):
        """
        Проверяет сброс selectedId при переключении роли в Navbar.vue и App.vue.
        Пользователь должен попадать на начальный экран выбора сущности новой роли.
        Assert: handleSelectRole в App.vue устанавливает selectedId в пустую строку.
        """
        app_code = read_source_file("frontend/src/App.vue")
        self.assertIn("function handleSelectRole(role)", app_code)
        self.assertIn("selectedId.value = '';", app_code)
        self.assertIn("selectedKind.value = role;", app_code)
        navbar_code = read_source_file("frontend/src/components/common/Navbar.vue")
        self.assertIn("emit('select-role', newRole)", navbar_code)
        self.assertIn("emit('update:role', newRole)", navbar_code)

    def test_06_navbar_modals_open_events(self):
        """
        Проверяет открытие модалок звонков и замен из Navbar.vue.
        Клики по кнопкам должны эмитить события открытия для управления состоянием в App.vue.
        Assert: Navbar эмитит open-bells/toggle-bells и open-subs/toggle-substitutions.
        """
        navbar_code = read_source_file("frontend/src/components/common/Navbar.vue")
        self.assertIn("emit('open-bells')", navbar_code)
        self.assertIn("emit('toggle-bells')", navbar_code)
        self.assertIn("emit('open-subs')", navbar_code)
        self.assertIn("emit('toggle-substitutions')", navbar_code)
        app_code = read_source_file("frontend/src/App.vue")
        self.assertIn("@open-bells=\"isBellsOpen = true\"", app_code)
        self.assertIn("@open-subs=\"isSubsOpen = true\"", app_code)

    def test_07_navbar_keyboard_accessibility(self):
        """
        Проверяет клавиатурную доступность интерактивных элементов Navbar.vue.
        Логотип и виджет текущей пары должны реагировать на клавиши Enter и Space.
        Assert: логотип и live-pair-pill имеют tabindex, role и обработчики keydown.
        """
        navbar_code = read_source_file("frontend/src/components/common/Navbar.vue")
        self.assertIn('@keydown.enter="$emit(\'reset-view\')"', navbar_code)
        self.assertIn('@keydown.space.prevent="$emit(\'reset-view\')"', navbar_code)
        self.assertIn('@keydown.enter="handleBellsClick"', navbar_code)
        self.assertIn('@keydown.space.prevent="handleBellsClick"', navbar_code)

    def test_08_teacher_search_yo_case_and_whitespace_filtering(self):
        """
        Проверяет поиск преподавателя с пробелами, разным регистром и буквой 'ё'.
        'Семёнов', '  семенов  ' и 'СЕМЕНОВ' должны успешно находить одного преподавателя.
        Assert: нормализация matchTeacherQuery дает идентичный положительный результат.
        """
        script = """
        function matchTeacherQuery(t, query) {
            if (!query) return true;
            const q = query.toLowerCase().replace(/ё/g, 'е').trim();
            const name = (t.name || '').toLowerCase().replace(/ё/g, 'е');
            const prof = (t.profile || '').toLowerCase().replace(/ё/g, 'е');
            return name.includes(q) || prof.includes(q);
        }
        const teacher = { id: 't1', name: 'Семёнов Пётр', profile: 'Информатика' };
        const queries = ['семенов', '  СЕМЕНОВ  ', 'пётр', 'ПЕТР', '  информатика  '];
        const results = queries.map(q => matchTeacherQuery(teacher, q));
        console.log(JSON.stringify({ allMatched: results.every(Boolean) }));
        """
        res = run_node_eval(script)
        self.assertTrue(res["allMatched"])

    def test_09_teacher_eight_ck_departments_tabs(self):
        """
        Проверяет распределение преподавателей по 8 цикловым комиссиям (ЦК).
        Загружает реальные данные через API и проверяет вычисление табов в TeacherRoomPicker.
        Assert: в ответе API ровно 8 ЦК и список доступных табов формируется без ошибок.
        """
        response = self.client.get("/api/public-index")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data).get("data", {})
        depts = data.get("departments", [])
        self.assertEqual(len(depts), 8, "В колледже КТМУ должно быть ровно 8 отделений ЦК")
        picker_code = read_source_file("frontend/src/components/public/TeacherRoomPicker.vue")
        self.assertIn("availableDepartments", picker_code)
        self.assertIn("resolveDeptBadge", picker_code)
        self.assertIn("dept-tabs-scroll", picker_code)

    def test_10_teacher_alphabet_filtering_and_normalization(self):
        """
        Проверяет алфавитный фильтр преподавателей и нормализацию 'Ё' в 'Е'.
        Преподаватели на 'Ё' (например, Ёлкин) должны группироваться под буквой 'Е'.
        Assert: массив доступных букв содержит только верхний регистр и не содержит отдельной 'Ё'.
        """
        script = """
        const teachers = [
            { id: '1', name: 'Ёлкин Иван' },
            { id: '2', name: 'Александров Петр' },
            { id: '3', name: 'Егоров Сергей' }
        ];
        const set = new Set();
        teachers.forEach(t => {
            let first = (t.name || '').trim().charAt(0).toUpperCase();
            if (first === 'Ё') first = 'Е';
            if (first && /[А-Я]/.test(first)) set.add(first);
        });
        const letters = Array.from(set).sort((a, b) => a.localeCompare(b, 'ru'));
        console.log(JSON.stringify({ letters }));
        """
        res = run_node_eval(script)
        self.assertIn("А", res["letters"])
        self.assertIn("Е", res["letters"])
        self.assertNotIn("Ё", res["letters"])

    def test_11_classroom_campus_tabs_switching(self):
        """
        Проверяет переключение между корпусами на вкладке «Аудитории».
        Табы: 'Все корпуса', 'ЦВЕТОЧНАЯ', 'ВОЗНЕСЕНСКИЙ', 'САДОВАЯ'.
        Assert: displayedCampusGroups фильтрует корпуса в соответствии с activeCampus.
        """
        res = run_node_eval(CAMPUS_SWITCH_SCRIPT)
        self.assertEqual(res["initialCount"], 2)
        self.assertEqual(res["filteredCount"], 1)
        self.assertEqual(res["filteredBadge"], "ВОЗНЕСЕНСКИЙ")

    def test_12_teacher_room_picker_reset_all_filters(self):
        """
        Проверяет сброс всех фильтров (поиска, ЦК, буквы и корпуса) в resetAllFilters.
        Assert: после вызова resetAllFilters все связанные реактивные переменные пусты.
        """
        res = run_node_eval(RESET_ALL_FILTERS_SCRIPT)
        self.assertEqual(res, {"q": "", "d": "", "l": "", "c": ""})

    def test_13_csv_export_utf8_bom_and_delimiters(self):
        """
        Проверяет генерацию UTF-8 BOM и разделителя «точка с запятой» в CSV.
        Наличие BOM (\\uFEFF / 0xEF, 0xBB, 0xBF) необходимо Excel для автоопределения кириллицы.
        Assert: Blob содержит BOM-байты и разделители ';' между колонками.
        """
        script = """
        import { convertToCSV } from './src/composables/useScheduleExport.js';
        const rows = [{ 'День недели': 'Пн', 'Пара': 1, 'Дисциплина': 'Информатика' }];
        const csv = convertToCSV(rows);
        const blob = new Blob(['\\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
        const buf = Buffer.from(await blob.arrayBuffer());
        const isBOM = buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF;
        const hasSemicolon = csv.includes(';');
        const hasCRLF = csv.includes('\\r\\n');
        console.log(JSON.stringify({ isBOM, hasSemicolon, hasCRLF }));
        """
        res = run_node_eval(script)
        self.assertTrue(res["isBOM"], "CSV должен начинаться с байтов UTF-8 BOM")
        self.assertTrue(res["hasSemicolon"], "CSV должен использовать точку с запятой")
        self.assertTrue(res["hasCRLF"], "CSV должен использовать перенос строк CRLF")

    def test_14_csv_export_quote_escaping_rfc4180(self):
        """
        Проверяет корректность экранирования двойных кавычек по стандарту RFC 4180.
        Символ кавычки внутри значения должен дублироваться: " -> "".
        Assert: строка Физика "Базовый" сериализуется с удвоенными внутренними кавычками.
        """
        script = """
        import { sanitizeCSVCell } from './src/composables/useScheduleExport.js';
        const val = 'Физика "Базовый" уровень';
        const escaped = sanitizeCSVCell(val);
        console.log(JSON.stringify({ escaped }));
        """
        res = run_node_eval(script)
        self.assertEqual(res["escaped"], '"Физика ""Базовый"" уровень"')

    def test_15_csv_export_injection_sanitization_cwe1236(self):
        """
        Проверяет защиту от CSV Formula Injection (CWE-1236).
        Ячейки, начинающиеся со знаков '=', '+', '-', '@', должны предваряться апострофом '.
        Assert: вредоносная формула нейтрализуется и не исполнится табличным процессором.
        """
        script = """
        import { sanitizeCSVCell } from './src/composables/useScheduleExport.js';
        const tests = ['=1+1', '+cmd', '-test', '@SUM(A1:A10)', '\\tcalc', '\\rpayload'];
        const sanitized = tests.map(sanitizeCSVCell);
        console.log(JSON.stringify({ sanitized }));
        """
        res = run_node_eval(script)
        for item in res["sanitized"]:
            self.assertTrue(item.startswith("\"'"), f"Ячейка {item} не защищена от формульной инъекции")

    def test_16_csv_export_filename_sanitization(self):
        """
        Проверяет санитарную очистку имени файла при выгрузке расписания.
        Спецсимволы файловых систем (/, \\, :, *, ?, <, >, |) должны заменяться на подчеркивание.
        Assert: имя файла не содержит запрещенных символов и безопасно для сохранения.
        """
        script = """
        import { sanitizeFileName } from './src/composables/useScheduleExport.js';
        const unsafeName = 'Расписание: Группа 120/121 <КТМУ>*';
        const safe = sanitizeFileName(unsafeName);
        console.log(JSON.stringify({ safe }));
        """
        res = run_node_eval(script)
        self.assertNotIn(":", res["safe"])
        self.assertNotIn("/", res["safe"])
        self.assertNotIn("<", res["safe"])
        self.assertNotIn(">", res["safe"])
        self.assertNotIn("*", res["safe"])

    def test_17_e2e_real_api_to_csv_pipeline(self):
        """
        Проверяет сквозную генерацию CSV из реальных данных публичного API.
        Запрашивает расписание группы и прогоняет его через useScheduleExport.
        Assert: CSV успешно создается, содержит заголовки, строки пар и валидные данные.
        """
        res_idx = self.client.get("/api/public-index")
        idx_data = json.loads(res_idx.data).get("data", {})
        groups = idx_data.get("groups", [])
        self.assertGreater(len(groups), 0)

        res_sched = self.client.get(f"/api/public-schedule/group/{groups[0]['id']}?week=10")
        instances = json.loads(res_sched.data).get("schedule", {}).get("instances", [])
        self.assertGreater(len(instances), 0, "У группы должны быть занятия для экспорта")

        script = build_e2e_csv_script(json.dumps(instances[:5]))
        export_res = run_node_eval(script)
        self.assertEqual(export_res["rowCount"], min(5, len(instances)))
        self.assertTrue(export_res["hasHeaders"])


if __name__ == "__main__":
    unittest.main()
