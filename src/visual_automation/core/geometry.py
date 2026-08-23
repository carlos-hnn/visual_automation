from __future__ import annotations

from typing import Any


def relative_region(window: dict[str, int], raw: dict[str, Any]) -> dict[str, int]:
    """Resolve and clamp one window-relative capture region."""
    left, top = int(raw["left"]), int(raw["top"])
    return {
        "left": window["left"] + left,
        "top": window["top"] + top,
        "width": min(int(raw["width"]), max(1, window["width"] - left)),
        "height": min(int(raw["height"]), max(1, window["height"] - top)),
    }


def region_center(region: dict[str, int]) -> tuple[int, int]:
    return (
        int(region["left"]) + int(region["width"]) // 2,
        int(region["top"]) + int(region["height"]) // 2,
    )


def point_in_region(point: tuple[int, int], region: dict[str, int]) -> bool:
    x, y = point
    return (
        region["left"] <= x < region["left"] + region["width"]
        and region["top"] <= y < region["top"] + region["height"]
    )
