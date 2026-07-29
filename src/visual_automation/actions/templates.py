from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

from visual_automation.actions.mouse import match_click_coordinates
from visual_automation.core.vision import TemplateMatch
from visual_automation.game_states.template_state import TemplateState


class TemplateFinder(Protocol):
    def find(self, template: TemplateState, timeout: float) -> tuple[TemplateMatch | None, float, float]: ...


@dataclass
class TemplateActions:
    state: TemplateFinder
    mouse: Any
    args: Any
    dry_run: bool

    def click_match(self, template: TemplateState, match: TemplateMatch, scale: float) -> None:
        x, y = match_click_coordinates(match, self.args.click_scale, self.args.spot_jitter)
        x += template.click_offset[0]
        y += template.click_offset[1]
        if self.dry_run:
            print(f"{template.name}: found score={match.score:.3f}, scale={scale:g}, would click=({x},{y})")
            return
        if self.args.pre_click_jitter > 0:
            time.sleep(random.uniform(0.0, self.args.pre_click_jitter))
        self.mouse.click(x, y)
        print(f"{template.name}: clicked score={match.score:.3f}, scale={scale:g}, at=({x},{y})")

    def find_and_click(self, template: TemplateState, timeout: float | None = None) -> bool:
        match, score, scale = self.state.find(
            template, self.args.click_timeout if timeout is None else timeout
        )
        if match is None:
            print(f"{template.name}: not found; best={score:.3f}, threshold={template.threshold:.3f}")
            return False
        self.click_match(template, match, scale)
        return True
