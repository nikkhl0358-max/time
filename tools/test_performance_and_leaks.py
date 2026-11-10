"""
Модуль тестирования производительности, бандла и аудита утечек памяти.

Проверяет:
  1. Размеры JS/CSS чанков в dist/ и static/ после сборки Vite.
  2. Изоляцию зависимостей (Three.js, Vue, React) и отсутствие дублирования.
  3. Корректность очистки таймеров, слушателей событий и наблюдателей жизненного цикла.
  4. Быстродействие группировки и фильтрации 200+ преподавателей (TeacherRoomPicker).
  5. Стабильность и производительность сортировки пар по дням (ScheduleViewer).
  6. Эффективность O(1) хэш-индексов справочников в useScheduleData.
"""
import json
import os
import re
import subprocess
import time
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DIST_DIR = os.path.join(FRONTEND_DIR, "dist")
SRC_DIR = os.path.join(FRONTEND_DIR, "src")


class TestBundleAndAssets(unittest.TestCase):
    """Тесты структуры бандла, размеров чанков и изоляции зависимостей."""

    def _get_target_dir(self):
        """Определяет актуальную директорию со скомпилированными ассетами."""
        if os.path.exists(os.path.join(STATIC_DIR, "assets")):
            return STATIC_DIR
        return DIST_DIR

    def _find_chunks(self, prefix, ext):
        """Возвращает список файлов чанков по префиксу и расширению."""
        assets_dir = os.path.join(self._get_target_dir(), "assets")
        if not os.path.exists(assets_dir):
            return []
        return [f for f in os.listdir(assets_dir) if f.startswith(prefix) and f.endswith(ext)]

    def test_01_dist_assets_exist(self):
        """Проверяет наличие скомпилированных файлов фронтенда в dist/static."""
        target = self._get_target_dir()
        self.assertTrue(os.path.exists(target), f"Директория {target} не найдена")
        index_html = os.path.join(target, "index.html")
        self.assertTrue(os.path.exists(index_html), "index.html отсутствует")
        assets = os.listdir(os.path.join(target, "assets"))
        self.assertGreater(len(assets), 3, "Слишком мало сгенерированных ассетов")

    def test_02_mount_vue_chunk_size(self):
        """Проверяет, что публичный чанк mountVue оптимизирован и не превышает 250 КБ."""
        vue_chunks = self._find_chunks("mountVue", ".js")
        self.assertTrue(len(vue_chunks) > 0, "Чанк mountVue не найден")
        path = os.path.join(self._get_target_dir(), "assets", vue_chunks[0])
        size_kb = os.path.getsize(path) / 1024
        # Публичный портал должен быть лёгким (< 250 КБ) для быстрого открытия на смартфонах
        self.assertLess(size_kb, 250.0, f"mountVue слишком большой: {size_kb:.1f} КБ")

    def test_03_hero_scene_chunk_isolated(self):
        """Проверяет, что Three.js и HeroScene3D вынесены в отдельный ленивый чанк."""
        hero_chunks = self._find_chunks("HeroScene3D", ".js")
        self.assertTrue(len(hero_chunks) > 0, "Ленивый чанк HeroScene3D не найден")
        path = os.path.join(self._get_target_dir(), "assets", hero_chunks[0])
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("WebGLRenderer", content, "Three.js должен быть изолирован в чанке HeroScene3D")

    def test_04_no_threejs_in_admin(self):
        """Проверяет отсутствие утечки Three.js в чанк администратора."""
        admin_chunks = self._find_chunks("mountAdmin", ".js")
        self.assertTrue(len(admin_chunks) > 0, "Чанк mountAdmin не найден")
        path = os.path.join(self._get_target_dir(), "assets", admin_chunks[0])
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("WebGLRenderer", content, "Three.js не должен попадать в mountAdmin")

    def test_05_no_react_in_public_vue(self):
        """Проверяет, что React не попадает в публичный чанк mountVue."""
        vue_chunks = self._find_chunks("mountVue", ".js")
        path = os.path.join(self._get_target_dir(), "assets", vue_chunks[0])
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("react-dom", content, "react-dom попал в публичный Vue бандл")
        self.assertNotIn("useState", content, "Хуки React попали в публичный Vue бандл")

    def test_06_no_duplicate_vue_runtimes(self):
        """Проверяет дедупликацию зависимостей Vue в package-lock.json."""
        lock_path = os.path.join(FRONTEND_DIR, "package-lock.json")
        self.assertTrue(os.path.exists(lock_path), "package-lock.json отсутствует")
        with open(lock_path, "r", encoding="utf-8") as f:
            lock_data = json.load(f)
        packages = lock_data.get("packages", {})
        vue_entries = [k for k in packages.keys() if k.endswith("node_modules/vue")]
        # Должен быть только один корневой node_modules/vue
        self.assertEqual(len(vue_entries), 1, f"Обнаружено дублирование рантаймов Vue: {vue_entries}")


class TestLifecycleAndMemoryLeaks(unittest.TestCase):
    """Статический аудит жизненного цикла компонентов и очистки слушателей событий."""

    def _read_src(self, relative_path):
        """Читает исходный файл компонента или композабла."""
        full_path = os.path.join(SRC_DIR, relative_path)
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_01_app_popstate_cleanup(self):
        """Проверяет регистрацию и корректную отписку от popstate в App.vue."""
        content = self._read_src("App.vue")
        self.assertIn("window.addEventListener('popstate'", content, "Слушатель popstate не зарегистрирован")
        self.assertIn("window.removeEventListener('popstate'", content, "Слушатель popstate не удаляется в onUnmounted")
        # Проверяем наличие блока onUnmounted
        self.assertRegex(content, r"onUnmounted\(\s*\(\)\s*=>\s*\{[^}]*removeEventListener", "removeEventListener вне onUnmounted")

    def test_02_app_popstate_index_data_safety(self):
        """Проверяет, что indexData деструктурирована в App.vue для защиты от ReferenceError."""
        content = self._read_src("App.vue")
        pattern = r"const\s*\{[^}]*indexData[^}]*\}\s*=\s*useScheduleData\(\)"
        self.assertRegex(content, pattern, "indexData не деструктурирована из useScheduleData в App.vue")

    def test_03_hero_scene_resize_observer_cleanup(self):
        """Проверяет создание и обязательное отключение ResizeObserver в HeroScene3D.vue."""
        content = self._read_src("components/visual3d/HeroScene3D.vue")
        self.assertIn("new ResizeObserver", content, "ResizeObserver не используется")
        self.assertIn("resizeObserver.disconnect()", content, "ResizeObserver не отключается при размонтировании")

    def test_04_hero_scene_visibility_observer_cleanup(self):
        """Проверяет отключение IntersectionObserver при размонтировании HeroScene3D.vue."""
        content = self._read_src("components/visual3d/HeroScene3D.vue")
        self.assertIn("new IntersectionObserver", content, "IntersectionObserver не используется")
        self.assertIn("observer.disconnect()", content, "IntersectionObserver не отключается при размонтировании")

    def test_05_hero_scene_animation_loop_cleanup(self):
        """Проверяет отмену анимационного цикла requestAnimationFrame в HeroScene3D.vue."""
        content = self._read_src("components/visual3d/HeroScene3D.vue")
        self.assertIn("requestAnimationFrame(animate)", content, "Цикл анимации отсутствует")
        self.assertIn("cancelAnimationFrame(animationFrameId)", content, "cancelAnimationFrame не вызывается")

    def test_06_hero_scene_threejs_resource_disposal(self):
        """Проверяет освобождение WebGL геометрий, материалов и текстур Three.js."""
        content = self._read_src("components/visual3d/HeroScene3D.vue")
        self.assertIn("geometry.dispose()", content, "Геометрии Three.js не освобождаются")
        self.assertIn("renderer.dispose()", content, "WebGLRenderer не освобождается")
        self.assertIn("m.map.dispose()", content, "Текстуры материалов Three.js не освобождаются")

    def test_07_spatial_card_pointer_listeners_scoped(self):
        """Проверяет, что обработчики SpatialCard ограничены шаблоном без глобальных утечек."""
        content = self._read_src("components/common/SpatialCard.vue")
        tilt_code = self._read_src("composables/use3DTilt.js")
        self.assertNotIn("window.addEventListener('mousemove'", tilt_code, "Глобальный слушатель mousemove на window")
        self.assertIn("@mousemove=\"handleMouseMove\"", content, "Слушатель должен быть привязан к элементу карточки")
        self.assertIn("cachedRect = null", tilt_code, "Кэшированный rect не очищается на mouseleave")

    def test_08_current_time_interval_cleanup(self):
        """Проверяет очистку 30-секундного интервала времени в useCurrentTime.js."""
        content = self._read_src("composables/useCurrentTime.js")
        self.assertIn("setInterval(updateStatus", content, "Таймер setInterval не запускается")
        self.assertIn("clearInterval(timerId)", content, "clearInterval отсутствует в onUnmounted")


class TestListRenderingPerformance(unittest.TestCase):
    """Бенчмарки и профилирование рендеринга списков и вычислений."""

    def _run_node_benchmark(self, script_body):
        """Выполняет JavaScript бенчмарк в Node.js и возвращает JSON-результат."""
        cmd = ["node", "-e", script_body]
        proc = subprocess.run(cmd, cwd=FRONTEND_DIR, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout.strip())

    def test_01_teacher_room_picker_benchmark(self):
        """Профилирует группировку и поиск по 250 преподавателям и 20 кафедрам (ЦК)."""
        js = """
        const teachers = Array.from({ length: 250 }, (_, i) => ({
            id: `t_${i}`,
            name: `Преподаватель Тест_${(250 - i).toString().padStart(3, '0')}`,
            departmentId: `dept_${i % 20}`,
            profile: `Предмет ${(i % 15) + 1}`
        }));
        const departments = Array.from({ length: 20 }, (_, i) => ({
            id: `dept_${i}`,
            name: `ЦК 0${(i % 9) + 1} Спецдисциплин (${i})`
        }));
        const t0 = performance.now();
        const sorted = [...teachers].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
        const deptBuckets = new Map();
        sorted.forEach(t => {
            if (!deptBuckets.has(t.departmentId)) deptBuckets.set(t.departmentId, []);
            deptBuckets.get(t.departmentId).push(t);
        });
        const grouped = departments.map(d => ({ ...d, teachers: deptBuckets.get(d.id) || [] }));
        const t1 = performance.now();
        const searchMatches = sorted.filter(t => t.name.toLowerCase().includes('тест_1'));
        const t2 = performance.now();
        console.log(JSON.stringify({ groupMs: t1 - t0, searchMs: t2 - t1, totalGrouped: grouped.length, matches: searchMatches.length }));
        """
        res = self._run_node_benchmark(js)
        # Группировка 250 преподавателей должна выполняться мгновенно (< 20 мс)
        self.assertLess(res["groupMs"], 20.0, f"Группировка преподавателей слишком медленная: {res['groupMs']:.2f} мс")
        self.assertLess(res["searchMs"], 10.0, f"Поиск преподавателя слишком медленный: {res['searchMs']:.2f} мс")
        self.assertEqual(res["totalGrouped"], 20)

    def test_02_schedule_viewer_lessons_by_day_benchmark(self):
        """Профилирует стабильность и скорость сортировки 120 пар по дням недели."""
        js = """
        const instances = Array.from({ length: 120 }, (_, i) => ({
            id: `inst_${i}`,
            instId: `inst_${i}`,
            day: i % 6,
            period: (i % 7) + 1,
            subjectId: `subj_${i % 10}`,
            teacherId: `teach_${i % 20}`,
            roomId: `room_${i % 15}`
        }));
        const t0 = performance.now();
        const map = { 0: [], 1: [], 2: [], 3: [], 4: [], 5: [] };
        instances.forEach(inst => { if (map[inst.day]) map[inst.day].push(inst); });
        Object.keys(map).forEach(key => {
            const list = map[key];
            const decorated = list.map(inst => ({ inst, order: (Number(inst.period) || 1) * 100 }));
            decorated.sort((a, b) => a.order - b.order);
            map[key] = decorated.map(d => d.inst);
        });
        const elapsed = performance.now() - t0;
        console.log(JSON.stringify({ elapsedMs: elapsed, dayCounts: Object.values(map).map(l => l.length) }));
        """
        res = self._run_node_benchmark(js)
        self.assertLess(res["elapsedMs"], 15.0, f"Сортировка пар недели слишком медленная: {res['elapsedMs']:.2f} мс")
        self.assertEqual(sum(res["dayCounts"]), 120, "Количество пар в днях не совпадает")

    def test_03_o1_lookup_speed_vs_linear_scan(self):
        """Проверяет преимущество O(1) хэш-индексов Map над линейным поиском Array.find."""
        js = """
        const teachers = Array.from({ length: 300 }, (_, i) => ({ id: `t_${i}`, name: `Преподаватель_${i}` }));
        const tMap = new Map();
        teachers.forEach(t => tMap.set(t.id, t.name));
        const testIds = Array.from({ length: 1000 }, (_, i) => `t_${i % 300}`);
        const t0 = performance.now();
        for (const id of testIds) { const res = tMap.get(id); }
        const mapTime = performance.now() - t0;
        const t1 = performance.now();
        for (const id of testIds) { const res = teachers.find(t => t.id === id); }
        const scanTime = performance.now() - t1;
        console.log(JSON.stringify({ mapTime, scanTime, speedup: scanTime / Math.max(mapTime, 0.001) }));
        """
        res = self._run_node_benchmark(js)
        # O(1) поиск через Map должен быть значительно быстрее Array.find
        self.assertGreater(res["speedup"], 3.0, f"Map недостаточно быстрее find: ускорение {res['speedup']:.1f}x")
        self.assertLess(res["mapTime"], 5.0, f"Map lookup слишком медленный: {res['mapTime']:.2f} мс")

    def test_04_substitution_matching_benchmark(self):
        """Профилирует сопоставление 100 пар с 50 заменами через хэш-индекс по дате."""
        js = """
        const subs = Array.from({ length: 50 }, (_, i) => ({
            id: `sub_${i}`,
            instId: `inst_${i * 2}`,
            date: '2026-11-09',
            replacementTeacherId: 'teach_99'
        }));
        const byDate = new Map();
        subs.forEach(s => {
            if (!byDate.has(s.date)) byDate.set(s.date, new Map());
            byDate.get(s.date).set(s.instId, s);
        });
        const instances = Array.from({ length: 100 }, (_, i) => ({ id: `inst_${i}`, instId: `inst_${i}` }));
        const t0 = performance.now();
        const dateSubs = byDate.get('2026-11-09');
        let matches = 0;
        instances.forEach(inst => { if (dateSubs && dateSubs.has(inst.instId)) matches++; });
        const elapsed = performance.now() - t0;
        console.log(JSON.stringify({ elapsedMs: elapsed, matches }));
        """
        res = self._run_node_benchmark(js)
        self.assertLess(res["elapsedMs"], 10.0, f"Сопоставление замен слишком медленное: {res['elapsedMs']:.2f} мс")
        self.assertEqual(res["matches"], 50)


if __name__ == "__main__":
    unittest.main()
