from __future__ import annotations

from dataclasses import dataclass
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
