from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateMatch:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def top_left(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def bottom_right(self) -> tuple[int, int]:
        return (self.x + self.width, self.y + self.height)
