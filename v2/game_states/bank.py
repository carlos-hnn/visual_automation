from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.vision import TemplateMatch
from v2.game_states.template_state import TemplateState


class TemplateFinder(Protocol):
    def find(self, template: TemplateState, timeout: float) -> tuple[TemplateMatch | None, float, float]:
        ...


@dataclass(frozen=True)
class BankStatus:
    is_open: bool
    deposit_all_match: TemplateMatch | None
    deposit_all_score: float
    deposit_all_scale: float
    deposit_all_threshold: float


def detect_bank_status(
    state: TemplateFinder,
    deposit_all_template: TemplateState,
    timeout: float = 0.0,
) -> BankStatus:
    match, score, scale = state.find(deposit_all_template, timeout)
    return BankStatus(
        is_open=match is not None,
        deposit_all_match=match,
        deposit_all_score=score,
        deposit_all_scale=scale,
        deposit_all_threshold=deposit_all_template.threshold,
    )
