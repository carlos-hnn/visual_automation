from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_DEFAULTS_PATH = PROJECT_ROOT / "config" / "shared_defaults.json"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def config_overrides(config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Return only values that differ from shared defaults."""
    result: dict[str, Any] = {}
    for key, value in config.items():
        default = defaults.get(key, object())
        if isinstance(value, dict) and isinstance(default, dict):
            nested = config_overrides(value, default)
            if nested:
                result[key] = nested
        elif value != default:
            result[key] = deepcopy(value)
    return result


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return data


def load_json_config(
    path: Path | None,
    *,
    include_shared: bool = True,
) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    local = _read_json_object(path)
    if not include_shared or path.resolve() == SHARED_DEFAULTS_PATH.resolve() or not SHARED_DEFAULTS_PATH.exists():
        return local
    return deep_merge(_read_json_object(SHARED_DEFAULTS_PATH), local)


def value_from_config(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, default)
