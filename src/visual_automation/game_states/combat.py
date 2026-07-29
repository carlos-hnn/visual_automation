from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from visual_automation.core.screen import Frame


@dataclass(frozen=True)
class PercentBarStatus:
    percent: float
    is_low: bool
    threshold_percent: float


@dataclass(frozen=True)
class CombatActivityStatus:
    green_fraction: float
    in_combat: bool
    threshold: float


def combat_green_fraction(frame: Frame) -> float:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array((35, 110, 70), np.uint8), np.array((90, 255, 255), np.uint8))
    return float(np.count_nonzero(green)) / max(1, green.size)


def health_percent(frame: Frame, minimum_row_coverage: float = 0.18) -> float:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, np.array((0, 120, 95), np.uint8), np.array((12, 255, 255), np.uint8))
    high_red = cv2.inRange(hsv, np.array((168, 120, 95), np.uint8), np.array((179, 255, 255), np.uint8))
    mask = cv2.bitwise_or(low_red, high_red)
    rows = np.flatnonzero(np.count_nonzero(mask, axis=1) / max(1, mask.shape[1]) >= minimum_row_coverage)
    if rows.size == 0:
        return 0.0
    return max(0.0, min(100.0, (mask.shape[0] - int(rows.min())) * 100.0 / mask.shape[0]))


def prayer_percent(frame: Frame, minimum_row_coverage: float = 0.75) -> float:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array((45, 100, 80), np.uint8), np.array((100, 255, 255), np.uint8))
    rows = np.flatnonzero(np.count_nonzero(mask, axis=1) / max(1, mask.shape[1]) >= minimum_row_coverage)
    if rows.size == 0:
        return 0.0
    return max(0.0, min(100.0, (mask.shape[0] - int(rows.min())) * 100.0 / mask.shape[0]))


def detect_health_status(frame: Frame, threshold_percent: float) -> PercentBarStatus:
    percent = health_percent(frame)
    return PercentBarStatus(percent=percent, is_low=percent < threshold_percent, threshold_percent=threshold_percent)


def detect_prayer_status(frame: Frame, threshold_percent: float) -> PercentBarStatus:
    percent = prayer_percent(frame)
    return PercentBarStatus(percent=percent, is_low=percent < threshold_percent, threshold_percent=threshold_percent)


def detect_combat_activity(frame: Frame, threshold: float) -> CombatActivityStatus:
    fraction = combat_green_fraction(frame)
    return CombatActivityStatus(green_fraction=fraction, in_combat=fraction >= threshold, threshold=threshold)
