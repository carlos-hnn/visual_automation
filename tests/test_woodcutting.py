from __future__ import annotations

import unittest

from visual_automation.actions.markers import select_marker
from visual_automation.core.geometry import region_center
from visual_automation.core.vision import TemplateMatch


class WoodcuttingTests(unittest.TestCase):
    def test_region_center_uses_absolute_region_coordinates(self) -> None:
        self.assertEqual(region_center({"left": 100, "top": 50, "width": 800, "height": 600}), (500, 350))

    def test_nearest_marker_is_selected_from_character_anchor(self) -> None:
        far = TemplateMatch(x=100, y=100, width=20, height=20, score=500)
        near = TemplateMatch(x=480, y=330, width=20, height=20, score=200)
        region = {"left": 100, "top": 50, "width": 800, "height": 600}
        self.assertIs(select_marker([far, near], region, "nearest"), near)

    def test_nearest_marker_returns_none_for_empty_list(self) -> None:
        region = {"left": 100, "top": 50, "width": 800, "height": 600}
        self.assertIsNone(select_marker([], region, "nearest"))


if __name__ == "__main__":
    unittest.main()
