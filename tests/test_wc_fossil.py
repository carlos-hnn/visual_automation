from __future__ import annotations

import unittest

import numpy as np

from core.screen import Frame
from core.vision import TemplateMatch
from v2.flows.wc_fossil import configured_point, exclude_red_filled_targets, red_fill_fraction


class WcFossilTests(unittest.TestCase):
    def test_unfilled_point_defaults_to_zero(self) -> None:
        self.assertEqual(configured_point({}, "outbound", None), (0, 0))

    def test_absolute_point_is_preserved(self) -> None:
        config = {"travel_points": {"outbound": {"x": 123, "y": 456}}}
        self.assertEqual(configured_point(config, "outbound", {"left": 10, "top": 20}), (123, 456))

    def test_window_relative_point_is_resolved(self) -> None:
        config = {
            "travel_points_are_window_relative": True,
            "travel_points": {"return": {"x": 100, "y": 200}},
        }
        self.assertEqual(configured_point(config, "return", {"left": 900, "top": 40}), (1000, 240))

    def test_red_filled_cyan_target_is_rejected(self) -> None:
        image = np.zeros((30, 60, 3), dtype=np.uint8)
        image[5:25, 5:25] = (0, 0, 255)
        frame = Frame(image=image, left=100, top=200, width=60, height=30)
        red = TemplateMatch(x=105, y=205, width=20, height=20, score=100)
        clean = TemplateMatch(x=135, y=205, width=20, height=20, score=100)
        pixels, fraction = red_fill_fraction(frame, red, {})
        self.assertEqual(pixels, 400)
        self.assertEqual(fraction, 1.0)
        self.assertEqual(exclude_red_filled_targets(frame, [red, clean], {}), [clean])


if __name__ == "__main__":
    unittest.main()
