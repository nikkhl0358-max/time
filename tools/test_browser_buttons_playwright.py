"""
Сквозной тест кнопок и пользовательских сценариев в браузере через Playwright.

Тестирует реальные клики мыши, переходы и состояние DOM:
  1. Переключение ролей (Преподаватели, Группы, Аудитории)
  2. Поиск преподавателя и очистка поля крестиком
  3. Выбор преподавателя и открытие расписания
  4. Фильтры дней недели (Пн, Вт, Ср, Чт, Пт, Сб, Вся неделя)
  5. Переключение учебных недель (◀ / ▶)
  6. Кнопка возврата к выбору («Сменить преподавателя»)
  7. Пошаговый выбор группы (Drill-Down: Направление -> Год -> Группа)
  8. Выбор аудитории
  9. Модальное окно звонков (открытие, [✕], клик по фону, Escape)
  10. Модальное окно оперативных замен
  11. Перекрёстные ссылки между парами (преподаватель -> группа -> преподаватель)
  12. Мобильный адаптивный режим (375x667)
"""
import os
import sys
import unittest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL") or "http://localhost:5000"


def _launch_browser(playwright_instance):
    """
    Запускает системный Chromium в headless-режиме для тестов.
    Используем системный бинарник /usr/bin/chromium из-за sandbox окружения.
    """
    return playwright_instance.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )


def _create_monitored_page(browser, viewport=None):
    """
    Создаёт изолированную страницу с отслеживанием консольных ошибок.
    Перехватывает необработанные исключения и ошибки консоли браузера.
    """
    errors = []
    vp = viewport or {"width": 1280, "height": 800}
    page = browser.new_page(viewport=vp)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda err: errors.append(str(err)))
    return page, errors


class TestBrowserButtonsPlaywright(unittest.TestCase):
    """Набор тестов взаимодействия с интерактивными элементами интерфейса."""

    @classmethod
    def setUpClass(cls):
        """Инициализирует один инстанс браузера для всех тестов класса."""
        cls.playwright = sync_playwright().start()
        cls.browser = _launch_browser(cls.playwright)
        cls.base_url = BASE_URL

    @classmethod
    def tearDownClass(cls):
        """Освобождает ресурсы браузера и завершает процесс Playwright."""
        cls.browser.close()
        cls.playwright.stop()

    def test_01_switch_role_teachers(self):
        """
        Проверяет переключение роли на 'Преподаватели' и показ ЦК с алфавитом.
        Кликает по кнопке 'Преподаватели' и ожидает появления каталога.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.wait_for_selector(".role-btn")
            page.click('.role-btn:has-text("Преподаватели")')
            page.wait_for_selector(".dept-tabs-scroll")
            self.assertGreater(page.locator(".dept-tab-btn").count(), 0)
            self.assertGreater(page.locator(".letter-btn").count(), 0)
            self.assertGreater(page.locator(".teacher-card").count(), 0)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_02_teacher_search_and_clear(self):
        """
        Проверяет работу поисковой строки преподавателей и кнопки очистки '✕'.
        Вводит текст 'Колесникова', проверяет фильтрацию, кликает '✕' и проверяет очистку.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.click('.role-btn:has-text("Преподаватели")')
            page.wait_for_selector(".teacher-search-input")
            page.fill(".teacher-search-input", "Колесникова")
            page.wait_for_selector(".clear-search-btn")
            self.assertEqual(page.locator(".teacher-card").count(), 1)
            page.click(".clear-search-btn")
            self.assertEqual(page.locator(".teacher-search-input").input_value(), "")
            self.assertGreater(page.locator(".teacher-card").count(), 1)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_03_select_teacher_navigates_to_schedule(self):
        """
        Проверяет клик по карточке преподавателя и переход к расписанию.
        Выбирает Колесникову К.М., проверяет URL и заголовок карточки.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.click('.role-btn:has-text("Преподаватели")')
            page.fill(".teacher-search-input", "Колесникова")
            page.click('.teacher-card:has-text("Колесникова")')
            page.wait_for_selector(".entity-name")
            self.assertIn("teacher/15bhvzk6", page.url)
            self.assertIn("Колесникова", page.locator(".entity-name").inner_text())
            self.assertEqual(errors, [])
        finally:
            page.close()

    def _verify_day_pill(self, page, pill_btn, expected_cols):
        """
        Проверяет клик по кнопке дня недели и количество отображаемых колонок.
        Убеждается, что активный класс переходит на выбранную таблетку.
        """
        pill_btn.click()
        self.assertIn("active", pill_btn.get_attribute("class") or "")
        self.assertEqual(page.locator(".day-column").count(), expected_cols)

    def test_04_day_pills_filtering(self):
        """
        Проверяет фильтрацию сетки расписания кнопками дней недели.
        Кликает по дням Пн-Сб (1 колонка) и 'Вся неделя' (6 колонок).
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(f"{self.base_url}/teacher/15bhvzk6?week=10")
            page.wait_for_selector(".day-filter-bar")
            pills = page.locator(".day-pill-btn")
            self._verify_day_pill(page, pills.nth(1), 1)
            self._verify_day_pill(page, pills.nth(2), 1)
            self._verify_day_pill(page, pills.nth(0), 6)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_05_week_navigation_buttons(self):
        """
        Проверяет кнопки переключения учебной недели ◀ и ▶.
        Увеличивает неделю на 1 (9 -> 10), затем возвращает (10 -> 9).
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(f"{self.base_url}/teacher/15bhvzk6?week=9")
            page.wait_for_selector(".week-pill .week-number")
            init_week = int(page.locator(".week-pill .week-number").inner_text())
            page.click('.week-nav-btn[title="Следующая неделя"]')
            self.assertEqual(int(page.locator(".week-pill .week-number").inner_text()), init_week + 1)
            page.click('.week-nav-btn[title="Предыдущая неделя"]')
            self.assertEqual(int(page.locator(".week-pill .week-number").inner_text()), init_week)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_06_change_entity_button_returns_to_picker(self):
        """
        Проверяет возврат к каталогу выбора по кнопке 'Сменить преподавателя'.
        Кликает по кнопке смены и проверяет появление поисковой строки.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(f"{self.base_url}/teacher/15bhvzk6?week=10")
            page.wait_for_selector(".change-entity-btn")
            page.click(".change-entity-btn")
            page.wait_for_selector(".teacher-search-input")
            self.assertTrue(page.locator(".teacher-search-input").is_visible())
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_07_switch_role_groups_drill_down(self):
        """
        Проверяет пошаговый выбор группы через 3 уровня карточек.
        Шаг 1: Специальность -> Шаг 2: Год поступления -> Шаг 3: Группа.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.click('.role-btn:has-text("Группы")')
            page.wait_for_selector(".specialty-card")
            page.locator(".specialty-card").first.click()
            page.wait_for_selector(".year-card")
            page.locator(".year-card").first.click()
            page.wait_for_selector(".group-card")
            grp_name = page.locator(".group-card .group-name").first.inner_text().strip()
            page.locator(".group-card").first.click()
            page.wait_for_selector(".entity-name")
            self.assertEqual(page.locator(".entity-name").inner_text().strip(), grp_name)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_08_switch_role_rooms_select_auditorium(self):
        """
        Проверяет выбор аудитории из каталога корпусов и показ расписания кабинета.
        Кликает 'Аудитории', выбирает чип кабинета и проверяет загрузку сетки.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.click('.role-btn:has-text("Аудитории")')
            page.wait_for_selector(".room-pick-btn")
            room_btn = page.locator(".room-pick-btn").first
            room_name = room_btn.locator(".room-pick-name").inner_text().strip() if room_btn.locator(".room-pick-name").count() else room_btn.inner_text().split('\n')[0].strip()
            room_btn.click()
            page.wait_for_selector(".entity-name")
            self.assertIn("room/", page.url)
            self.assertEqual(page.locator(".entity-name").inner_text().strip(), room_name)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def _verify_modal_close_action(self, page, open_selector, action_fn):
        """
        Открывает модальное окно, совершает действие закрытия и проверяет скрытие.
        Используется для проверки [✕], клика по оверлею и клавиши Escape.
        """
        page.click(open_selector)
        page.wait_for_selector(".modal-window")
        action_fn()
        page.wait_for_selector(".modal-backdrop", state="detached")

    def test_09_bells_modal_interactions(self):
        """
        Проверяет сценарии закрытия модального окна звонков.
        Тестирует закрытие через кнопку [✕], клик по оверлею и клавишу Escape.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            btn = '.nav-action-btn:has-text("Звонки")'
            self._verify_modal_close_action(page, btn, lambda: page.click(".modal-close-btn"))
            self._verify_modal_close_action(page, btn, lambda: page.click(".modal-backdrop", position={"x": 5, "y": 5}))
            self._verify_modal_close_action(page, btn, lambda: page.keyboard.press("Escape"))
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_10_substitutions_modal_interactions(self):
        """
        Проверяет открытие и закрытие модального окна оперативных замен.
        Кликает по кнопке 'Замены' и закрывает модальное окно кнопкой [✕].
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(self.base_url)
            page.click('.nav-action-btn:has-text("Замены")')
            page.wait_for_selector(".substitutions-feed")
            self.assertTrue(page.locator(".substitutions-feed").is_visible())
            page.click(".modal-close-btn")
            page.wait_for_selector(".modal-backdrop", state="detached")
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_11_cross_navigation_cards(self):
        """
        Проверяет перекрёстную навигацию между преподавателем и группой из карточки.
        В расписании преподавателя жмёт чип группы, а в группе — имя преподавателя.
        """
        page, errors = _create_monitored_page(self.browser)
        try:
            page.goto(f"{self.base_url}/teacher/15bhvzk6?week=10")
            page.wait_for_selector(".group-tag-btn")
            page.locator(".group-tag-btn").first.click()
            page.wait_for_selector(".teacher-info.teacher-btn")
            self.assertIn("group", page.url)
            page.locator(".teacher-info.teacher-btn").first.click()
            page.wait_for_selector(".entity-name")
            self.assertIn("teacher", page.url)
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_12_mobile_viewport_buttons(self):
        """
        Проверяет кликабельность и отсутствие перекрытий кнопок на мобильном экране (375x667).
        Проверяет кнопки ролей, звонков и замен на iPhone SE viewport.
        """
        page, errors = _create_monitored_page(self.browser, {"width": 375, "height": 667})
        try:
            page.goto(self.base_url)
            page.wait_for_selector(".app-navbar")
            for i in range(page.locator(".role-btn").count()):
                btn = page.locator(".role-btn").nth(i)
                self.assertIsNotNone(btn.bounding_box())
                btn.click()
            page.click('.nav-action-btn:has-text("Звонки")')
            page.wait_for_selector(".bells-container")
            page.click(".modal-close-btn")
            self.assertEqual(errors, [])
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
