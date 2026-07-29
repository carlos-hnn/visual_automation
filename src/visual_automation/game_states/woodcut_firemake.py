from __future__ import annotations

from visual_automation.actions import StopKeys
from visual_automation.core.screen import ScreenCapture
from visual_automation.game_states.template_state import TemplateMatcherState


class WoodcutFiremakeState(TemplateMatcherState):
    """Backward-compatible name for the generic template matcher state."""

    def __init__(self, screen: ScreenCapture, monitor: int, poll_seconds: float, stop_keys: StopKeys) -> None:
        super().__init__(screen, monitor, poll_seconds, stop_keys)
