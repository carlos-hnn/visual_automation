from __future__ import annotations

import os
import random

from core.mouse import MouseConfig, MouseController, QuartzMouseController
from core.vision import TemplateMatch


def build_mouse(
    move_duration_min: float,
    move_duration_max: float,
    click_pause_seconds: float = 0.05,
    spot_jitter_pixels: int = 4,
) -> MouseController:
    config = MouseConfig(
        move_duration_min=move_duration_min,
        move_duration_max=max(move_duration_min, move_duration_max),
        click_pause_seconds=click_pause_seconds,
        random_offset_pixels=max(0, spot_jitter_pixels),
        point_tolerance_pixels=max(0, spot_jitter_pixels),
    )
    backend = os.environ.get("VISUAL_AUTOMATION_MOUSE_BACKEND", "standard").strip().lower()
    if backend == "standard":
        return MouseController(config)
    if backend == "quartz":
        return QuartzMouseController(config)
    raise ValueError(f"Unknown mouse backend: {backend}; expected standard or quartz")


def match_click_coordinates(match: TemplateMatch, click_scale: float, spot_jitter_pixels: int) -> tuple[int, int]:
    center_x, center_y = match.center
    jitter = max(0, int(spot_jitter_pixels))
    max_x_offset = min(jitter, max(0, (match.width - 1) // 2))
    max_y_offset = min(jitter, max(0, (match.height - 1) // 2))
    center_x += random.randint(-max_x_offset, max_x_offset) if max_x_offset else 0
    center_y += random.randint(-max_y_offset, max_y_offset) if max_y_offset else 0
    scale = max(0.01, click_scale)
    return round(center_x / scale), round(center_y / scale)
