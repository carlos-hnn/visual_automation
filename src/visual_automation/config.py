from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return data


def value_from_config(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, default)

