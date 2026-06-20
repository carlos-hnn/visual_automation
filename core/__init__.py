"""Local visual automation framework."""

from core.regions import Region
from core.screen import ScreenCapture
from core.state_machine import AutomationState, StateMachine
from core.vision import TemplateMatch, Vision

__all__ = [
    "AutomationState",
    "Region",
    "ScreenCapture",
    "StateMachine",
    "TemplateMatch",
    "Vision",
]
