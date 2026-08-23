from visual_automation.game_states.inventory import InventorySlotStatus


def test_default_loads_before_collection_is_three() -> None:
    from visual_automation.flows.blast_furnace import DEFAULTS

    assert DEFAULTS.loads_before_collection == 3


def test_default_empty_inventory_allows_fixed_coins_slot() -> None:
    from visual_automation.flows.blast_furnace import DEFAULTS

    assert DEFAULTS.empty_slots_required == 23


def test_inventory_slot_status_is_full_only_without_empty_slots() -> None:
    assert InventorySlotStatus(empty_slots=0, empty_required=10).is_full
    assert not InventorySlotStatus(empty_slots=1, empty_required=10).is_full


def test_inventory_slot_status_requires_calibrated_empty_count() -> None:
    assert not InventorySlotStatus(empty_slots=1, empty_required=10).is_empty
    assert not InventorySlotStatus(empty_slots=9, empty_required=10).is_empty
    assert InventorySlotStatus(empty_slots=10, empty_required=10).is_empty
    assert InventorySlotStatus(empty_slots=11, empty_required=10).is_empty
