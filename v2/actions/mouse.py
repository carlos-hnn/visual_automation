from __future__ import annotations

from core.mouse import MouseConfig, MouseController


def build_mouse(
    move_duration_min: float,
    move_duration_max: float,
    click_pause_seconds: float = 0.05,
    spot_jitter_pixels: int = 4,
) -> MouseController:
    return MouseController(
        MouseConfig(
            move_duration_min=move_duration_min,
            move_duration_max=max(move_duration_min, move_duration_max),
            click_pause_seconds=click_pause_seconds,
            random_offset_pixels=max(0, spot_jitter_pixels),
            point_tolerance_pixels=max(0, spot_jitter_pixels),
        )
    )

