from __future__ import annotations

import unittest

import cv2
import numpy as np

from visual_automation.core.screen import Frame
from visual_automation.flows.combat_mode import detect_red_targets
from visual_automation.game_states.combat import prayer_percent


class PrayerPercentTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
