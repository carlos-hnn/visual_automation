from visual_automation.config import config_overrides, deep_merge


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
