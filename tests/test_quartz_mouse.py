from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from visual_automation.actions.mouse import build_mouse
from visual_automation.core.mouse import MouseConfig, QuartzMouseController


class FakeQuartz:
    kCGErrorSuccess = 0

    def __init__(self) -> None:
        self.events: list[tuple[tuple[float, float], bool, int, bool]] = []

    def CGPostMouseEvent(
        self,
        point: tuple[float, float],
        update_cursor: bool,
        button_count: int,
        button_down: bool,
    ) -> int:
        self.events.append((point, update_cursor, button_count, button_down))
        return self.kCGErrorSuccess


class QuartzMouseControllerTests(unittest.TestCase):
    def test_click_posts_down_and_up_without_updating_cursor(self) -> None:
        controller = QuartzMouseController(MouseConfig(click_pause_seconds=0.0))
        fake = FakeQuartz()
        controller._quartz = fake

        controller.click(120, 340)

        self.assertEqual(
            fake.events,
            [
                ((120.0, 340.0), False, 1, True),
                ((120.0, 340.0), False, 1, False),
            ],
        )

    def test_move_to_is_intentionally_a_no_op(self) -> None:
        controller = QuartzMouseController(MouseConfig())
        controller.move_to(500, 600)

    def test_factory_selects_quartz_from_environment(self) -> None:
        with patch.dict(os.environ, {"VISUAL_AUTOMATION_MOUSE_BACKEND": "quartz"}):
            controller = build_mouse(0.1, 0.2)
        self.assertIsInstance(controller, QuartzMouseController)

    def test_factory_rejects_unknown_backend(self) -> None:
        with patch.dict(os.environ, {"VISUAL_AUTOMATION_MOUSE_BACKEND": "unknown"}):
            with self.assertRaisesRegex(ValueError, "expected standard or quartz"):
                build_mouse(0.1, 0.2)


if __name__ == "__main__":
    unittest.main()
