from types import SimpleNamespace
from unittest.mock import patch

from visual_automation.core.vision import TemplateMatch
from visual_automation.flows.mine_bank import (
    configured_point,
    marker_nearest_rock_center,
    run_flow,
    sort_rocks,
    wait_for_mining_completion,
)


def test_sort_rocks_orders_top_then_left_to_right() -> None:
    rocks = [
        TemplateMatch(x=30, y=40, width=10, height=10, score=1),
        TemplateMatch(x=10, y=40, width=10, height=10, score=1),
        TemplateMatch(x=20, y=10, width=10, height=10, score=1),
    ]
    assert [rock.center for rock in sort_rocks(rocks)] == [(25, 15), (15, 45), (35, 45)]


def test_configured_point_can_be_window_relative() -> None:
    config = {"travel_fallback_point": {"x": 20, "y": 30}}
    window = {"left": 100, "top": 200, "width": 800, "height": 600}
    assert configured_point(config, "travel_fallback_point", window) == (120, 230)


def test_travel_marker_must_be_near_center_of_rock_cluster() -> None:
    rocks = [
        TemplateMatch(x=90, y=80, width=10, height=10, score=1),
        TemplateMatch(x=110, y=80, width=10, height=10, score=1),
        TemplateMatch(x=100, y=110, width=10, height=10, score=1),
    ]
    unrelated = TemplateMatch(x=300, y=300, width=10, height=10, score=5)
    between = TemplateMatch(x=100, y=90, width=10, height=10, score=1)
    assert marker_nearest_rock_center([unrelated, between], rocks, 30) == between
    assert marker_nearest_rock_center([unrelated], rocks, 30) is None


def test_run_flow_constructs_mouse_with_supported_arguments() -> None:
    args = SimpleNamespace(
        move_duration_min=0.1,
        move_duration_max=0.2,
        spot_jitter=1,
        countdown=0.0,
        dry_run=True,
        dry_run_sleep=False,
        time_jitter=0.0,
        start_mode="banking",
    )
    with (
        patch("visual_automation.flows.mine_bank.required_regions", return_value=({}, None, {})),
        patch("visual_automation.flows.mine_bank.build_mouse", autospec=True) as build,
        patch("visual_automation.flows.mine_bank.wait_seconds", return_value=False),
        patch("visual_automation.flows.mine_bank.StopKeys"),
    ):
        assert run_flow(args, {}) == 0
    build.assert_called_once_with(0.1, 0.2, spot_jitter_pixels=1)


def test_wait_for_mining_completion_requires_started_then_finished() -> None:
    args = SimpleNamespace(
        stop_keys=SimpleNamespace(stop_requested=False),
        mining_start_timeout_seconds=1.0,
        mining_end_timeout_seconds=1.0,
        mining_status_poll_seconds=0.01,
    )
    with patch(
        "visual_automation.flows.mine_bank.is_mining",
        side_effect=[(True, 187, 0.018), (False, 0, 0.0)],
    ):
        assert wait_for_mining_completion(object(), {"mining_status": {}}, {}, args)
