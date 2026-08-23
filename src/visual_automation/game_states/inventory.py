from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from visual_automation.core.screen import ScreenCapture
from visual_automation.core.vision import TemplateMatch
from visual_automation.game_states.template_state import TemplateState


class TemplateFinder(Protocol):
    def find(self, template: TemplateState, timeout: float) -> tuple[TemplateMatch | None, float, float]: ...


@dataclass(frozen=True)
class InventoryStatus:
    has_empty_slot: bool
    empty_slot_match: TemplateMatch | None
    empty_slot_score: float
    empty_slot_scale: float
    empty_slot_threshold: float

    @property
    def is_full(self) -> bool:
        return not self.has_empty_slot


@dataclass(frozen=True)
class InventorySlotStatus:
    empty_slots: int
    empty_required: int

    @property
    def is_full(self) -> bool:
        return self.empty_slots == 0

    @property
    def is_empty(self) -> bool:
        return self.empty_slots >= self.empty_required


def detect_inventory_slot_status(
    screen: ScreenCapture,
    empty_slot_template: TemplateState,
    empty_required: int,
) -> InventorySlotStatus:
    """Count distinct empty slots, supporting the template's configured scales."""
    source = cv2.imread(str(empty_slot_template.path), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(f"Template image not found or unreadable: {empty_slot_template.path}")
    frame = screen.capture(empty_slot_template.region)
    candidates: list[tuple[float, int, int, int, int]] = []
    for scale in empty_slot_template.scales:
        width = max(1, round(source.shape[1] * scale))
        height = max(1, round(source.shape[0] * scale))
        if width > frame.width or height > frame.height:
            continue
        resized = cv2.resize(source, (width, height), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(frame.image, resized, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= empty_slot_template.threshold)
        candidates.extend(
            (float(result[y, x]), frame.left + int(x), frame.top + int(y), width, height) for y, x in zip(ys, xs)
        )

    kept: list[tuple[float, int, int, int, int]] = []
    for candidate in sorted(candidates, reverse=True):
        _, x, y, width, height = candidate
        center = (x + width // 2, y + height // 2)
        if all(
            float(np.hypot(center[0] - (other[1] + other[3] // 2), center[1] - (other[2] + other[4] // 2)))
            > min(width, height) / 2
            for other in kept
        ):
            kept.append(candidate)
    return InventorySlotStatus(empty_slots=len(kept), empty_required=max(1, empty_required))


def detect_inventory_grid_status(
    screen: ScreenCapture,
    region: dict[str, int],
    empty_required: int,
    first_center: tuple[int, int],
    spacing: tuple[float, float],
    columns: int = 4,
    rows: int = 7,
    patch_radius: int = 13,
    occupied_std_threshold: float = 8.0,
) -> InventorySlotStatus:
    """Classify fixed inventory cells from visual detail around each center."""
    frame = screen.capture(region)
    gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    empty_slots = 0
    radius = max(2, int(patch_radius))
    for row in range(rows):
        for column in range(columns):
            center_x = round(first_center[0] + column * spacing[0])
            center_y = round(first_center[1] + row * spacing[1])
            patch = gray[
                max(0, center_y - radius) : min(frame.height, center_y + radius + 1),
                max(0, center_x - radius) : min(frame.width, center_x + radius + 1),
            ]
            if patch.size and float(patch.std()) < occupied_std_threshold:
                empty_slots += 1
    return InventorySlotStatus(empty_slots=empty_slots, empty_required=max(1, empty_required))


def detect_inventory_status(
    state: TemplateFinder,
    empty_slot_template: TemplateState,
    timeout: float = 0.0,
) -> InventoryStatus:
    match, score, scale = state.find(empty_slot_template, timeout)
    return InventoryStatus(
        has_empty_slot=match is not None,
        empty_slot_match=match,
        empty_slot_score=score,
        empty_slot_scale=scale,
        empty_slot_threshold=empty_slot_template.threshold,
    )
