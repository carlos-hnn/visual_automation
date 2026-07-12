from __future__ import annotations

from core.screen import ScreenCapture
from v2.actions import StopKeys
from v2.game_states.template_state import TemplateMatcherState, TemplateState


class WoodcutFiremakeState(TemplateMatcherState):
    """Backward-compatible name for the generic template matcher state."""

    def __init__(self, screen: ScreenCapture, monitor: int, poll_seconds: float, stop_keys: StopKeys) -> None:
        super().__init__(screen, monitor, poll_seconds, stop_keys)
