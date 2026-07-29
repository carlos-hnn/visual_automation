from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from visual_automation.actions import StopKeys
from visual_automation.core.safety import report_failure, report_progress
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.vision import TemplateMatch
from visual_automation.game_states.template_matching import best_template_match, wait_for_template_match


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
        if result.match is None:
            report_failure(f"template:{template.name}")
        else:
            report_progress(f"template:{template.name}")
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
        if match.score >= template.threshold:
            report_progress(f"template:{template.name}")
        else:
            report_failure(f"template:{template.name}")
        return match, match.score, scale
