from __future__ import annotations

import json
import logging
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import Region, RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import ScreenCapture
from core.state_machine import AutomationState, StateMachine
from core.vision import TemplateMatch, Vision


@dataclass(frozen=True)
class Timing:
    after_rock_click_seconds: float = 2.0
    status_poll_seconds: float = 0.8
    drop_tick_seconds: float = 0.02
    drop_tick_jitter_seconds: float = 0.005
    after_drop_inventory_check_seconds: float = 0.1


@dataclass(frozen=True)
class RockColorFilter:
    enabled: bool = True
    mask_saturation_min: int = 35
    mask_value_max: int = 150
    candidate_hue_min: int = 0
    candidate_hue_max: int = 28
    candidate_saturation_min: int = 35
    candidate_value_max: int = 160
    min_brown_ratio: float = 0.55
    max_gray_ratio: float = 0.20
    gray_saturation_max: int = 25
    min_mask_pixels: int = 50


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "mining.json"
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


def build_timing(config: dict[str, Any]) -> Timing:
    raw = config.get("timing", {})
    return Timing(
        after_rock_click_seconds=float(raw.get("after_rock_click_seconds", 2.0)),
        status_poll_seconds=float(raw.get("status_poll_seconds", 0.8)),
        drop_tick_seconds=float(raw.get("drop_tick_seconds", 0.02)),
        drop_tick_jitter_seconds=float(raw.get("drop_tick_jitter_seconds", 0.005)),
        after_drop_inventory_check_seconds=float(raw.get("after_drop_inventory_check_seconds", 0.1)),
    )


def build_rock_color_filter(config: dict[str, Any]) -> RockColorFilter:
    raw = config.get("rock_color_filter", {})
    return RockColorFilter(
        enabled=bool(raw.get("enabled", True)),
        mask_saturation_min=int(raw.get("mask_saturation_min", 35)),
        mask_value_max=int(raw.get("mask_value_max", 150)),
        candidate_hue_min=int(raw.get("candidate_hue_min", 0)),
        candidate_hue_max=int(raw.get("candidate_hue_max", 28)),
        candidate_saturation_min=int(raw.get("candidate_saturation_min", 35)),
        candidate_value_max=int(raw.get("candidate_value_max", 160)),
        min_brown_ratio=float(raw.get("min_brown_ratio", 0.55)),
        max_gray_ratio=float(raw.get("max_gray_ratio", 0.20)),
        gray_saturation_max=int(raw.get("gray_saturation_max", 25)),
        min_mask_pixels=int(raw.get("min_mask_pixels", 50)),
    )


def detect_status(screen: ScreenCapture, region: Region, config: dict[str, Any]) -> str:
    frame = screen.capture(region)
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (45, 80, 80), (85, 255, 255))
    red_low = cv2.inRange(hsv, (0, 80, 80), (12, 255, 255))
    red_high = cv2.inRange(hsv, (170, 80, 80), (179, 255, 255))
    green_pixels = int((green > 0).sum())
    red_pixels = int(((red_low > 0) | (red_high > 0)).sum())

    thresholds = config.get("status_detection", {})
    green_min = int(thresholds.get("green_min_pixels", 12))
    red_min = int(thresholds.get("red_min_pixels", 12))
    if green_pixels >= green_min and green_pixels > red_pixels:
        return "mining"
    if red_pixels >= red_min and red_pixels > green_pixels:
        return "not_mining"
    return "unknown"


def nearest_to_center(matches: list[TemplateMatch], region: Region) -> TemplateMatch | None:
    if not matches:
        return None
    center = (region.left + region.width // 2, region.top + region.height // 2)
    return min(matches, key=lambda match: (match.center[0] - center[0]) ** 2 + (match.center[1] - center[1]) ** 2)


def deduplicate_matches(matches: list[TemplateMatch]) -> list[TemplateMatch]:
    matches = sorted(matches, key=lambda item: item.score, reverse=True)
    kept: list[TemplateMatch] = []
    for match in matches:
        min_distance = min(match.width, match.height) / 2
        if all(float(np.hypot(match.center[0] - existing.center[0], match.center[1] - existing.center[1])) > min_distance for existing in kept):
            kept.append(match)
    return kept


def rock_is_ready_brown(
    frame_image: np.ndarray,
    frame_left: int,
    frame_top: int,
    template_image: np.ndarray,
    match: TemplateMatch,
    color_filter: RockColorFilter,
) -> tuple[bool, float, float]:
    if not color_filter.enabled:
        return True, 1.0, 0.0

    rel_x = match.x - frame_left
    rel_y = match.y - frame_top
    crop = frame_image[rel_y : rel_y + match.height, rel_x : rel_x + match.width]
    if crop.shape[:2] != template_image.shape[:2]:
        return False, 0.0, 1.0

    template_hsv = cv2.cvtColor(template_image, cv2.COLOR_BGR2HSV)
    crop_hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    rock_mask = (template_hsv[:, :, 1] >= color_filter.mask_saturation_min) & (template_hsv[:, :, 2] <= color_filter.mask_value_max)
    mask_pixels = int(rock_mask.sum())
    if mask_pixels < color_filter.min_mask_pixels:
        return True, 1.0, 0.0

    brown_pixels = (
        (crop_hsv[:, :, 0] >= color_filter.candidate_hue_min)
        & (crop_hsv[:, :, 0] <= color_filter.candidate_hue_max)
        & (crop_hsv[:, :, 1] >= color_filter.candidate_saturation_min)
        & (crop_hsv[:, :, 2] <= color_filter.candidate_value_max)
        & rock_mask
    )
    gray_pixels = (crop_hsv[:, :, 1] <= color_filter.gray_saturation_max) & rock_mask

    brown_ratio = float(brown_pixels.sum() / mask_pixels)
    gray_ratio = float(gray_pixels.sum() / mask_pixels)
    return brown_ratio >= color_filter.min_brown_ratio and gray_ratio <= color_filter.max_gray_ratio, brown_ratio, gray_ratio


def find_ready_iron_rocks(
    screen: ScreenCapture,
    templates: list[Path],
    region: Region,
    threshold: float,
    color_filter: RockColorFilter,
    logger: logging.Logger,
) -> list[TemplateMatch]:
    frame = screen.capture(region)
    matches: list[TemplateMatch] = []
    for template_path in templates:
        template_image = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if template_image is None:
            raise FileNotFoundError(f"Template image not found or unreadable: {template_path}")
        if template_image.shape[0] > frame.image.shape[0] or template_image.shape[1] > frame.image.shape[1]:
            logger.warning("iron rock template is larger than game region: %s", template_path)
            continue

        result = cv2.matchTemplate(frame.image, template_image, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        raw_matches = [
            TemplateMatch(
                x=frame.left + int(x),
                y=frame.top + int(y),
                width=int(template_image.shape[1]),
                height=int(template_image.shape[0]),
                score=float(result[y, x]),
            )
            for y, x in zip(*locations)
        ]
        for match in deduplicate_matches(raw_matches):
            is_ready, brown_ratio, gray_ratio = rock_is_ready_brown(
                frame.image,
                frame.left,
                frame.top,
                template_image,
                match,
                color_filter,
            )
            if is_ready:
                matches.append(match)
            else:
                logger.debug(
                    "discarded non-brown rock template=%s center=%s score=%.3f brown_ratio=%.3f gray_ratio=%.3f",
                    template_path.name,
                    match.center,
                    match.score,
                    brown_ratio,
                    gray_ratio,
                )
    return deduplicate_matches(matches)


def click_match_or_log(label: str, match: TemplateMatch, mouse: MouseController, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        mouse.move_to(*match.center)
        logger.info("dry-run would click %s center=%s score=%.3f", label, match.center, match.score)
        return
    clicked = mouse.click_match(match)
    logger.info("clicked %s at=%s score=%.3f", label, clicked, match.score)


def inventory_has_empty_slot(vision: Vision, template: Path, region: Region, threshold: float) -> bool:
    return vision.exists(template, region=region, threshold=threshold)


def drop_inventory_ores(
    vision: Vision,
    mouse: MouseController,
    ore_template: Path,
    inventory_region: Region,
    threshold: float,
    timing: Timing,
    dry_run: bool,
    logger: logging.Logger,
    safety: SafetyController,
) -> int:
    dropped = 0
    while not safety.should_stop():
        matches = vision.find_all_templates(ore_template, region=inventory_region, threshold=threshold)
        matches = sorted(matches, key=lambda match: (match.y, match.x))
        if not matches:
            logger.info("no ore templates found to drop; dropped=%s", dropped)
            return dropped

        for match in matches:
            if safety.should_stop():
                return dropped
            click_match_or_log("ore", match, mouse, dry_run, logger)
            dropped += 1
            delay = max(0.01, timing.drop_tick_seconds + random.uniform(-timing.drop_tick_jitter_seconds, timing.drop_tick_jitter_seconds))
            time.sleep(delay)
        if dry_run:
            logger.info("dry-run ore drop pass complete; dropped=%s", dropped)
            return dropped
    return dropped


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    thresholds = config.get("thresholds", {})
    default_threshold = float(config.get("threshold", 0.85))
    timing = build_timing(config)
    rock_color_filter = build_rock_color_filter(config)

    templates = config["templates"]
    iron_rock_templates = [path_from_config(path) for path in templates["iron_rocks"]]
    empty_slot_template = path_from_config(templates["empty_slot"])
    ore_template = path_from_config(templates["drop_ore"])
    missing = [path for path in [*iron_rock_templates, empty_slot_template, ore_template] if not path.exists()]
    if missing:
        for path in missing:
            logger.error("missing template: %s", path)
        return 1

    regions = RegionManager(ROOT / "config" / "regions.json")
    region_names = config["regions"]
    game_region = regions.get_region(region_names["game_view"])
    status_region = regions.get_region(region_names["status"])
    inventory_region = regions.get_region(region_names["inventory"])
    last_slot_region = regions.get_region(region_names["last_slot"])

    mouse_settings = config.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.04)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.09)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.02)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 4)),
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

    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        vision = Vision(screen=screen, debug_enabled=bool(config.get("debug")), debug_dir=ROOT / config.get("debug_dir", "logs/debug"))
        safety.start()
        state.transition_to(AutomationState.RUNNING, "mining workflow started")
        try:
            while not safety.should_stop():
                status = detect_status(screen, status_region, config)
                logger.info("status=%s", status)
                if status == "mining":
                    time.sleep(timing.status_poll_seconds)
                    continue

                has_empty_slot = inventory_has_empty_slot(
                    vision,
                    empty_slot_template,
                    last_slot_region,
                    float(thresholds.get("empty_slot", default_threshold)),
                )
                inventory_full = not has_empty_slot
                logger.info("inventory_full=%s", inventory_full)

                if inventory_full:
                    logger.info("step: drop inventory ores")
                    dropped = drop_inventory_ores(
                        vision,
                        mouse,
                        ore_template,
                        inventory_region,
                        float(thresholds.get("drop_ore", default_threshold)),
                        timing,
                        dry_run,
                        logger,
                        safety,
                    )
                    time.sleep(timing.after_drop_inventory_check_seconds)
                    if dropped > 0:
                        continue
                    logger.info("inventory looked full, but no ores were found; continuing to rock search")

                logger.info("step: find nearest iron rock")
                matches = find_ready_iron_rocks(
                    screen,
                    iron_rock_templates,
                    game_region,
                    float(thresholds.get("iron_rock", default_threshold)),
                    rock_color_filter,
                    logger,
                )
                rock = nearest_to_center(matches, game_region)
                if rock is None:
                    logger.warning("iron rock not found")
                    time.sleep(timing.status_poll_seconds)
                    continue

                click_match_or_log("iron rock", rock, mouse, dry_run, logger)
                time.sleep(timing.after_rock_click_seconds)

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("mining workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "mining workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
