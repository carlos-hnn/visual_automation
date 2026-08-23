from __future__ import annotations

from typing import Any, Literal

FieldSection = Literal["operation", "advanced"]

ADVANCED_EXACT = {
    "mouse_backend",
    "platform",
    "window_title",
    "monitor",
    "regions_are_window_relative",
    "travel_points_are_window_relative",
    "template_dirs_by_platform",
    "template_paths",
    "templates_dir",
    "regions",
    "thresholds",
    "click_offsets",
}

ADVANCED_PARTS = (
    "_hsv_",
    "_threshold",
    "template_",
    "_pixels",
    "_dimension",
    "_grouping",
    "_fill_fraction",
    "_scale",
    "_spacing",
    "_patch_radius",
    "_occupied_std",
    "_first_x",
    "_first_y",
    "_poll_seconds",
    "_timeout",
    "_jitter",
    "move_duration_",
)


def field_section(key: str, _value: Any) -> FieldSection:
    if key in ADVANCED_EXACT or any(part in key for part in ADVANCED_PARTS):
        return "advanced"
    return "operation"


def field_label(key: str) -> str:
    return key.replace("_", " ").strip().title()


def panel_metadata(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        key: {"section": field_section(key, value), "label": field_label(key)}
        for key, value in config.items()
    }
