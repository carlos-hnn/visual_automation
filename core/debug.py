from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def save_screenshot(image: np.ndarray, directory: str | Path, prefix: str = "screenshot") -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"{prefix}_{timestamp_slug()}.png"
    cv2.imwrite(str(output), image)
    return output


def save_annotated_match(
    image: np.ndarray,
    top_left: tuple[int, int],
    bottom_right: tuple[int, int],
    score: float,
    directory: str | Path,
    prefix: str = "match",
) -> Path:
    annotated = image.copy()
    cv2.rectangle(annotated, top_left, bottom_right, (0, 255, 0), 2)
    cv2.putText(
        annotated,
        f"{score:.3f}",
        (top_left[0], max(20, top_left[1] - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    return save_screenshot(annotated, directory, prefix)
