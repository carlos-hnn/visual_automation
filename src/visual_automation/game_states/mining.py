from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from visual_automation.config import value_from_config
from visual_automation.core.screen import Frame, ScreenCapture
from visual_automation.core.vision import TemplateMatch
from visual_automation.game_states.color_markers import capture_color_markers, marker_settings_from_config
from visual_automation.platforming import resolve_path


def parse_scales(value: Any) -> list[float]:
    if isinstance(value, str):
        scales = [float(item.strip()) for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        scales = [float(item) for item in value]
    else:
        scales = [1.0]
    return [scale for scale in scales if scale > 0] or [1.0]


def is_mining(screen: ScreenCapture, region: dict[str, int], config: dict[str, Any]) -> tuple[bool, int, float]:
    frame = screen.capture(region)
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    lower = np.array(value_from_config(config, "mining_status_hsv_min", [35, 110, 70]), np.uint8)
    upper = np.array(value_from_config(config, "mining_status_hsv_max", [90, 255, 255]), np.uint8)
    pixels = int(np.count_nonzero(cv2.inRange(hsv, lower, upper)))
    fraction = pixels / max(1, frame.image.shape[0] * frame.image.shape[1])
    required_pixels = int(value_from_config(config, "mining_status_min_green_pixels", 40))
    required_fraction = float(value_from_config(config, "mining_status_min_green_fraction", 0.004))
    return pixels >= required_pixels and fraction >= required_fraction, pixels, fraction


def best_template_match(
    screen: ScreenCapture,
    template_path: Path,
    region: dict[str, int],
    scales: list[float],
) -> tuple[TemplateMatch | None, float, float]:
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"template not found or unreadable: {template_path}")
    frame = screen.capture(region)
    best: TemplateMatch | None = None
    best_score = -1.0
    best_scale = scales[0] if scales else 1.0
    for scale in scales or [1.0]:
        resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if resized.shape[0] > frame.image.shape[0] or resized.shape[1] > frame.image.shape[1]:
            continue
        result = cv2.matchTemplate(frame.image, resized, cv2.TM_CCOEFF_NORMED)
        _minimum, maximum, _minimum_location, maximum_location = cv2.minMaxLoc(result)
        if float(maximum) > best_score:
            best_score = float(maximum)
            best_scale = scale
            best = TemplateMatch(
                x=frame.left + int(maximum_location[0]),
                y=frame.top + int(maximum_location[1]),
                width=int(resized.shape[1]),
                height=int(resized.shape[0]),
                score=best_score,
            )
    return best, best_score, best_scale


def inventory_slot_occupancy(frame: Frame, config: dict[str, Any]) -> tuple[int, int, str]:
    rows = max(1, int(value_from_config(config, "inventory_slots_rows", 7)))
    columns = max(1, int(value_from_config(config, "inventory_slots_cols", 4)))
    sample_width = max(8, int(value_from_config(config, "inventory_slot_sample_width", 32)))
    sample_height = max(8, int(value_from_config(config, "inventory_slot_sample_height", 28)))
    standard_deviation = float(value_from_config(config, "inventory_slot_std_threshold", 4.0))
    edge_threshold = float(value_from_config(config, "inventory_slot_edge_threshold", 0.01))
    occupied_slots: list[str] = []
    for row in range(rows):
        for column in range(columns):
            center_x = round((column + 0.5) * frame.width / columns)
            center_y = round((row + 0.5) * frame.height / rows)
            left = max(0, center_x - sample_width // 2)
            top = max(0, center_y - sample_height // 2)
            crop = frame.image[top:min(frame.height, top + sample_height), left:min(frame.width, left + sample_width)]
            if crop.size == 0:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            edge_fraction = np.count_nonzero(cv2.Canny(crop, 40, 100)) / max(1, crop.shape[0] * crop.shape[1])
            if float(gray.std()) >= standard_deviation or edge_fraction >= edge_threshold:
                occupied_slots.append(f"{row + 1}:{column + 1}")
    detail = f"occupied_slots={','.join(occupied_slots) if occupied_slots else 'none'}"
    return len(occupied_slots), rows * columns, detail


def inventory_is_full(
    screen: ScreenCapture,
    regions: dict[str, dict[str, int]],
    config: dict[str, Any],
) -> tuple[bool, str]:
    mode = str(value_from_config(config, "inventory_full_mode", "empty_slot_template")).strip().lower()
    if mode == "always_false":
        return False, "inventory check disabled"
    if mode == "slot_occupancy":
        occupied, total, detail = inventory_slot_occupancy(screen.capture(regions["inventory"]), config)
        empty = total - occupied
        allowed = max(0, int(value_from_config(config, "inventory_full_allowed_empty_slots", 0)))
        return empty <= allowed, f"slot_occupancy occupied={occupied}/{total}, empty={empty}, {detail}"
    if mode == "empty_slot_template":
        path = resolve_path(value_from_config(
            config, "empty_inventory_slot_template", "templates/gem_cutting/empty_inventory_slot.png",
        ))
        threshold = float(value_from_config(config, "empty_inventory_slot_threshold", 0.96))
        match, score, scale = best_template_match(
            screen, path, regions["inventory"], parse_scales(value_from_config(config, "empty_inventory_slot_scales", [1.0])),
        )
        return not (match is not None and score >= threshold), f"empty_slot score={score:.3f}/{threshold:.3f} scale={scale:g}"
    if mode in {"marker_count", "last_slot_marker"}:
        markers = capture_color_markers(screen, regions["inventory"], marker_settings_from_config(config, "inventory_marker"))
        if mode == "marker_count":
            required = int(value_from_config(config, "inventory_full_marker_count", 28))
            return len(markers) >= required, f"inventory markers={len(markers)}/{required}"
        region = regions["inventory"]
        rows = max(1, int(value_from_config(config, "inventory_slots_rows", 7)))
        columns = max(1, int(value_from_config(config, "inventory_slots_cols", 4)))
        last_left = region["left"] + region["width"] * (columns - 1) / columns
        last_top = region["top"] + region["height"] * (rows - 1) / rows
        occupied = any(marker.center[0] >= last_left and marker.center[1] >= last_top for marker in markers)
        return occupied, f"last slot marker={'present' if occupied else 'absent'}; markers={len(markers)}"
    raise ValueError(f"unsupported inventory_full_mode: {mode}")
