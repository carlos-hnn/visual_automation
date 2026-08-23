from __future__ import annotations

from types import SimpleNamespace

from visual_automation.actions.markers import MarkerActions, StabilityPolicy, select_marker
from visual_automation.core.vision import TemplateMatch

REGION = {"left": 100, "top": 200, "width": 200, "height": 100}


def marker(x: int, y: int, score: float = 100.0) -> TemplateMatch:
    return TemplateMatch(x=x, y=y, width=20, height=20, score=score)


def test_select_marker_strategies() -> None:
    left = marker(110, 220, 50)
    center = marker(190, 240, 100)
    right = marker(270, 210, 75)
    markers = [center, right, left]

    assert select_marker(markers, REGION, "best") is center
    assert select_marker(markers, REGION, "nearest") is center
    assert select_marker(markers, REGION, "leftmost") is left
    assert select_marker(markers, REGION, "rightmost") is right


def test_stable_click_uses_fresh_final_marker() -> None:
    sequence = [marker(190, 240), marker(192, 241), marker(191, 242), marker(193, 241)]

    class FakeActions(MarkerActions):
        def find(self, *_args, **_kwargs):
            item = sequence.pop(0)
            return item, [item]

    args = SimpleNamespace(click_scale=1.0, spot_jitter=0, dry_run=True)
    stop = SimpleNamespace(stop_requested=False)
    actions = FakeActions(None, None, args, stop)

    result = actions.wait_until_stable_and_click(
        "marker",
        REGION,
        None,
        timeout=1.0,
        policy=StabilityPolicy(samples=3, interval=0.001, tolerance=4),
    )

    assert result is not None
    assert result.center == (203, 251)
    assert not sequence
