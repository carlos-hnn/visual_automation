from visual_automation.core.vision import TemplateMatch
from visual_automation.flows.hunter import cleanup_due, next_target

REGION = {"left": 0, "top": 0, "width": 200, "height": 200}


def marker(x: int, y: int) -> TemplateMatch:
    return TemplateMatch(x=x, y=y, width=10, height=10, score=100.0)


def test_first_target_is_nearest_to_game_center() -> None:
    edge = marker(10, 10)
    center = marker(95, 95)
    assert next_target([edge, center], None, REGION, 20.0) is center


def test_cleanup_is_due_every_ten_map_clicks() -> None:
    assert not cleanup_due(9, 10)
    assert cleanup_due(10, 10)
    assert not cleanup_due(11, 10)
    assert cleanup_due(20, 10)
