from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.vision import TemplateMatch
from v2.game_states.template_state import TemplateState


class TemplateFinder(Protocol):
    def find(self, template: TemplateState, timeout: float) -> tuple[TemplateMatch | None, float, float]:
        ...


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
