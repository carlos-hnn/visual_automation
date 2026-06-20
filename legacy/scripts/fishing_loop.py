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
    after_spot_click_seconds: float = 5.0
    status_poll_seconds: float = 1.0
    drop_tick_seconds: float = 0.02
    drop_tick_jitter_seconds: float = 0.005
    after_drop_inventory_check_seconds: float = 0.1


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "fishing.json"
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
        after_spot_click_seconds=float(raw.get("after_spot_click_seconds", 5.0)),
        status_poll_seconds=float(raw.get("status_poll_seconds", 1.0)),
        drop_tick_seconds=float(raw.get("drop_tick_seconds", 0.02)),
        drop_tick_jitter_seconds=float(raw.get("drop_tick_jitter_seconds", 0.005)),
        after_drop_inventory_check_seconds=float(raw.get("after_drop_inventory_check_seconds", 0.1)),
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
        return "fishing"
    if red_pixels >= red_min and red_pixels > green_pixels:
        return "not_fishing"
    return "unknown"


def nearest_to_center(matches: list[TemplateMatch], region: Region) -> TemplateMatch | None:
    if not matches:
        return None
    center = (region.left + region.width // 2, region.top + region.height // 2)
    return min(matches, key=lambda match: (match.center[0] - center[0]) ** 2 + (match.center[1] - center[1]) ** 2)


def click_match_or_log(label: str, match: TemplateMatch, mouse: MouseController, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        mouse.move_to(*match.center)
        logger.info("dry-run would click %s center=%s score=%.3f", label, match.center, match.score)
        return
    clicked = mouse.click_match(match)
    logger.info("clicked %s at=%s score=%.3f", label, clicked, match.score)


def inventory_has_empty_slot(vision: Vision, template: Path, region: Region, threshold: float) -> bool:
    return vision.exists(template, region=region, threshold=threshold)


def wait_for_inventory_refill(
    vision: Vision,
    template: Path,
    region: Region,
    threshold: float,
    timeout: float,
    poll: float,
    safety: SafetyController,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not safety.should_stop():
        if vision.exists(template, region=region, threshold=threshold):
            return True
        time.sleep(poll)
    return False


def drop_inventory_fish(
    vision: Vision,
    mouse: MouseController,
    fish_templates: list[Path],
    inventory_region: Region,
    threshold: float,
    timing: Timing,
    dry_run: bool,
    logger: logging.Logger,
    safety: SafetyController,
) -> None:
    dropped = 0
    while not safety.should_stop():
        matches: list[TemplateMatch] = []
        for template in fish_templates:
            matches.extend(vision.find_all_templates(template, region=inventory_region, threshold=threshold))
        matches = sorted(matches, key=lambda match: (match.y, match.x))
        if not matches:
            logger.info("no fish templates found to drop; dropped=%s", dropped)
            return

        for match in matches:
            if safety.should_stop():
                return
            click_match_or_log("fish", match, mouse, dry_run, logger)
            dropped += 1
            delay = max(0.05, timing.drop_tick_seconds + random.uniform(-timing.drop_tick_jitter_seconds, timing.drop_tick_jitter_seconds))
            time.sleep(delay)


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    thresholds = config.get("thresholds", {})
    default_threshold = float(config.get("threshold", 0.85))
    timing = build_timing(config)

    templates = config["templates"]
    fishing_spot_template = path_from_config(templates["fishing_spot"])
    empty_slot_template = path_from_config(templates["empty_slot"])
    fish_templates = [path_from_config(path) for path in templates["drop_fish"]]
    missing = [path for path in [fishing_spot_template, empty_slot_template, *fish_templates] if not path.exists()]
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
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.25)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.55)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.12)),
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
        state.transition_to(AutomationState.RUNNING, "fishing workflow started")
        try:
            while not safety.should_stop():
                status = detect_status(screen, status_region, config)
                logger.info("status=%s", status)
                if status == "fishing":
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
                    logger.info("step: drop inventory fish")
                    drop_inventory_fish(
                        vision,
                        mouse,
                        fish_templates,
                        inventory_region,
                        float(thresholds.get("drop_fish", default_threshold)),
                        timing,
                        dry_run,
                        logger,
                        safety,
                    )
                    time.sleep(timing.after_drop_inventory_check_seconds)
                    continue

                logger.info("step: find nearest fishing spot")
                matches = vision.find_all_templates(
                    fishing_spot_template,
                    region=game_region,
                    threshold=float(thresholds.get("fishing_spot", default_threshold)),
                )
                spot = nearest_to_center(matches, game_region)
                if spot is None:
                    logger.warning("fishing spot not found")
                    time.sleep(timing.status_poll_seconds)
                    continue

                click_match_or_log("fishing spot", spot, mouse, dry_run, logger)
                time.sleep(timing.after_spot_click_seconds)

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("fishing workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "fishing workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
