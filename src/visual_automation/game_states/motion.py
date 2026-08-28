from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class MotionStatus:
    difference: float
    is_moving: bool


def frame_motion_difference(before: np.ndarray, after: np.ndarray, scale: float = 0.25) -> float:
    """Return the mean grayscale pixel difference between two captured frames."""
    if before.shape != after.shape:
        raise ValueError("motion frames must have matching dimensions")
    width = max(1, round(before.shape[1] * max(0.05, min(1.0, scale))))
    height = max(1, round(before.shape[0] * max(0.05, min(1.0, scale))))
    first = cv2.resize(before, (width, height), interpolation=cv2.INTER_AREA)
    second = cv2.resize(after, (width, height), interpolation=cv2.INTER_AREA)
    first_gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    second_gray = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    return float(np.mean(cv2.absdiff(first_gray, second_gray)))


def classify_motion(before: np.ndarray, after: np.ndarray, threshold: float, scale: float = 0.25) -> MotionStatus:
    difference = frame_motion_difference(before, after, scale)
    return MotionStatus(difference=difference, is_moving=difference > threshold)
