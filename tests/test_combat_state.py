from __future__ import annotations

import unittest

import cv2
import numpy as np

from visual_automation.core.screen import Frame
from visual_automation.flows.combat_mode import (
    RedTarget,
    best_green_marked_inventory_item,
    detect_red_targets,
    parse_task_counter_text,
    reacquire_target,
)
from visual_automation.game_states.combat import detect_combat_activity, prayer_percent


class PrayerPercentTests(unittest.TestCase):
    def test_adjacent_green_potions_fall_back_to_ungrouped_components(self) -> None:
        image = np.zeros((90, 50, 3), dtype=np.uint8)
        image[5:39, 10:34] = (0, 255, 0)
        image[41:75, 10:34] = (0, 255, 0)
        frame = Frame(image=image, left=100, top=200, width=50, height=90)

        class FakeScreen:
            def capture(self, _region):
                return frame

        candidate = best_green_marked_inventory_item(
            FakeScreen(),
            {"left": 100, "top": 200, "width": 50, "height": 90},
            (35, 110, 70),
            (90, 255, 255),
            min_green_pixels=120,
            min_dimension=8,
            max_dimension=70,
            grouping_pixels=1,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.match.center, (122, 222))

    def test_reads_number_immediately_before_controlled(self) -> None:
        reading = parse_task_counter_text("L3 | 34 | 14 | 66 | Controlled")

        self.assertTrue(reading.controlled_visible)
        self.assertEqual(reading.remaining, 66)

    def test_reports_when_controlled_counter_disappears(self) -> None:
        reading = parse_task_counter_text("")

        self.assertFalse(reading.controlled_visible)
        self.assertIsNone(reading.remaining)

    def test_small_green_remainder_sustains_combat(self) -> None:
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        image[8:12, 10:15] = (0, 255, 0)
        image[8:12, 15:90] = (0, 0, 255)
        frame = Frame(image=image, left=0, top=0, width=100, height=20)

        status = detect_combat_activity(
            frame, threshold=0.02, sustain=True, sustain_min_green_pixels=3,
        )

        self.assertTrue(status.in_combat)
        self.assertLess(status.green_fraction, status.threshold)

    def test_residual_red_bar_does_not_sustain_combat(self) -> None:
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        image[8:12, 10:90] = (0, 0, 255)
        frame = Frame(image=image, left=0, top=0, width=100, height=20)

        status = detect_combat_activity(
            frame, threshold=0.02, sustain=True, sustain_min_green_pixels=3,
        )

        self.assertFalse(status.in_combat)
        self.assertEqual(status.green_pixels, 0)

    def test_empty_combat_region_is_not_combat(self) -> None:
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        frame = Frame(image=image, left=0, top=0, width=100, height=20)

        self.assertFalse(detect_combat_activity(frame, threshold=0.02).in_combat)

    def test_cyan_digits_do_not_count_as_full_bar(self) -> None:
        image = np.zeros((100, 20, 3), dtype=np.uint8)
        cyan = cv2.cvtColor(np.uint8([[[90, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0]
        image[3:7, 2:7] = cyan
        image[62:100, :] = cyan
        frame = Frame(image=image, left=0, top=0, width=20, height=100)
        self.assertEqual(prayer_percent(frame), 38.0)

    def test_red_component_inside_character_exclusion_radius_is_ignored(self) -> None:
        image = np.zeros((160, 220, 3), dtype=np.uint8)
        image[65:95, 95:125] = (0, 0, 255)
        image[65:95, 175:205] = (0, 0, 255)
        frame = Frame(image=image, left=0, top=0, width=220, height=160)
        targets, _mask = detect_red_targets(
            frame,
            {"left": 0, "top": 0, "width": 220, "height": 160},
            [],
            (110, 80),
            min_red_pixels=20,
            min_dimension=10,
            max_dimension=80,
            grouping_pixels=0,
            anchor_exclusion_radius=70.0,
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].center, (190, 80))

    def test_reacquires_nearest_target_after_it_moves(self) -> None:
        previous = RedTarget(100, 100, 30, 30, 200, 50.0)
        moved = RedTarget(125, 108, 30, 30, 200, 55.0)
        other = RedTarget(250, 200, 30, 30, 200, 180.0)

        self.assertIs(reacquire_target(previous, [other, moved], 50.0), moved)

    def test_does_not_click_when_target_cannot_be_safely_reacquired(self) -> None:
        previous = RedTarget(100, 100, 30, 30, 200, 50.0)
        far_away = RedTarget(250, 200, 30, 30, 200, 180.0)

        self.assertIsNone(reacquire_target(previous, [far_away], 50.0))


if __name__ == "__main__":
    unittest.main()
