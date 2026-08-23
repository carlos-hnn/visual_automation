from visual_automation.flows.cleaning_herbs import DEFAULTS


def test_cleaning_speed_is_preserved() -> None:
    assert DEFAULTS.after_herb_click_ticks == 0.05
    assert DEFAULTS.tick_seconds == 0.6
    assert DEFAULTS.herb_time_jitter == 0.015
    assert DEFAULTS.herb_spot_jitter == 3


def test_cleaning_uses_all_28_inventory_slots() -> None:
    assert DEFAULTS.empty_slots_required == 28


def test_first_bank_item_is_the_blast_furnace_top_left_slot() -> None:
    assert (DEFAULTS.first_item_x, DEFAULTS.first_item_y) == (106, 129)


def test_inventory_grid_matches_blast_furnace_layout() -> None:
    assert (DEFAULTS.inventory_first_x, DEFAULTS.inventory_first_y) == (40, 31)
    assert (DEFAULTS.inventory_column_spacing, DEFAULTS.inventory_row_spacing) == (44.0, 38.5)
