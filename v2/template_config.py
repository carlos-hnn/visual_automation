from __future__ import annotations

from pathlib import Path
from typing import Any

from v2.config import value_from_config
from v2.game_states.template_matching import parse_scales
from v2.game_states.template_state import TemplateState
from v2.platforming import detect_platform


def template_path(templates_dir: Path, name: str) -> Path:
    return templates_dir / f"{name}.png"


def threshold_for(config: dict, name: str, default: float) -> float:
    thresholds = value_from_config(config, "thresholds", {})
    if isinstance(thresholds, dict) and name in thresholds:
        return float(thresholds[name])
    if name.startswith("tree") and isinstance(thresholds, dict) and "tree" in thresholds:
        return float(thresholds["tree"])
    return default


def scales_for(config: dict[str, Any], name: str, default_scales: list[float]) -> tuple[float, ...]:
    scale_config = value_from_config(config, "template_scales_by_name", {})
    if not isinstance(scale_config, dict):
        return tuple(default_scales)
    value = scale_config.get(name)
    if value is None and name.startswith("tree"):
        value = scale_config.get("tree")
    if value is None:
        return tuple(default_scales)
    if isinstance(value, str):
        return tuple(parse_scales(value))
    if isinstance(value, list):
        return tuple(float(item) for item in value if float(item) > 0)
    return tuple(default_scales)


def region_for(config: dict[str, Any], name: str) -> dict[str, int] | None:
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        return None
    region = regions.get(name)
    if region is None and name.startswith("tree"):
        region = regions.get("tree")
    if not isinstance(region, dict):
        return None
    required = ("left", "top", "width", "height")
    if any(key not in region for key in required):
        raise ValueError(f"Region for {name} must contain left, top, width, height")
    return {key: int(region[key]) for key in required}


def click_offset_for(config: dict[str, Any], name: str) -> tuple[int, int]:
    offsets = value_from_config(config, "click_offsets", {})
    if not isinstance(offsets, dict):
        return (0, 0)
    offset = offsets.get(name)
    if offset is None and name.startswith("tree"):
        offset = offsets.get("tree")
    if not isinstance(offset, dict):
        return (0, 0)
    return (int(offset.get("x", 0)), int(offset.get("y", 0)))


def find_window_bounds(title_contains: str) -> dict[str, int] | None:
    if detect_platform() == "windows":
        try:
            import pygetwindow as gw  # type: ignore[import-not-found]
        except Exception:
            return None
        title_lower = title_contains.lower()
        for window in gw.getAllWindows():
            title = str(getattr(window, "title", "") or "")
            if title_lower not in title.lower():
                continue
            width = int(getattr(window, "width", 0) or 0)
            height = int(getattr(window, "height", 0) or 0)
            if width <= 0 or height <= 0:
                continue
            return {
                "left": int(getattr(window, "left", 0) or 0),
                "top": int(getattr(window, "top", 0) or 0),
                "width": width,
                "height": height,
            }
        return None

    try:
        import Quartz  # type: ignore[import-not-found]
    except Exception:
        return None

    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    for window in windows:
        owner = str(window.get("kCGWindowOwnerName") or "")
        title = str(window.get("kCGWindowName") or "")
        if title_contains not in owner and title_contains not in title:
            continue
        bounds = window.get("kCGWindowBounds")
        if not bounds:
            continue
        return {
            "left": int(bounds["X"]),
            "top": int(bounds["Y"]),
            "width": int(bounds["Width"]),
            "height": int(bounds["Height"]),
        }
    return None


def resolve_regions(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int] | None]:
    if not bool(value_from_config(config, "regions_are_window_relative", False)):
        return config, None

    window_title = str(value_from_config(config, "window_title", "RuneLite"))
    window = find_window_bounds(window_title)
    if window is None:
        print(f"RuneLite window not found for title/owner containing: {window_title}")
        return config, None

    resolved = dict(config)
    raw_regions = value_from_config(config, "regions", {})
    if not isinstance(raw_regions, dict):
        return resolved, window

    absolute_regions: dict[str, dict[str, int]] = {}
    for name, raw_region in raw_regions.items():
        if not isinstance(raw_region, dict):
            continue
        required = ("left", "top", "width", "height")
        if any(key not in raw_region for key in required):
            continue
        left = window["left"] + int(raw_region["left"])
        top = window["top"] + int(raw_region["top"])
        max_width = max(1, window["left"] + window["width"] - left)
        max_height = max(1, window["top"] + window["height"] - top)
        absolute_regions[str(name)] = {
            "left": left,
            "top": top,
            "width": min(int(raw_region["width"]), max_width),
            "height": min(int(raw_region["height"]), max_height),
        }

    resolved["regions"] = absolute_regions
    return resolved, window


def build_template_states(
    names: tuple[str, ...],
    templates_dir: Path,
    threshold: float,
    default_scales: list[float],
    config: dict[str, Any],
) -> dict[str, TemplateState]:
    return {
        name: TemplateState(
            name=name,
            path=template_path(templates_dir, name),
            threshold=threshold_for(config, name, threshold),
            scales=scales_for(config, name, default_scales),
            region=region_for(config, name),
            click_offset=click_offset_for(config, name),
        )
        for name in names
    }
