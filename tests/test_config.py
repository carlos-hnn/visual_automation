from pathlib import Path

from visual_automation.config import config_overrides, deep_merge
from visual_automation.template_config import SHARED_TEMPLATE_PATHS, template_path


def test_deep_merge_preserves_nested_defaults() -> None:
    defaults = {"monitor": 1, "regions": {"game": {"left": 0, "top": 0}}}
    override = {"regions": {"game": {"left": 12}}}

    assert deep_merge(defaults, override) == {
        "monitor": 1,
        "regions": {"game": {"left": 12, "top": 0}},
    }
    assert defaults["regions"]["game"]["left"] == 0


def test_config_overrides_removes_only_shared_values() -> None:
    defaults = {"monitor": 1, "poll_seconds": 0.12, "nested": {"stable": 3, "interval": 0.35}}
    config = {"monitor": 1, "poll_seconds": 0.5, "nested": {"stable": 3, "interval": 0.2}, "loops": 4}

    assert config_overrides(config, defaults) == {
        "poll_seconds": 0.5,
        "nested": {"interval": 0.2},
        "loops": 4,
    }


def test_template_path_falls_back_to_shared_asset(tmp_path: Path) -> None:
    assert template_path(tmp_path, "deposit_all") == SHARED_TEMPLATE_PATHS["deposit_all"]


def test_template_path_prefers_flow_specific_asset(tmp_path: Path) -> None:
    local = tmp_path / "bank_close.png"
    local.touch()
    assert template_path(tmp_path, "bank_close") == local
