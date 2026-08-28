from __future__ import annotations

import unittest

import numpy as np

from visual_automation.core.screen import Frame
from visual_automation.core.vision import TemplateMatch
from visual_automation.flows.wc_fossil import (
    click_inventory_items,
    configured_point,
    exclude_red_filled_targets,
    red_fill_fraction,
)


class WcFossilTests(unittest.TestCase):
    def test_inventory_cleanup_clicks_every_occupied_position(self) -> None:
        class Mouse:
            def __init__(self):
                self.clicks = []

            def click_point(self, x, y, tolerance_pixels):
                self.tolerance_pixels = tolerance_pixels
                self.clicks.append((x, y))

        inventory = type("Inventory", (), {"occupied_centers": ((10, 20), (30, 40), (50, 60))})()
        args = type("Args", (), {
            "dry_run": False,
            "inventory_click_ticks": 0.0,
            "tick_seconds": 0.6,
            "inventory_time_jitter": 0.0,
            "inventory_spot_jitter": 3,
        })()
        stop_keys = type("StopKeys", (), {"stop_requested": False})()
        mouse = Mouse()

        clicked = click_inventory_items(mouse, inventory, args, stop_keys)

        self.assertEqual(clicked, 3)
        self.assertEqual(mouse.clicks, [(10, 20), (30, 40), (50, 60)])
        self.assertEqual(mouse.tolerance_pixels, 3)

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
