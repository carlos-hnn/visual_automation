from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from core.screen import Frame, ScreenCapture
from core.vision import TemplateMatch
from v2.actions import match_click_coordinates
from v2.config import value_from_config


@dataclass(frozen=True)
class MarkerSettings:
    hsv_min: tuple[int, int, int] = (70, 60, 60)
    hsv_max: tuple[int, int, int] = (110, 255, 255)
    min_pixels: int = 120
    min_dimension: int = 8
    max_dimension: int = 140
    grouping_pixels: int = 2
    min_fill_fraction: float = 0.0


def parse_hsv_triplet(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain three HSV values")
    return tuple(max(0, min(255, int(item))) for item in value)


def marker_settings_from_config(config: dict[str, Any], prefix: str = "cyan_marker") -> MarkerSettings:
    return MarkerSettings(
        hsv_min=parse_hsv_triplet(value_from_config(config, f"{prefix}_hsv_min", [70, 60, 60]), f"{prefix}_hsv_min"),
        hsv_max=parse_hsv_triplet(value_from_config(config, f"{prefix}_hsv_max", [110, 255, 255]), f"{prefix}_hsv_max"),
        min_pixels=max(1, int(value_from_config(config, f"{prefix}_min_pixels", 120))),
        min_dimension=max(1, int(value_from_config(config, f"{prefix}_min_dimension", 8))),
        max_dimension=max(1, int(value_from_config(config, f"{prefix}_max_dimension", 140))),
        grouping_pixels=max(0, int(value_from_config(config, f"{prefix}_grouping_pixels", 2))),
        min_fill_fraction=max(0.0, float(value_from_config(config, f"{prefix}_min_fill_fraction", 0.0))),
    )


def color_mask(image: np.ndarray, settings: MarkerSettings) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, np.array(settings.hsv_min, np.uint8), np.array(settings.hsv_max, np.uint8))


def find_color_markers(frame: Frame, settings: MarkerSettings) -> list[TemplateMatch]:
    raw_mask = color_mask(frame.image, settings)
    kernel_size = max(1, settings.grouping_pixels * 2 + 1)
    grouped = cv2.dilate(
        raw_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
        iterations=1,
    )
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(grouped)
    candidates: list[TemplateMatch] = []
    for label in range(1, count):
        x, y, width, height, _area = (int(item) for item in stats[label])
        pixels = int(np.count_nonzero(raw_mask[labels == label]))
        if pixels < settings.min_pixels:
            continue
        if min(width, height) < settings.min_dimension or max(width, height) > settings.max_dimension:
            continue
        fill_fraction = float(pixels) / max(1, width * height)
        if fill_fraction < settings.min_fill_fraction:
            continue
        candidates.append(
            TemplateMatch(
                x=frame.left + x,
                y=frame.top + y,
                width=width,
                height=height,
                score=float(pixels),
            )
        )
    return sorted(candidates, key=lambda match: match.score, reverse=True)


def best_color_marker(frame: Frame, settings: MarkerSettings) -> TemplateMatch | None:
    markers = find_color_markers(frame, settings)
    return markers[0] if markers else None


def capture_color_markers(
    screen: ScreenCapture,
    region: dict[str, int],
    settings: MarkerSettings,
) -> list[TemplateMatch]:
    return find_color_markers(screen.capture(region), settings)


def marker_click_point(match: TemplateMatch, click_scale: float, spot_jitter_pixels: int) -> tuple[int, int]:
    return match_click_coordinates(match, click_scale, spot_jitter_pixels)


def sorted_inventory_markers(markers: list[TemplateMatch], row_tolerance: int = 18) -> list[TemplateMatch]:
    return sorted(markers, key=lambda match: (round(match.center[1] / max(1, row_tolerance)), match.center[0]))
