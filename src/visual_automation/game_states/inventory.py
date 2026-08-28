from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2

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
    occupied_centers: tuple[tuple[int, int], ...] = ()

    @property
    def is_full(self) -> bool:
        return self.empty_slots == 0

    @property
    def is_empty(self) -> bool:
        return self.empty_slots >= self.empty_required


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
    occupied_centers: list[tuple[int, int]] = []
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
            elif patch.size:
                occupied_centers.append((frame.left + center_x, frame.top + center_y))
    return InventorySlotStatus(
        empty_slots=empty_slots,
        empty_required=max(1, empty_required),
        occupied_centers=tuple(occupied_centers),
    )


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
