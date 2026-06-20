from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import mss
import numpy as np

from core.regions import Region


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    left: int
    top: int
    width: int
    height: int


class ScreenCapture:
    def __init__(self, monitor: int = 1) -> None:
        self.monitor = monitor
        self._sct = mss.mss()

    @property
    def monitors(self) -> list[dict[str, Any]]:
        return list(self._sct.monitors)

    def monitor_bounds(self) -> dict[str, int]:
        try:
            return dict(self._sct.monitors[self.monitor])
        except IndexError as exc:
            raise ValueError(f"Monitor {self.monitor} not found. Available: {len(self._sct.monitors) - 1}") from exc

    def capture(self, region: Region | dict[str, int] | None = None) -> Frame:
        bounds = self._normalize_region(region) if region is not None else self.monitor_bounds()
        screenshot = self._sct.grab(bounds)
        bgra = np.array(screenshot, dtype=np.uint8)
        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

        return Frame(
            image=bgr,
            left=int(bounds["left"]),
            top=int(bounds["top"]),
            width=int(bounds["width"]),
            height=int(bounds["height"]),
        )

    def close(self) -> None:
        self._sct.close()

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _normalize_region(self, region: Region | dict[str, int]) -> dict[str, int]:
        if isinstance(region, Region):
            return region.to_mss()
        return {
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": int(region["width"]),
            "height": int(region["height"]),
        }
