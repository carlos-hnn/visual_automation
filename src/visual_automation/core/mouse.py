from __future__ import annotations

import random
import time
from dataclasses import dataclass
from math import hypot

import pyautogui

from visual_automation.core.regions import Region
from visual_automation.core.vision import TemplateMatch, Vision


@dataclass(frozen=True)
class MouseConfig:
    move_duration_min: float = 0.25
    move_duration_max: float = 0.55
    click_pause_seconds: float = 0.12
    random_offset_pixels: int = 4
    point_tolerance_pixels: int = 6
    minimum_humanized_duration: float = 0.16
    curve_strength_min: float = 0.12
    curve_strength_max: float = 0.32
    movement_step_seconds: float = 0.012


class MouseController:
    def __init__(self, config: MouseConfig | None = None) -> None:
        self.config = config or MouseConfig()
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.0

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.move_to(x, y)
        pyautogui.click(button=button)
        time.sleep(self.config.click_pause_seconds)

    def click_point(self, x: int, y: int, tolerance_pixels: int | None = None, button: str = "left") -> tuple[int, int]:
        target_x, target_y = self.point_near(x, y, tolerance_pixels=tolerance_pixels)
        self.click(target_x, target_y, button=button)
        return target_x, target_y

    def move_to(self, x: int, y: int) -> None:
        start_x, start_y = pyautogui.position()
        target_x = int(x)
        target_y = int(y)
        distance = hypot(target_x - start_x, target_y - start_y)
        if distance < 1:
            return

        duration = random.uniform(self.config.move_duration_min, self.config.move_duration_max)
        duration = max(duration, self.config.minimum_humanized_duration)
        step_seconds = max(0.004, self.config.movement_step_seconds)
        steps = max(8, int(duration / step_seconds), min(60, int(distance / 8)))

        control_1, control_2 = self._curve_controls(start_x, start_y, target_x, target_y, distance)
        previous_x, previous_y = start_x, start_y
        started_at = time.monotonic()

        for step in range(1, steps + 1):
            progress = step / steps
            eased = self._ease_in_out(progress)
            next_x, next_y = self._cubic_bezier(
                (start_x, start_y),
                control_1,
                control_2,
                (target_x, target_y),
                eased,
            )
            next_x = round(next_x)
            next_y = round(next_y)
            if next_x != previous_x or next_y != previous_y or step == steps:
                pyautogui.moveTo(next_x, next_y, duration=0, _pause=False)
                previous_x, previous_y = next_x, next_y

            target_elapsed = duration * progress
            remaining = started_at + target_elapsed - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    def point_near(self, x: int, y: int, tolerance_pixels: int | None = None) -> tuple[int, int]:
        radius = self.config.point_tolerance_pixels if tolerance_pixels is None else tolerance_pixels
        radius = max(0, int(radius))
        return (
            x + random.randint(-radius, radius),
            y + random.randint(-radius, radius),
        )

    def click_match(self, match: TemplateMatch, button: str = "left") -> tuple[int, int]:
        x, y = self._point_inside_match(match)
        self.click(x, y, button=button)
        return x, y

    def click_template(
        self,
        vision: Vision,
        template_path: str,
        region: Region | dict[str, int] | None = None,
        threshold: float = 0.85,
        button: str = "left",
    ) -> bool:
        match = vision.find_template(template_path, region=region, threshold=threshold)
        if match is None:
            return False
        self.click_match(match, button=button)
        return True

    def _point_inside_match(self, match: TemplateMatch) -> tuple[int, int]:
        center_x, center_y = match.center
        max_x_offset = max(0, min(self.config.random_offset_pixels, (match.width - 1) // 2))
        max_y_offset = max(0, min(self.config.random_offset_pixels, (match.height - 1) // 2))
        return (
            center_x + random.randint(-max_x_offset, max_x_offset),
            center_y + random.randint(-max_y_offset, max_y_offset),
        )

    def _curve_controls(
        self,
        start_x: int,
        start_y: int,
        target_x: int,
        target_y: int,
        distance: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        dx = target_x - start_x
        dy = target_y - start_y
        normal_x = -dy / distance
        normal_y = dx / distance
        bend = random.choice((-1, 1)) * distance * random.uniform(
            self.config.curve_strength_min,
            self.config.curve_strength_max,
        )
        bend *= random.uniform(0.45, 1.0)
        control_1 = (
            start_x + dx * random.uniform(0.25, 0.40) + normal_x * bend,
            start_y + dy * random.uniform(0.25, 0.40) + normal_y * bend,
        )
        control_2 = (
            start_x + dx * random.uniform(0.60, 0.80) - normal_x * bend * random.uniform(0.25, 0.75),
            start_y + dy * random.uniform(0.60, 0.80) - normal_y * bend * random.uniform(0.25, 0.75),
        )
        return control_1, control_2

    def _cubic_bezier(
        self,
        start: tuple[float, float],
        control_1: tuple[float, float],
        control_2: tuple[float, float],
        end: tuple[float, float],
        progress: float,
    ) -> tuple[float, float]:
        inverse = 1.0 - progress
        x = (
            inverse**3 * start[0]
            + 3 * inverse**2 * progress * control_1[0]
            + 3 * inverse * progress**2 * control_2[0]
            + progress**3 * end[0]
        )
        y = (
            inverse**3 * start[1]
            + 3 * inverse**2 * progress * control_1[1]
            + 3 * inverse * progress**2 * control_2[1]
            + progress**3 * end[1]
        )
        return x, y

    def _ease_in_out(self, progress: float) -> float:
        return progress * progress * (3.0 - 2.0 * progress)


class QuartzMouseController(MouseController):
    """Experimental macOS click backend that does not update the visible cursor."""

    def __init__(self, config: MouseConfig | None = None) -> None:
        self.config = config or MouseConfig()
        try:
            import Quartz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Quartz mouse backend requires PyObjC Quartz on macOS") from exc
        self._quartz = Quartz

    def click(self, x: int, y: int, button: str = "left") -> None:
        if button != "left":
            raise ValueError("Experimental Quartz backend currently supports only left clicks")
        point = (float(x), float(y))
        down_error = self._quartz.CGPostMouseEvent(point, False, 1, True)
        time.sleep(0.035)
        up_error = self._quartz.CGPostMouseEvent(point, False, 1, False)
        success = getattr(self._quartz, "kCGErrorSuccess", 0)
        if down_error != success or up_error != success:
            raise RuntimeError(f"Quartz click failed: down={down_error}, up={up_error}")
        time.sleep(self.config.click_pause_seconds)

    def move_to(self, x: int, y: int) -> None:
        # Background Quartz clicks intentionally leave the user's pointer alone.
        return
