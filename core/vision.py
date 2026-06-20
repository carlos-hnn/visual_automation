from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.debug import save_annotated_match
from core.regions import Region
from core.screen import Frame, ScreenCapture


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


class Vision:
    def __init__(
        self,
        screen: ScreenCapture | None = None,
        debug_enabled: bool = False,
        debug_dir: str | Path = "logs/debug",
    ) -> None:
        self.screen = screen or ScreenCapture()
        self.debug_enabled = debug_enabled
        self.debug_dir = Path(debug_dir)

    def find_template(
        self,
        template_path: str | Path,
        region: Region | dict[str, int] | None = None,
        threshold: float = 0.85,
    ) -> TemplateMatch | None:
        frame, template, result = self._match(template_path, region)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < threshold:
            return None

        match = self._build_match(max_loc, template, frame.left, frame.top, max_val)
        if self.debug_enabled:
            relative_top_left = (match.x - frame.left, match.y - frame.top)
            relative_bottom_right = (relative_top_left[0] + match.width, relative_top_left[1] + match.height)
            save_annotated_match(frame.image, relative_top_left, relative_bottom_right, match.score, self.debug_dir)
        return match

    def find_all_templates(
        self,
        template_path: str | Path,
        region: Region | dict[str, int] | None = None,
        threshold: float = 0.85,
    ) -> list[TemplateMatch]:
        frame, template, result = self._match(template_path, region)
        locations = np.where(result >= threshold)
        matches = [
            self._build_match((int(x), int(y)), template, frame.left, frame.top, float(result[y, x]))
            for y, x in zip(*locations)
        ]
        return self._deduplicate(matches)

    def exists(
        self,
        template_path: str | Path,
        region: Region | dict[str, int] | None = None,
        threshold: float = 0.85,
    ) -> bool:
        return self.find_template(template_path, region=region, threshold=threshold) is not None

    def wait_for_template(
        self,
        template_path: str | Path,
        timeout: float = 5,
        region: Region | dict[str, int] | None = None,
        threshold: float = 0.85,
        poll_interval: float = 0.15,
    ) -> TemplateMatch | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = self.find_template(template_path, region=region, threshold=threshold)
            if match is not None:
                return match
            time.sleep(poll_interval)
        return None

    def _match(
        self,
        template_path: str | Path,
        region: Region | dict[str, int] | None,
    ) -> tuple[Frame, np.ndarray, np.ndarray]:
        template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if template is None:
            raise FileNotFoundError(f"Template image not found or unreadable: {template_path}")

        frame = self.screen.capture(region)
        if template.shape[0] > frame.image.shape[0] or template.shape[1] > frame.image.shape[1]:
            raise ValueError("Template is larger than captured frame/region")

        result = cv2.matchTemplate(frame.image, template, cv2.TM_CCOEFF_NORMED)
        return frame, template, result

    def _build_match(
        self,
        location: tuple[int, int],
        template: np.ndarray,
        frame_left: int,
        frame_top: int,
        score: float,
    ) -> TemplateMatch:
        template_h, template_w = template.shape[:2]
        return TemplateMatch(
            x=frame_left + int(location[0]),
            y=frame_top + int(location[1]),
            width=int(template_w),
            height=int(template_h),
            score=float(score),
        )

    def _deduplicate(self, matches: list[TemplateMatch]) -> list[TemplateMatch]:
        matches = sorted(matches, key=lambda item: item.score, reverse=True)
        kept: list[TemplateMatch] = []
        for match in matches:
            if all(self._distance(match.center, existing.center) > min(match.width, match.height) / 2 for existing in kept):
                kept.append(match)
        return kept

    def _distance(self, a: tuple[int, int], b: tuple[int, int]) -> float:
        return float(np.hypot(a[0] - b[0], a[1] - b[1]))
