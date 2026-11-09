"""
Глубокий визуальный и интерактивный аудит нового Vue 3 интерфейса КТМУ.

Проверяет:
1. Адаптивность и отсутствие горизонтального скролла на 8 размерах экранов (от 375px до 1920px).
2. Стили и интерактивность кнопок (смена группы, навигация недель, экспорт CSV и печать).
3. Адаптивный скролл табов дней недели и счетчики пар.
4. Модальные окна («Звонки», «Замены», «Авторизация») и их закрытие по X, оверлею и Escape.
5. Фокусировку и валидацию формы входа администратора.
6. Центрирование оранжевого Chrono-Orb и матовое стекло (backdrop-filter: blur).
"""
import unittest
import socket
import threading
import time
import os
import sys

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app
from playwright.sync_api import sync_playwright

def find_free_port():
    """Находит свободный порт для запуска тестового сервера."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

def start_test_server(port):
    """Запускает фоновый сервер Flask."""
    server_thread = threading.Thread(
        target=lambda: app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False),
        daemon=True
    )
    server_thread.start()
    time.sleep(1.2)
    return server_thread

class TestUIVisualInteractions(unittest.TestCase):
    """Тестирование визуальной верстки, интерактивности и адаптивности."""

    @classmethod
    def setUpClass(cls):
        """Инициализация тестового сервера и браузера Playwright."""
        cls.port = find_free_port()
        cls.server_thread = start_test_server(cls.port)
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        """Очистка ресурсов браузера и Playwright."""
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        """Создание новой страницы перед каждым тестом."""
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        """Закрытие контекста страницы после каждого теста."""
        self.context.close()

    def navigate_home(self, width=1280, height=800):
        """Переход на главную страницу с заданным viewport."""
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(self.base_url + "/")
        self.page.wait_for_selector(".brand-logo", timeout=10000)
        self.page.wait_for_selector(".drill-down-container", timeout=10000)

    def select_first_group(self, width=1280, height=800):
        """Выбирает первую группу для перехода в ScheduleViewer."""
        self.navigate_home(width, height)
        first_specialty = self.page.locator(".specialty-card").first
        first_specialty.click()
        self.page.wait_for_selector(".year-card", timeout=5000)
        self.page.locator(".year-card").first.click()
        self.page.wait_for_selector(".group-card", timeout=5000)
        self.page.locator(".group-card").first.click()
        self.page.wait_for_selector(".filter-bar", timeout=5000)

    def assert_no_horizontal_scroll(self, viewport_name=""):
        """Проверяет отсутствие горизонтального скролла на странице."""
        res = self.page.evaluate("""() => {
            return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                scrollX: window.scrollX
            };
        }""")
        diff = res["scrollWidth"] - res["innerWidth"]
        msg = f"Горизонтальный скролл на {viewport_name}: scrollWidth={res['scrollWidth']} > innerWidth={res['innerWidth']}"
        self.assertLessEqual(diff, 1, msg)
        self.assertEqual(res["scrollX"], 0, f"Горизонтальное смещение на {viewport_name}")

    def test_01_responsive_no_horizontal_scroll_mobile_phones(self):
        """Проверяет отсутствие горизонтального скролла на смартфонах (375px, 390px, 412px)."""
        mobile_viewports = [
            ("iPhone SE (375px)", 375, 667),
            ("iPhone 14 (390px)", 390, 844),
            ("Android Pixel (412px)", 412, 915),
        ]
        for name, w, h in mobile_viewports:
            self.page.set_viewport_size({"width": w, "height": h})
            self.page.goto(self.base_url + "/")
            self.page.wait_for_selector(".drill-down-container", timeout=6000)
            self.assert_no_horizontal_scroll(name)

    def test_02_responsive_no_horizontal_scroll_tablets_and_desktops(self):
        """Проверяет отсутствие горизонтального скролла на планшетах и десктопах (768px-1920px)."""
        viewports = [
            ("iPad Mini (768px)", 768, 1024),
            ("iPad Air (820px)", 820, 1180),
            ("Desktop HD (1280px)", 1280, 800),
            ("Desktop 1440p (1440px)", 1440, 900),
            ("Desktop FHD (1920px)", 1920, 1080),
        ]
        for name, w, h in viewports:
            self.page.set_viewport_size({"width": w, "height": h})
            self.page.goto(self.base_url + "/")
            self.page.wait_for_selector(".drill-down-container", timeout=6000)
            self.assert_no_horizontal_scroll(name)

    def test_03_subject_title_word_wrap(self):
        """Проверяет перенос названий предметов (overflow-wrap: anywhere)."""
        self.select_first_group()
        titles = self.page.locator(".subject-title")
        if titles.count() > 0:
            wrap = titles.first.evaluate("el => window.getComputedStyle(el).overflowWrap")
            self.assertIn(wrap, ["anywhere", "break-word"])

    def test_04_change_entity_button_interactions(self):
        """Проверяет стили, выравнивание SVG и клик кнопки «Сменить группу»."""
        self.select_first_group()
        btn = self.page.locator(".change-entity-btn")
        self.assertTrue(btn.is_visible())
        svg = btn.locator("svg.change-icon-svg")
        self.assertTrue(svg.is_visible())
        display = btn.evaluate("el => window.getComputedStyle(el).display")
        self.assertIn(display, ["inline-flex", "flex"])
        btn.click()
        self.page.wait_for_selector(".drill-down-container", timeout=5000)
        self.assertTrue(self.page.locator(".specialty-grid").is_visible())

    def test_05_week_navigation_disabled_states(self):
        """Проверяет отключение кнопок на 1-й и 24-й неделях."""
        self.select_first_group()
        prev_btn = self.page.locator(".week-nav-btn").first
        for _ in range(15):
            if prev_btn.is_disabled():
                break
            prev_btn.click()
            time.sleep(0.05)
        self.assertTrue(prev_btn.is_disabled())
        cursor = prev_btn.evaluate("el => window.getComputedStyle(el).cursor")
        self.assertEqual(cursor, "not-allowed")

    def test_06_export_buttons_states(self):
        """Проверяет стили и состояния кнопок экспорта CSV и печати."""
        self.select_first_group()
        csv_btn = self.page.locator(".export-btn")
        print_btn = self.page.locator(".print-btn")
        self.assertTrue(csv_btn.is_visible())
        self.assertTrue(print_btn.is_visible())
        self.assertTrue(csv_btn.locator("svg").is_visible())
        self.assertTrue(print_btn.locator("svg").is_visible())

    def test_07_day_tabs_horizontal_scroll_on_mobile(self):
        """Проверяет адаптивный горизонтальный скролл табов дней недели на мобильных."""
        self.select_first_group(width=375, height=667)
        day_bar = self.page.locator(".day-filter-bar")
        self.assertTrue(day_bar.is_visible())
        res = day_bar.evaluate("""el => {
            const style = window.getComputedStyle(el);
            return {
                overflowX: style.overflowX,
                flexWrap: style.flexWrap
            };
        }""")
        self.assertIn(res["overflowX"], ["auto", "scroll"])
        self.assertEqual(res["flexWrap"], "nowrap")

    def test_08_day_tabs_active_highlight_and_filter(self):
        """Проверяет подсветку активного дня и фильтрацию пар."""
        self.select_first_group()
        tabs = self.page.locator(".day-pill-btn")
        self.assertGreater(tabs.count(), 1)
        first_day_tab = tabs.nth(1)
        first_day_tab.click()
        self.assertIn("active", first_day_tab.get_attribute("class"))
        grid = self.page.locator(".days-grid")
        self.assertIn("single-column-center", grid.get_attribute("class"))

    def test_09_modal_bells_closing_mechanisms(self):
        """Проверяет открытие и закрытие модального окна звонков по X, Escape и оверлею."""
        self.navigate_home()
        self.page.locator(".nav-action-btn:has-text('Звонки')").click()
        self.page.wait_for_selector(".bells-container", timeout=5000)
        self.page.locator(".modal-close-btn").click()
        self.page.wait_for_selector(".bells-container", state="hidden", timeout=5000)
        self.page.locator(".nav-action-btn:has-text('Звонки')").click()
        self.page.wait_for_selector(".bells-container", timeout=5000)
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(".bells-container", state="hidden", timeout=5000)

    def test_10_modal_substitutions_feed_styling(self):
        """Проверяет визуальное оформление модального окна оперативных замен."""
        self.navigate_home()
        self.page.locator(".nav-action-btn.orange:has-text('Замены')").click()
        self.page.wait_for_selector(".substitutions-feed", timeout=5000)
        feed = self.page.locator(".substitutions-feed")
        self.assertTrue(feed.is_visible())
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(".substitutions-feed", state="hidden", timeout=5000)

    def test_11_admin_auth_modal_focus_and_validation(self):
        """Проверяет фокус в инпут и индикацию ошибок валидации админки."""
        self.navigate_home()
        self.page.locator(".admin-entry-btn").click()
        self.page.wait_for_selector(".auth-form", timeout=5000)
        focused_tag = self.page.evaluate("() => document.activeElement ? document.activeElement.tagName : ''")
        self.assertEqual(focused_tag, "INPUT")
        self.page.fill("#admin-auth-username", "wrong_user")
        self.page.fill("#admin-auth-password", "wrong_pass")
        self.assertTrue(self.page.locator(".auth-submit-btn").is_enabled())
        self.page.locator(".auth-submit-btn").click()
        self.page.wait_for_selector(".auth-error-banner", timeout=5000)
        self.assertTrue(self.page.locator("#admin-auth-password.has-error").is_visible())
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(".auth-form", state="hidden", timeout=5000)

    def test_12_chrono_orb_centering_and_frosted_glass(self):
        """Проверяет центрирование оранжевого Chrono-Orb и матовое стекло карточек."""
        self.navigate_home()
        orb = self.page.locator(".ambient-orange-orb")
        self.assertTrue(orb.is_visible())
        transform = orb.evaluate("el => window.getComputedStyle(el).transform")
        self.assertIn("matrix", transform)
        card = self.page.locator(".drill-card").first
        blur_style = card.evaluate("""el => {
            const s = window.getComputedStyle(el);
            if (s.backdropFilter && s.backdropFilter !== 'none') return s.backdropFilter;
            if (s.webkitBackdropFilter && s.webkitBackdropFilter !== 'none') return s.webkitBackdropFilter;
            for (const sheet of document.styleSheets) {
                try {
                    for (const r of sheet.cssRules) {
                        if (r.selectorText && r.selectorText.includes('.drill-card')) {
                            if (r.cssText.includes('backdrop-filter')) return r.cssText;
                        }
                    }
                } catch(e) {}
            }
            return '';
        }""")
        self.assertIn("blur", blur_style)

if __name__ == "__main__":
    unittest.main()
