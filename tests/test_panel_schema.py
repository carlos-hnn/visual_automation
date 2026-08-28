from visual_automation.panel_schema import field_label, field_section, panel_metadata


def test_detection_and_integration_fields_are_advanced() -> None:
    for key in ("mouse_backend", "regions", "bank_hsv_lower", "state_timeout", "spot_jitter"):
        assert field_section(key, None) == "advanced"


def test_operation_fields_remain_visible() -> None:
    for key in ("dry_run", "loops", "loads_before_collection", "first_item_clicks", "click_inventory_when_full"):
        assert field_section(key, None) == "operation"


def test_panel_metadata_has_readable_label() -> None:
    assert field_label("loads_before_collection") == "Loads Before Collection"
    assert panel_metadata({"loops": 2}) == {
        "loops": {"section": "operation", "label": "Loops"},
    }
