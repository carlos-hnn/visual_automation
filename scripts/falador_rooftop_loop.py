from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import Region, RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import Frame, ScreenCapture
from core.state_machine import AutomationState, StateMachine
from core.vision import TemplateMatch, Vision


ColorName = Literal["green", "red"]


@dataclass(frozen=True)
class OverlayDetection:
    green_hsv_min: tuple[int, int, int] = (35, 55, 45)
    green_hsv_max: tuple[int, int, int] = (95, 255, 255)
    red_hsv_low_min: tuple[int, int, int] = (0, 145, 135)
    red_hsv_low_max: tuple[int, int, int] = (10, 255, 255)
    red_hsv_high_min: tuple[int, int, int] = (170, 145, 135)
    red_hsv_high_max: tuple[int, int, int] = (179, 255, 255)
    hard_green_value_min: int = 115
    hard_green_minus_red_min: int = 45
    hard_green_minus_blue_min: int = 35
    hard_red_value_min: int = 130
    hard_red_minus_green_min: int = 45
    hard_red_minus_blue_min: int = 45
    min_hard_pixels: int = 20
    min_hard_ratio: float = 0.015
    min_area: float = 90.0
    max_area: float = 18000.0
    morph_kernel: int = 3


@dataclass(frozen=True)
class Selection:
    ignore_recent_centers: int = 3
    ignore_recent_radius_pixels: float = 95.0
    prefer: str = "largest"
    ignore_zones: tuple[Region, ...] = ()
    allow_step_resync: bool = True
    allow_global_fallback: bool = False
    anchor_area_weight: float = 0.002


@dataclass(frozen=True)
class MarkScan:
    enabled: bool = True
    scan_after_every_obstacle: bool = True
    scan_when_red_overlay_seen: bool = True
    max_attempts: int = 3
    poll_seconds: float = 0.35


@dataclass(frozen=True)
class Timing:
    after_obstacle_click_seconds: float = 3.2
    after_mark_click_seconds: float = 1.5
    poll_seconds: float = 0.25
    no_candidate_sleep_seconds: float = 0.6


@dataclass(frozen=True)
class OverlayCandidate:
    color: ColorName
    x: int
    y: int
    width: int
    height: int
    area: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


@dataclass(frozen=True)
class CourseStep:
    name: str
    target_region: Region | None = None
    grid_region: str | None = None
    anchor: tuple[int, int] | None = None


GRID_REGIONS = {
    "north-west": (0, 0),
    "north": (1, 0),
    "north-east": (2, 0),
    "west": (0, 1),
    "center": (1, 1),
    "east": (2, 1),
    "south-west": (0, 2),
    "south": (1, 2),
    "south-east": (2, 2),
}


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "falador_rooftop.json"
    ensure_local_config(config_path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def path_from_config(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def runtime_seconds_from_config(config: dict[str, Any]) -> float | None:
    hours = config.get("max_runtime_hours")
    if hours is not None:
        return float(hours) * 60 * 60
    seconds = config.get("max_runtime_seconds", 900)
    if seconds is None:
        return None
    return float(seconds)


def tuple3(raw: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return default
    return (int(raw[0]), int(raw[1]), int(raw[2]))


def build_overlay_detection(config: dict[str, Any]) -> OverlayDetection:
    raw = config.get("overlay_detection", {})
    defaults = OverlayDetection()
    return OverlayDetection(
        green_hsv_min=tuple3(raw.get("green_hsv_min"), defaults.green_hsv_min),
        green_hsv_max=tuple3(raw.get("green_hsv_max"), defaults.green_hsv_max),
        red_hsv_low_min=tuple3(raw.get("red_hsv_low_min"), defaults.red_hsv_low_min),
        red_hsv_low_max=tuple3(raw.get("red_hsv_low_max"), defaults.red_hsv_low_max),
        red_hsv_high_min=tuple3(raw.get("red_hsv_high_min"), defaults.red_hsv_high_min),
        red_hsv_high_max=tuple3(raw.get("red_hsv_high_max"), defaults.red_hsv_high_max),
        hard_green_value_min=int(raw.get("hard_green_value_min", defaults.hard_green_value_min)),
        hard_green_minus_red_min=int(raw.get("hard_green_minus_red_min", defaults.hard_green_minus_red_min)),
        hard_green_minus_blue_min=int(raw.get("hard_green_minus_blue_min", defaults.hard_green_minus_blue_min)),
        hard_red_value_min=int(raw.get("hard_red_value_min", defaults.hard_red_value_min)),
        hard_red_minus_green_min=int(raw.get("hard_red_minus_green_min", defaults.hard_red_minus_green_min)),
        hard_red_minus_blue_min=int(raw.get("hard_red_minus_blue_min", defaults.hard_red_minus_blue_min)),
        min_hard_pixels=int(raw.get("min_hard_pixels", defaults.min_hard_pixels)),
        min_hard_ratio=float(raw.get("min_hard_ratio", defaults.min_hard_ratio)),
        min_area=float(raw.get("min_area", defaults.min_area)),
        max_area=float(raw.get("max_area", defaults.max_area)),
        morph_kernel=int(raw.get("morph_kernel", defaults.morph_kernel)),
    )


def build_selection(config: dict[str, Any]) -> Selection:
    raw = config.get("selection", {})
    ignore_zones = tuple(
        Region.from_mapping(zone)
        for zone in raw.get("ignore_zones", [])
        if isinstance(zone, dict)
    )
    return Selection(
        ignore_recent_centers=int(raw.get("ignore_recent_centers", 3)),
        ignore_recent_radius_pixels=float(raw.get("ignore_recent_radius_pixels", 95.0)),
        prefer=str(raw.get("prefer", "largest")),
        ignore_zones=ignore_zones,
        allow_step_resync=bool(raw.get("allow_step_resync", True)),
        allow_global_fallback=bool(raw.get("allow_global_fallback", False)),
        anchor_area_weight=float(raw.get("anchor_area_weight", 0.002)),
    )


def build_course_steps(config: dict[str, Any]) -> list[CourseStep]:
    course = config.get("course", {})
    raw_steps = course.get("steps")
    if raw_steps is None:
        raw_steps = course.get("obstacles", [])

    steps: list[CourseStep] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if isinstance(raw_step, str):
            steps.append(CourseStep(name=raw_step))
            continue
        if not isinstance(raw_step, dict):
            steps.append(CourseStep(name=f"step {index}"))
            continue

        target_region = None
        if isinstance(raw_step.get("target_region"), dict):
            target_region = Region.from_mapping(raw_step["target_region"])

        anchor = None
        raw_anchor = raw_step.get("anchor")
        if isinstance(raw_anchor, (list, tuple)) and len(raw_anchor) == 2:
            anchor = (int(raw_anchor[0]), int(raw_anchor[1]))

        grid_region = raw_step.get("grid_region")
        steps.append(
            CourseStep(
                name=str(raw_step.get("name", f"step {index}")),
                target_region=target_region,
                grid_region=str(grid_region) if grid_region is not None else None,
                anchor=anchor,
            )
        )
    return steps


def build_mark_scan(config: dict[str, Any]) -> MarkScan:
    raw = config.get("mark_scan", {})
    return MarkScan(
        enabled=bool(raw.get("enabled", True)),
        scan_after_every_obstacle=bool(raw.get("scan_after_every_obstacle", True)),
        scan_when_red_overlay_seen=bool(raw.get("scan_when_red_overlay_seen", True)),
        max_attempts=int(raw.get("max_attempts", 3)),
        poll_seconds=float(raw.get("poll_seconds", 0.35)),
    )


def build_timing(config: dict[str, Any]) -> Timing:
    raw = config.get("timing", {})
    return Timing(
        after_obstacle_click_seconds=float(raw.get("after_obstacle_click_seconds", 3.2)),
        after_mark_click_seconds=float(raw.get("after_mark_click_seconds", 1.5)),
        poll_seconds=float(raw.get("poll_seconds", 0.25)),
        no_candidate_sleep_seconds=float(raw.get("no_candidate_sleep_seconds", 0.6)),
    )


def active_capture_region(config: dict[str, Any], fallback_region: Region, logger: logging.Logger) -> Region:
    app_window = config.get("app_window", {})
    if not bool(app_window.get("enabled", False)):
        return fallback_region

    app_name = str(app_window.get("app_name", "RuneLite"))
    if bool(app_window.get("activate", True)):
        activate_app(app_name)
    bounds = app_window_bounds(app_name)
    if bounds is None:
        logger.warning("could not resolve %s window bounds; using configured fallback region", app_name)
        return fallback_region

    insets = app_window.get("capture_insets", {})
    left = bounds.left + int(insets.get("left", 0))
    top = bounds.top + int(insets.get("top", 0))
    right = int(insets.get("right", 0))
    bottom = int(insets.get("bottom", 0))
    width = max(1, bounds.width - int(insets.get("left", 0)) - right)
    height = max(1, bounds.height - int(insets.get("top", 0)) - bottom)
    return Region(left=left, top=top, width=width, height=height)


def activate_app(app_name: str) -> None:
    script = f'tell application "{app_name}" to activate'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False, timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return


def app_window_bounds(app_name: str) -> Region | None:
    script = f'''
tell application "System Events"
    if not (exists process "{app_name}") then return ""
    tell process "{app_name}"
        if (count of windows) is 0 then return ""
        set windowPosition to position of window 1
        set windowSize to size of window 1
        return (item 1 of windowPosition as text) & "," & (item 2 of windowPosition as text) & "," & (item 1 of windowSize as text) & "," & (item 2 of windowSize as text)
    end tell
end tell
'''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        return None
    try:
        left, top, width, height = [int(float(part.strip())) for part in output.split(",")]
    except ValueError:
        return None
    return Region(left=left, top=top, width=width, height=height)


def build_mask(frame: Frame, color: ColorName, settings: OverlayDetection) -> np.ndarray:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    if color == "green":
        mask = cv2.inRange(hsv, np.array(settings.green_hsv_min), np.array(settings.green_hsv_max))
    else:
        low = cv2.inRange(hsv, np.array(settings.red_hsv_low_min), np.array(settings.red_hsv_low_max))
        high = cv2.inRange(hsv, np.array(settings.red_hsv_high_min), np.array(settings.red_hsv_high_max))
        mask = cv2.bitwise_or(low, high)

    kernel_size = max(1, settings.morph_kernel)
    if kernel_size > 1:
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def find_overlay_candidates(frame: Frame, color: ColorName, settings: OverlayDetection) -> list[OverlayCandidate]:
    mask = build_mask(frame, color, settings)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[OverlayCandidate] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < settings.min_area or area > settings.max_area:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        if width < 8 or height < 8:
            continue
        crop = frame.image[y : y + height, x : x + width]
        hard_pixels, hard_ratio = hard_overlay_score(crop, color, settings)
        if hard_pixels < settings.min_hard_pixels or hard_ratio < settings.min_hard_ratio:
            continue
        candidates.append(
            OverlayCandidate(
                color=color,
                x=frame.left + int(x),
                y=frame.top + int(y),
                width=int(width),
                height=int(height),
                area=area,
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.area, reverse=True)


def hard_overlay_score(crop: np.ndarray, color: ColorName, settings: OverlayDetection) -> tuple[int, float]:
    blue, green, red = cv2.split(crop)
    red_i = red.astype(np.int16)
    green_i = green.astype(np.int16)
    blue_i = blue.astype(np.int16)
    if color == "green":
        hard = (
            (green_i >= settings.hard_green_value_min)
            & ((green_i - red_i) >= settings.hard_green_minus_red_min)
            & ((green_i - blue_i) >= settings.hard_green_minus_blue_min)
        )
    else:
        hard = (
            (red_i >= settings.hard_red_value_min)
            & ((red_i - green_i) >= settings.hard_red_minus_green_min)
            & ((red_i - blue_i) >= settings.hard_red_minus_blue_min)
        )

    hard_pixels = int(hard.sum())
    total_pixels = max(1, int(crop.shape[0] * crop.shape[1]))
    return hard_pixels, float(hard_pixels / total_pixels)


def distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


def filter_recent_candidates(
    candidates: list[OverlayCandidate],
    recent_centers: list[tuple[int, int]],
    selection: Selection,
    logger: logging.Logger,
) -> list[OverlayCandidate]:
    if not recent_centers:
        return candidates

    filtered: list[OverlayCandidate] = []
    ignored = 0
    active_recent = recent_centers[-selection.ignore_recent_centers :]
    for candidate in candidates:
        nearest_recent = min(distance(candidate.center, center) for center in active_recent)
        if nearest_recent <= selection.ignore_recent_radius_pixels:
            ignored += 1
            logger.debug(
                "ignored recent %s overlay center=%s area=%.1f nearest_recent=%.1f",
                candidate.color,
                candidate.center,
                candidate.area,
                nearest_recent,
            )
            continue
        filtered.append(candidate)

    if ignored:
        logger.info("ignored %s recently clicked overlay candidate(s)", ignored)
    return filtered


def filter_ignored_zone_candidates(
    candidates: list[OverlayCandidate],
    region: Region,
    selection: Selection,
    logger: logging.Logger,
) -> list[OverlayCandidate]:
    if not selection.ignore_zones:
        return candidates

    filtered: list[OverlayCandidate] = []
    ignored = 0
    for candidate in candidates:
        relative_x = candidate.center[0] - region.left
        relative_y = candidate.center[1] - region.top
        if any(
            zone.left <= relative_x <= zone.left + zone.width
            and zone.top <= relative_y <= zone.top + zone.height
            for zone in selection.ignore_zones
        ):
            ignored += 1
            logger.debug("ignored UI-zone %s overlay center=%s", candidate.color, candidate.center)
            continue
        filtered.append(candidate)

    if ignored:
        logger.info("ignored %s overlay candidate(s) inside UI zones", ignored)
    return filtered


def choose_candidate(candidates: list[OverlayCandidate], region: Region, selection: Selection) -> OverlayCandidate | None:
    if not candidates:
        return None
    if selection.prefer == "nearest_center":
        center = (region.left + region.width // 2, region.top + region.height // 2)
        return min(candidates, key=lambda candidate: distance(candidate.center, center))
    return max(candidates, key=lambda candidate: candidate.area)


def candidate_in_relative_region(candidate: OverlayCandidate, base_region: Region, relative_region: Region) -> bool:
    relative_x = candidate.center[0] - base_region.left
    relative_y = candidate.center[1] - base_region.top
    return (
        relative_region.left <= relative_x <= relative_region.left + relative_region.width
        and relative_region.top <= relative_y <= relative_region.top + relative_region.height
    )


def grid_region_for_name(name: str, base_region: Region) -> Region | None:
    normalized = name.strip().lower().replace("_", "-")
    cell = GRID_REGIONS.get(normalized)
    if cell is None:
        return None
    column, row = cell
    left_edges = [0, base_region.width // 3, (base_region.width * 2) // 3, base_region.width]
    top_edges = [0, base_region.height // 3, (base_region.height * 2) // 3, base_region.height]
    return Region(
        left=left_edges[column],
        top=top_edges[row],
        width=left_edges[column + 1] - left_edges[column],
        height=top_edges[row + 1] - top_edges[row],
    )


def step_target_region(step: CourseStep, game_region: Region, logger: logging.Logger) -> Region | None:
    if step.target_region is not None:
        return step.target_region
    if step.grid_region is None:
        return None
    region = grid_region_for_name(step.grid_region, game_region)
    if region is None:
        logger.warning("unknown grid_region=%s for step=%s", step.grid_region, step.name)
    return region


def choose_step_candidate(
    candidates: list[OverlayCandidate],
    game_region: Region,
    steps: list[CourseStep],
    step_index: int,
    selection: Selection,
    logger: logging.Logger,
) -> tuple[OverlayCandidate | None, int]:
    if not candidates:
        return None, step_index
    if not steps:
        return choose_candidate(candidates, game_region, selection), step_index

    expected_index = step_index % len(steps)
    expected_step = steps[expected_index]
    expected = candidates_for_step(candidates, game_region, expected_step, logger)
    if expected:
        return choose_anchored_candidate(expected, game_region, expected_step, selection), expected_index

    if selection.allow_step_resync:
        for offset in range(1, len(steps)):
            candidate_index = (expected_index + offset) % len(steps)
            step = steps[candidate_index]
            matches = candidates_for_step(candidates, game_region, step, logger)
            if matches:
                logger.warning(
                    "resynced course step: expected=%s actual=%s candidates=%s",
                    expected_step.name,
                    step.name,
                    len(matches),
                )
                return choose_anchored_candidate(matches, game_region, step, selection), candidate_index

    if selection.allow_global_fallback:
        logger.warning("using global overlay fallback for step=%s candidates=%s", expected_step.name, len(candidates))
        return choose_candidate(candidates, game_region, selection), expected_index

    logger.info(
        "no candidate inside expected step region: step=%s candidates=%s rel_centers=%s",
        expected_step.name,
        len(candidates),
        format_relative_candidates(candidates, game_region),
    )
    return None, expected_index


def candidates_for_step(
    candidates: list[OverlayCandidate],
    game_region: Region,
    step: CourseStep,
    logger: logging.Logger,
) -> list[OverlayCandidate]:
    target_region = step_target_region(step, game_region, logger)
    if target_region is None:
        return candidates
    return [candidate for candidate in candidates if candidate_in_relative_region(candidate, game_region, target_region)]


def choose_anchored_candidate(
    candidates: list[OverlayCandidate],
    game_region: Region,
    step: CourseStep,
    selection: Selection,
) -> OverlayCandidate:
    if step.anchor is None:
        return choose_candidate(candidates, game_region, selection) or candidates[0]

    anchor = (game_region.left + step.anchor[0], game_region.top + step.anchor[1])
    return min(candidates, key=lambda candidate: distance(candidate.center, anchor) - candidate.area * selection.anchor_area_weight)


def format_relative_candidates(candidates: list[OverlayCandidate], game_region: Region, limit: int = 8) -> list[dict[str, int]]:
    return [
        {
            "x": candidate.center[0] - game_region.left,
            "y": candidate.center[1] - game_region.top,
            "area": int(candidate.area),
        }
        for candidate in candidates[:limit]
    ]


def click_overlay_or_log(
    label: str,
    candidate: OverlayCandidate,
    mouse: MouseController,
    dry_run: bool,
    logger: logging.Logger,
) -> tuple[int, int]:
    x, y = candidate.center
    if dry_run:
        mouse.move_to(x, y)
        logger.info(
            "dry-run would click %s center=%s color=%s area=%.1f box=(%s,%s,%s,%s)",
            label,
            candidate.center,
            candidate.color,
            candidate.area,
            candidate.x,
            candidate.y,
            candidate.width,
            candidate.height,
        )
        return candidate.center

    clicked = mouse.click_point(x, y)
    logger.info("clicked %s at=%s color=%s area=%.1f", label, clicked, candidate.color, candidate.area)
    return clicked


def click_match_or_log(label: str, match: TemplateMatch, mouse: MouseController, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        mouse.move_to(*match.center)
        logger.info("dry-run would click %s center=%s score=%.3f", label, match.center, match.score)
        return
    clicked = mouse.click_match(match)
    logger.info("clicked %s at=%s score=%.3f", label, clicked, match.score)


def load_existing_templates(paths: list[Path], logger: logging.Logger) -> list[Path]:
    existing: list[Path] = []
    for path in paths:
        if path.exists():
            existing.append(path)
        else:
            logger.warning("missing optional mark template: %s", path)
    return existing


def find_best_mark(
    vision: Vision,
    templates: list[Path],
    region: Region,
    threshold: float,
) -> TemplateMatch | None:
    matches: list[TemplateMatch] = []
    for template in templates:
        matches.extend(vision.find_all_templates(template, region=region, threshold=threshold))
    if not matches:
        return None
    return max(matches, key=lambda match: match.score)


def scan_and_pick_mark(
    vision: Vision,
    mouse: MouseController,
    templates: list[Path],
    region: Region,
    threshold: float,
    mark_scan: MarkScan,
    timing: Timing,
    dry_run: bool,
    logger: logging.Logger,
    safety: SafetyController,
) -> bool:
    if not mark_scan.enabled:
        return False
    if not templates:
        logger.info("mark scan skipped; no mark templates are available yet")
        return False

    for attempt in range(1, mark_scan.max_attempts + 1):
        if safety.should_stop():
            return False
        mark = find_best_mark(vision, templates, region, threshold)
        if mark is not None:
            logger.info("mark of grace found attempt=%s center=%s score=%.3f", attempt, mark.center, mark.score)
            click_match_or_log("mark of grace", mark, mouse, dry_run, logger)
            time.sleep(timing.after_mark_click_seconds)
            return True
        logger.info("mark of grace not found attempt=%s/%s", attempt, mark_scan.max_attempts)
        time.sleep(mark_scan.poll_seconds)
    return False


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    thresholds = config.get("thresholds", {})
    default_threshold = float(config.get("threshold", 0.85))
    overlay_detection = build_overlay_detection(config)
    selection = build_selection(config)
    mark_scan = build_mark_scan(config)
    timing = build_timing(config)
    course_steps = build_course_steps(config)

    configured_mark_templates = [path_from_config(path) for path in config.get("templates", {}).get("mark_of_grace", [])]

    regions = RegionManager(ROOT / "config" / "regions.json")
    fallback_game_region = regions.get_region(config["regions"]["game_view"])

    mouse_settings = config.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.04)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.10)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.04)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 5)),
            point_tolerance_pixels=int(mouse_settings.get("point_tolerance_pixels", 6)),
        )
    )

    safety_settings = config.get("safety", {})
    safety = SafetyController(
        logger,
        SafetyConfig(
            max_runtime_seconds=runtime_seconds_from_config(config),
            stop_hotkey=safety_settings.get("stop_hotkey", "cmd+shift+q"),
            enable_esc_stop=bool(safety_settings.get("enable_esc_stop", True)),
            failsafe_corner_pixels=int(safety_settings.get("failsafe_corner_pixels", 5)),
        ),
    )

    recent_centers: list[tuple[int, int]] = []
    step_index = 0

    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        vision = Vision(screen=screen, debug_enabled=bool(config.get("debug")), debug_dir=ROOT / config.get("debug_dir", "logs/debug"))
        mark_templates = load_existing_templates(configured_mark_templates, logger)
        safety.start()
        state.transition_to(AutomationState.RUNNING, "falador rooftop workflow started")
        try:
            while not safety.should_stop():
                game_region = active_capture_region(config, fallback_game_region, logger)
                frame = screen.capture(game_region)
                green_candidates = find_overlay_candidates(frame, "green", overlay_detection)
                red_candidates = find_overlay_candidates(frame, "red", overlay_detection)
                green_candidates = filter_ignored_zone_candidates(green_candidates, game_region, selection, logger)
                red_candidates = filter_ignored_zone_candidates(red_candidates, game_region, selection, logger)
                fresh_green_candidates = filter_recent_candidates(green_candidates, recent_centers, selection, logger)
                fresh_red_candidates = filter_recent_candidates(red_candidates, recent_centers, selection, logger)

                red_seen = bool(red_candidates or fresh_red_candidates)
                if red_seen:
                    logger.info("red agility overlay seen; checking for mark of grace")
                    scan_and_pick_mark(
                        vision,
                        mouse,
                        mark_templates,
                        game_region,
                        float(thresholds.get("mark_of_grace", default_threshold)),
                        mark_scan,
                        timing,
                        dry_run,
                        logger,
                        safety,
                    )
                    game_region = active_capture_region(config, fallback_game_region, logger)
                    frame = screen.capture(game_region)
                    green_candidates = find_overlay_candidates(frame, "green", overlay_detection)
                    green_candidates = filter_ignored_zone_candidates(green_candidates, game_region, selection, logger)
                    fresh_green_candidates = filter_recent_candidates(green_candidates, recent_centers, selection, logger)

                obstacle, chosen_step_index = choose_step_candidate(
                    fresh_green_candidates,
                    game_region,
                    course_steps,
                    step_index,
                    selection,
                    logger,
                )
                if obstacle is None:
                    logger.info(
                        "no fresh green obstacle found; step=%s green=%s red=%s recent=%s",
                        course_steps[step_index % len(course_steps)].name if course_steps else f"step {step_index + 1}",
                        len(green_candidates),
                        len(red_candidates),
                        recent_centers[-selection.ignore_recent_centers :],
                    )
                    time.sleep(timing.no_candidate_sleep_seconds)
                    continue

                step_index = chosen_step_index
                obstacle_name = course_steps[step_index % len(course_steps)].name if course_steps else f"step {step_index + 1}"
                clicked_center = click_overlay_or_log(obstacle_name, obstacle, mouse, dry_run, logger)
                recent_centers.append(clicked_center)
                recent_centers = recent_centers[-max(1, selection.ignore_recent_centers) :]
                step_index += 1

                time.sleep(timing.after_obstacle_click_seconds)
                if mark_scan.scan_after_every_obstacle or (mark_scan.scan_when_red_overlay_seen and red_seen):
                    game_region = active_capture_region(config, fallback_game_region, logger)
                    post_frame = screen.capture(game_region)
                    post_red = find_overlay_candidates(post_frame, "red", overlay_detection)
                    post_red = filter_ignored_zone_candidates(post_red, game_region, selection, logger)
                    if post_red:
                        logger.info("post-obstacle red overlay candidate(s)=%s", len(post_red))
                    scan_and_pick_mark(
                        vision,
                        mouse,
                        mark_templates,
                        game_region,
                        float(thresholds.get("mark_of_grace", default_threshold)),
                        mark_scan,
                        timing,
                        dry_run,
                        logger,
                        safety,
                    )
                time.sleep(timing.poll_seconds)

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("falador rooftop workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "falador rooftop workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
