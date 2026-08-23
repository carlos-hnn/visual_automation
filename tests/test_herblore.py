import numpy as np

from visual_automation.flows.herblore import DEFAULTS, ingredient_markers_finished, region_change_score


def test_bank_items_are_the_first_two_top_row_slots() -> None:
    assert (DEFAULTS.first_item_x, DEFAULTS.first_item_y) == (106, 129)
    assert (DEFAULTS.second_item_x, DEFAULTS.second_item_y) == (151, 129)
    assert (DEFAULTS.deposit_hover_x, DEFAULTS.deposit_hover_y) == (484, 851)
    assert (DEFAULTS.inventory_park_x, DEFAULTS.inventory_park_y) == (450, 500)


def test_inventory_grid_uses_all_slots() -> None:
    assert DEFAULTS.empty_slots_required == 28
    assert (DEFAULTS.inventory_first_x, DEFAULTS.inventory_first_y) == (40, 31)


def test_production_waits_before_low_frequency_validation() -> None:
    assert DEFAULTS.production_wait_ticks == 25.0
    assert DEFAULTS.tick_seconds == 0.6
    assert DEFAULTS.completion_poll_seconds == 1.0


def test_chat_change_score_is_zero_for_equal_frames() -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    assert region_change_score(frame, frame.copy()) == 0.0


def test_chat_change_score_detects_visual_change() -> None:
    before = np.zeros((4, 4, 3), dtype=np.uint8)
    after = np.full((4, 4, 3), 20, dtype=np.uint8)
    assert region_change_score(before, after) == 20.0


def test_mixing_finishes_when_both_reagents_are_exhausted() -> None:
    assert ingredient_markers_finished(green_count=0, red_count=0)
    assert not ingredient_markers_finished(green_count=0, red_count=14)
    assert not ingredient_markers_finished(green_count=14, red_count=0)
    assert not ingredient_markers_finished(green_count=1, red_count=1)
