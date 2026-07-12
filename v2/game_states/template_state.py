from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.screen import ScreenCapture
from core.vision import TemplateMatch
from v2.actions import StopKeys
from v2.game_states.template_matching import best_template_match, wait_for_template_match


@dataclass(frozen=True)
class TemplateState:
    name: str
    path: Path
    threshold: float
    scales: tuple[float, ...]
    region: dict[str, int] | None
    click_offset: tuple[int, int]


class TemplateMatcherState:
    def __init__(
        self,
        screen: ScreenCapture,
        monitor: int,
        poll_seconds: float,
        stop_keys: StopKeys,
    ) -> None:
        self.screen = screen
        self.monitor = monitor
        self.poll_seconds = poll_seconds
        self.stop_keys = stop_keys

    def find(self, template: TemplateState, timeout: float) -> tuple[TemplateMatch | None, float, float]:
        result = wait_for_template_match(
            screen=self.screen,
            template_path=template.path,
            monitor=self.monitor,
            template_scales=list(template.scales),
            threshold=template.threshold,
            timeout=max(0.0, timeout),
            poll_seconds=self.poll_seconds,
            stop_keys=self.stop_keys,
            region=template.region,
        )
        return result.match, result.best_seen.score, result.scale

    def exists(self, template: TemplateState, timeout: float) -> bool:
        match, _best_score, _scale = self.find(template, timeout)
        return match is not None

    def best(self, template: TemplateState) -> tuple[TemplateMatch | None, float, float]:
        match, _frame, scale = best_template_match(
            screen=self.screen,
            template_path=template.path,
            monitor=self.monitor,
            template_scales=list(template.scales),
            region=template.region,
        )
        return match, match.score, scale
