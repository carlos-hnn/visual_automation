from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from visual_automation.actions.stop_keys import StopKeys
from visual_automation.core.safety import report_failure, report_progress
from visual_automation.core.screen import Frame, ScreenCapture
from visual_automation.core.vision import TemplateMatch


@dataclass(frozen=True)
class TemplateSearchResult:
    match: TemplateMatch | None
    best_seen: TemplateMatch
    scale: float


def parse_scales(value: str) -> list[float]:
    scales = [float(item.strip()) for item in value.split(",") if item.strip()]
    scales = [scale for scale in scales if scale > 0]
    if not scales:
        raise ValueError("--template-scales must contain at least one positive value")
    return scales


def scaled_template(template_path: Path, scale: float) -> np.ndarray:
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"Template image not found or unreadable: {template_path}")
    if scale == 1.0:
        return template
    width = max(1, round(template.shape[1] * scale))
    height = max(1, round(template.shape[0] * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(template, (width, height), interpolation=interpolation)


def best_template_match(
    screen: ScreenCapture,
    template_path: Path,
    monitor: int,
    template_scales: list[float],
    region: dict[str, int] | None = None,
    min_template_dimension: int = 0,
) -> tuple[TemplateMatch, Frame, float]:
    frame = screen.capture(region)
    best: TemplateMatch | None = None
    best_scale = template_scales[0]

    valid_scales = []
    for scale in template_scales:
        template = scaled_template(template_path, scale)
        if template.shape[0] > frame.image.shape[0] or template.shape[1] > frame.image.shape[1]:
            raise ValueError(f"Template is larger than monitor {monitor} at scale {scale:g}: {template_path}")
        if min_template_dimension > 0 and (
            template.shape[0] < min_template_dimension or template.shape[1] < min_template_dimension
        ):
            continue
        valid_scales.append(scale)

    scales_to_check = valid_scales if valid_scales else template_scales
    for scale in scales_to_check:
        template = scaled_template(template_path, scale)
        result = cv2.matchTemplate(frame.image, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        match = TemplateMatch(
            x=frame.left + int(max_loc[0]),
            y=frame.top + int(max_loc[1]),
            width=int(template.shape[1]),
            height=int(template.shape[0]),
            score=float(max_val),
        )
        if best is None or match.score > best.score:
            best = match
            best_scale = scale

    if best is None:
        raise RuntimeError("No template scales were available")
    return best, frame, best_scale


def wait_for_template_match(
    screen: ScreenCapture,
    template_path: Path,
    monitor: int,
    template_scales: list[float],
    threshold: float,
    timeout: float,
    poll_seconds: float,
    stop_keys: StopKeys,
    region: dict[str, int] | None = None,
) -> TemplateSearchResult:
    deadline = time.monotonic() + timeout
    best_seen: TemplateMatch | None = None
    best_seen_scale = template_scales[0]
    while time.monotonic() < deadline and not stop_keys.stop_requested:
        match, _frame, scale = best_template_match(screen, template_path, monitor, template_scales, region=region)
        if best_seen is None or match.score > best_seen.score:
            best_seen = match
            best_seen_scale = scale
        if match.score >= threshold:
            report_progress(f"template:{template_path.name}")
            return TemplateSearchResult(match=match, best_seen=match, scale=scale)
        time.sleep(max(0.01, poll_seconds))
    if best_seen is None:
        best_seen, _frame, best_seen_scale = best_template_match(
            screen,
            template_path,
            monitor,
            template_scales,
            region=region,
        )
    if best_seen.score >= threshold:
        report_progress(f"template:{template_path.name}")
        return TemplateSearchResult(match=best_seen, best_seen=best_seen, scale=best_seen_scale)
    report_failure(f"template:{template_path.name}")
    return TemplateSearchResult(match=None, best_seen=best_seen, scale=best_seen_scale)
