"""Local visual automation framework."""

from visual_automation.core.regions import Region
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.vision import TemplateMatch, Vision

__all__ = [
    "Region",
    "ScreenCapture",
    "TemplateMatch",
    "Vision",
]
