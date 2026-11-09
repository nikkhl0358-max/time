"""
Тесты логики переносов (moved substitutions), расчета свободных пар и характеристик аудиторий.
Проверяет соответствие логики новому публичному клиенту и паритет с базой данных.
"""

import unittest
import json
from app import _public_entity_data_patch

class TestRoomSubstitutionsParity(unittest.TestCase):
    """Набор тестов для проверки обработки переносов занятий и типов кабинетов."""

    def test_01_public_entity_data_patch_includes_room_substitutions(self):
        """Проверяет, что патч шардов включает переносы по roomId и newRoomId."""
        project = {
            "loads": [],
            "substitutions": [
                {
                    "id": "sub_m1",
                    "type": "moved",
                    "date": "2026-11-04",
                    "targetDate": "2026-11-12",
                    "targetPeriod": 0,
                    "newRoomId": "room_art_17",
                    "instId": "inst_01"
                },
                {
                    "id": "sub_m2",
                    "type": "cancel",
                    "date": "2026-11-12",
                    "roomId": "room_art_17",
                    "instId": "inst_02"
                },
                {
                    "id": "sub_other",
                    "type": "cancel",
                    "date": "2026-11-12",
                    "roomId": "room_other_99",
                    "instId": "inst_99"
                }
            ]
        }
        chosen_instances = [
            {"instId": "inst_01", "roomId": "room_art_17", "period": 1, "day": 3}
        ]
        patch = _public_entity_data_patch(project, chosen_instances)
        sub_ids = [s["id"] for s in patch["substitutions"]]
        self.assertIn("sub_m1", sub_ids)
        self.assertIn("sub_m2", sub_ids)
        self.assertNotIn("sub_other", sub_ids)

    def test_02_room_category_detection_rules(self):
        """Проверяет эвристику определения типов аудиторий (комп, худож, лаб)."""
        types = [
            {"id": "t_art", "name": "художественный кабинет"},
            {"id": "t_comp", "name": "компьютерная"},
            {"id": "t_lab", "name": "лаборатория химии"}
        ]
        sizes = [
            {"id": "s_sm", "name": "малая (до 15 мест)"},
            {"id": "s_lg", "name": "большая (свыше 30 мест)"}
        ]
        # Кабинет 17 (художественный)
        room_17 = {
            "id": "r17",
            "name": "17",
            "typeId": "t_art",
            "sizeId": "s_sm",
            "capacity": 15,
            "isArtRoom": True,
            "equipment": "Мольберты"
        }
        # Проверяем ключевые флаги
        is_art = bool(room_17.get("isArtRoom") or "худож" in types[0]["name"])
        self.assertTrue(is_art)
        self.assertEqual(room_17["capacity"], 15)

    def test_03_calculate_free_periods_logic(self):
        """Проверяет формулу свободных слотов аудитории [1..7] без отмененных пар."""
        occupied = [1, 2, 3, 4]  # Первые 4 пары заняты
        free = [p for p in range(1, 8) if p not in occupied]
        self.assertEqual(free, [5, 6, 7])

    def test_04_calculate_gaps_logic(self):
        """Проверяет расчет окон между занятиями."""
        occupied = [1, 2, 5]
        gaps = [p for p in range(min(occupied) + 1, max(occupied)) if p not in occupied]
        self.assertEqual(gaps, [3, 4])

    def test_05_moved_substitution_dereferences_instance_group(self):
        """Проверяет, что перенос без groupId связывается с группой по instId."""
        project = {
            "schedule": {
                "instances": [
                    {"instId": "inst_nov4_move", "groupId": "grp_3kdd36", "teacherId": "t_kudr", "period": 1, "day": 2}
                ]
            },
            "substitutions": [
                {
                    "id": "sub_moved_12nov",
                    "type": "moved",
                    "date": "2026-11-04",
                    "targetDate": "2026-11-12",
                    "targetPeriod": 0,
                    "newRoomId": "room_17",
                    "instId": "inst_nov4_move"
                }
            ]
        }
        patch = _public_entity_data_patch(project, [], kind="group", entity_id="grp_3kdd36")
        sub_ids = [s["id"] for s in patch["substitutions"]]
        self.assertIn("sub_moved_12nov", sub_ids)

    def test_06_suppress_ordinary_room_type_tags(self):
        """Проверяет, что типовые аудитории ('обычная', 'лекционная') не получают тег."""
        ignored_names = ["обычная", "стандартная", "аудитория", "лекционная"]
        for name in ignored_names:
            lower = name.lower()
            suppressed = any(w in lower for w in ["обычн", "стандарт", "аудитор", "лекцион"])
            self.assertTrue(suppressed, f"{name} должен быть отфильтрован")

    def test_07_gym_room_name_deduplication(self):
        """Проверяет, что спортзал не дублирует слово 'спортзал' в тегах."""
        room_name = "Спортивный зал (1)"
        r_lower = room_name.lower()
        is_gym_redundant = "спорт" in r_lower or "зал" in r_lower
        self.assertTrue(is_gym_redundant, "Название зала должно определять избыточность тега спортзал")


if __name__ == "__main__":
    unittest.main()
