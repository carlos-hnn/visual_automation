from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Region":
        required = ("left", "top", "width", "height")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"Region is missing keys: {', '.join(missing)}")

        return cls(
            left=int(data["left"]),
            top=int(data["top"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )

    def to_mss(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


class RegionManager:
    def __init__(self, config_path: str | Path = "config/regions.json") -> None:
        self.config_path = Path(config_path)
        self._regions: dict[str, Region] = {}
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            self._regions = {}
            return

        with self.config_path.open("r", encoding="utf-8") as file:
            raw = json.load(file)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid regions config: {self.config_path}")

        self._regions = {
            name: Region.from_mapping(value)
            for name, value in raw.items()
            if isinstance(value, dict)
        }

    def get_region(self, name: str) -> Region:
        try:
            return self._regions[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._regions)) or "none"
            raise KeyError(f"Unknown region '{name}'. Known regions: {known}") from exc

    def all(self) -> dict[str, Region]:
        return dict(self._regions)


_default_manager: RegionManager | None = None


def get_region(name: str, config_path: str | Path = "config/regions.json") -> Region:
    global _default_manager
    path = Path(config_path)
    if _default_manager is None or _default_manager.config_path != path:
        _default_manager = RegionManager(path)
    return _default_manager.get_region(name)
