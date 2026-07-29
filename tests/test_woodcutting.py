from __future__ import annotations

import unittest

from visual_automation.core.vision import TemplateMatch
from visual_automation.flows.woodcutting import nearest_to_center, region_center


class WoodcuttingTests(unittest.TestCase):
    def test_region_center_uses_absolute_region_coordinates(self) -> None:
        self.assertEqual(region_center({"left": 100, "top": 50, "width": 800, "height": 600}), (500, 350))

    def test_nearest_marker_is_selected_from_character_anchor(self) -> None:
        far = TemplateMatch(x=100, y=100, width=20, height=20, score=500)
        near = TemplateMatch(x=480, y=330, width=20, height=20, score=200)
        self.assertIs(nearest_to_center([far, near], (500, 350)), near)

    def test_nearest_marker_returns_none_for_empty_list(self) -> None:
        self.assertIsNone(nearest_to_center([], (500, 350)))


if __name__ == "__main__":
    unittest.main()
