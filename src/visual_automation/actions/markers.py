from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any, Literal

from visual_automation.core.geometry import point_in_region, region_center
from visual_automation.core.vision import TemplateMatch
from visual_automation.game_states.color_markers import (
    MarkerSettings,
    capture_color_markers,
    marker_click_point,
)

MarkerStrategy = Literal["best", "nearest", "leftmost", "rightmost"]


def click_marker(mouse: Any, marker: TemplateMatch, label: str, args: Any, dry_run: bool) -> None:
    """Click a detected marker with the common jitter and scaling policy."""
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if dry_run:
        print(
            f"{label}: would click=({x},{y}), center={marker.center}, "
            f"pixels={marker.score:.0f}, rect=({marker.x},{marker.y},{marker.width},{marker.height})"
        )
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), center={marker.center}, pixels={marker.score:.0f}")


@dataclass(frozen=True)
class StabilityPolicy:
    samples: int = 3
    interval: float = 0.35
    tolerance: int = 4


def select_marker(
    markers: list[TemplateMatch],
    region: dict[str, int],
    strategy: MarkerStrategy = "best",
) -> TemplateMatch | None:
    if not markers:
        return None
    if strategy == "nearest":
        center = region_center(region)
        return min(markers, key=lambda marker: math.dist(marker.center, center))
    if strategy == "leftmost":
        return min(markers, key=lambda marker: marker.center[0])
    if strategy == "rightmost":
        return max(markers, key=lambda marker: marker.center[0])
    return markers[0]


@dataclass
class MarkerActions:
    screen: Any
    mouse: Any
    args: Any
    stop_keys: Any

    def find(
        self,
        region: dict[str, int],
        settings: MarkerSettings,
        *,
        strategy: MarkerStrategy = "best",
        exclusions: tuple[dict[str, int], ...] = (),
    ) -> tuple[TemplateMatch | None, list[TemplateMatch]]:
        markers = capture_color_markers(self.screen, region, settings)
        if exclusions:
            markers = [
                marker for marker in markers
                if not any(point_in_region(marker.center, excluded) for excluded in exclusions)
            ]
        return select_marker(markers, region, strategy), markers

    def click(self, marker: TemplateMatch, label: str) -> None:
        click_marker(self.mouse, marker, label, self.args, self.args.dry_run)

    def wait_and_click(
        self,
        label: str,
        region: dict[str, int],
        settings: MarkerSettings,
        *,
        timeout: float,
        interval: float,
        strategy: MarkerStrategy = "best",
        exclusions: tuple[dict[str, int], ...] = (),
    ) -> TemplateMatch | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.stop_keys.stop_requested and time.monotonic() < deadline:
            marker, markers = self.find(region, settings, strategy=strategy, exclusions=exclusions)
            if marker is not None:
                print(f"{label}: {len(markers)} marker(s)")
                self.click(marker, label)
                return marker
            time.sleep(max(0.01, interval))
        print(f"{label}: not found before timeout")
        return None

    def wait_until_stable_and_click(
        self,
        label: str,
        region: dict[str, int],
        settings: MarkerSettings,
        *,
        timeout: float,
        policy: StabilityPolicy,
        strategy: MarkerStrategy = "nearest",
        exclusions: tuple[dict[str, int], ...] = (),
    ) -> TemplateMatch | None:
        """Require stable samples, then recapture once more immediately before clicking."""
        deadline = time.monotonic() + max(0.0, timeout)
        previous: TemplateMatch | None = None
        stable = 0
        while not self.stop_keys.stop_requested and time.monotonic() < deadline:
            marker, _ = self.find(region, settings, strategy=strategy, exclusions=exclusions)
            if marker is None:
                previous, stable = None, 0
            else:
                unchanged = previous is not None and (
                    abs(marker.center[0] - previous.center[0]) <= policy.tolerance
                    and abs(marker.center[1] - previous.center[1]) <= policy.tolerance
                )
                stable = stable + 1 if unchanged else 1
                previous = marker
                print(f"{label}: center={marker.center}; stable={stable}/{policy.samples}")
                if stable >= max(1, policy.samples):
                    fresh, markers = self.find(
                        region, settings, strategy=strategy, exclusions=exclusions,
                    )
                    if fresh is not None and (
                        abs(fresh.center[0] - marker.center[0]) <= policy.tolerance
                        and abs(fresh.center[1] - marker.center[1]) <= policy.tolerance
                    ):
                        print(f"{label}: final capture confirmed; {len(markers)} marker(s)")
                        self.click(fresh, label)
                        return fresh
                    previous, stable = None, 0
            time.sleep(max(0.05, policy.interval))
        print(f"{label}: no stable marker before timeout")
        return None
