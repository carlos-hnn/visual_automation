from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from visual_automation.actions.markers import MarkerActions
from visual_automation.core.geometry import relative_region
from visual_automation.core.waiting import WaitPolicy
from visual_automation.core.waiting import wait_until as poll_until
from visual_automation.game_states.template_state import TemplateState
from visual_automation.template_config import threshold_for


def make_template(
    name: str,
    path: Path,
    args: Any,
    config: dict[str, Any],
    region: dict[str, int] | None,
) -> TemplateState:
    """Build a template using the project's shared threshold/scale conventions."""
    return TemplateState(
        name=name,
        path=path,
        threshold=threshold_for(config, name, args.threshold),
        scales=tuple(args.template_scales),
        region=region,
        click_offset=(0, 0),
    )


def wait_for_state(
    label: str,
    predicate: Callable[[], bool],
    args: Any,
    stop_keys: Any,
) -> bool:
    return poll_until(
        label,
        predicate,
        stop_keys,
        WaitPolicy(timeout=args.state_timeout, interval=args.poll_seconds),
    )


def click_color_marker(
    label: str,
    screen: Any,
    region: dict[str, int],
    settings: Any,
    mouse: Any,
    args: Any,
    stop_keys: Any,
) -> bool:
    actions = MarkerActions(screen, mouse, args, stop_keys)
    marker = actions.wait_and_click(
        label,
        region,
        settings,
        timeout=args.state_timeout,
        interval=args.retry_seconds,
    )
    return marker is not None


__all__ = ["click_color_marker", "make_template", "relative_region", "wait_for_state"]
