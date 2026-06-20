from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.keyboard import KeyboardConfig, KeyboardController
from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import Region, RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import ScreenCapture
from core.state_machine import AutomationState, StateMachine
from core.vision import TemplateMatch, Vision


@dataclass(frozen=True)
class Timing:
    idle_poll_seconds: float = 0.8
    first_action_check_seconds: float = 5.0
    logs_per_inventory: int = 27
    ticks_per_log: float = 6.0
    tick_seconds: float = 0.6
    checkpoint_decay_ratio: float = 1 / 3
    after_chest_click_seconds: float = 1.0
    bank_log_timeout_seconds: float = 5.0
    after_withdraw_seconds: float = 0.6
    inventory_refill_timeout_seconds: float = 5.0
    after_fire_click_seconds: float = 1.2
    after_confirm_status_wait_seconds: float = 1.2
    confirm_retry_if_status_missing: bool = True


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "firemaking.json"
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
        idle_poll_seconds=float(raw.get("idle_poll_seconds", 0.8)),
        first_action_check_seconds=float(raw.get("first_action_check_seconds", 5.0)),
        logs_per_inventory=int(raw.get("logs_per_inventory", 27)),
        ticks_per_log=float(raw.get("ticks_per_log", 6.0)),
        tick_seconds=float(raw.get("tick_seconds", 0.6)),
        checkpoint_decay_ratio=float(raw.get("checkpoint_decay_ratio", 1 / 3)),
        after_chest_click_seconds=float(raw.get("after_chest_click_seconds", 1.0)),
        bank_log_timeout_seconds=float(raw.get("bank_log_timeout_seconds", 5.0)),
        after_withdraw_seconds=float(raw.get("after_withdraw_seconds", 0.6)),
        inventory_refill_timeout_seconds=float(raw.get("inventory_refill_timeout_seconds", 5.0)),
        after_fire_click_seconds=float(raw.get("after_fire_click_seconds", raw.get("after_fire_click_tick_seconds", 1.2))),
        after_confirm_status_wait_seconds=float(raw.get("after_confirm_status_wait_seconds", raw.get("after_confirm_seconds", 1.2))),
        confirm_retry_if_status_missing=bool(raw.get("confirm_retry_if_status_missing", True)),
    )


def build_action_checkpoints(timing: Timing) -> list[float]:
    total_expected_seconds = timing.logs_per_inventory * timing.ticks_per_log * timing.tick_seconds
    checkpoints = [
        timing.first_action_check_seconds,
        total_expected_seconds / 2,
    ]

    next_delay = checkpoints[-1] * timing.checkpoint_decay_ratio
    while next_delay > timing.idle_poll_seconds:
        checkpoints.append(next_delay)
        next_delay *= timing.checkpoint_decay_ratio
    checkpoints.append(timing.idle_poll_seconds)
    return [max(timing.idle_poll_seconds, delay) for delay in checkpoints]


def next_status_delay(checkpoints: list[float], index: int, fallback: float) -> tuple[float, int]:
    if index >= len(checkpoints):
        return fallback, index
    return checkpoints[index], index + 1


def wait_for_match(
    vision: Vision,
    safety: SafetyController,
    template_path: Path,
    timeout: float,
    region: Region | None,
    threshold: float,
    poll_interval: float,
) -> TemplateMatch | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not safety.should_stop():
        match = vision.find_template(template_path, region=region, threshold=threshold)
        if match is not None:
            return match
        time.sleep(poll_interval)
    return None


def click_match_or_log(label: str, match: TemplateMatch, mouse: MouseController, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        mouse.move_to(*match.center)
        logger.info("dry-run would click %s center=%s score=%.3f", label, match.center, match.score)
        return
    clicked = mouse.click_match(match)
    logger.info("clicked %s at=%s score=%.3f", label, clicked, match.score)


def click_point_or_log(label: str, point: dict[str, Any], mouse: MouseController, dry_run: bool, logger: logging.Logger) -> None:
    x = int(point["x"])
    y = int(point["y"])
    tolerance = point.get("tolerance_pixels")
    if dry_run:
        target = mouse.point_near(x, y, tolerance_pixels=tolerance)
        mouse.move_to(*target)
        logger.info("dry-run would click %s point=(%s, %s) target=%s", label, x, y, target)
        return
    clicked = mouse.click_point(x, y, tolerance_pixels=tolerance)
    logger.info("clicked %s point=(%s, %s) target=%s", label, x, y, clicked)


def press_or_log(keyboard: KeyboardController, key: str, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        logger.info("dry-run would press key=%s", key)
        return
    keyboard.press(key)
    logger.info("pressed key=%s", key)


def status_is_active(vision: Vision, template_path: Path, region: Region, threshold: float) -> bool:
    return vision.find_template(template_path, region=region, threshold=threshold) is not None


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    threshold = float(config.get("threshold", 0.85))
    thresholds = config.get("thresholds", {})
    loop_delay = float(config.get("loop_delay_seconds", 0.35))
    timing = build_timing(config)
    action_checkpoints = build_action_checkpoints(timing)

    templates = config["templates"]
    logs_status_template = path_from_config(templates["logs_status"])
    bank_chest_template = path_from_config(templates["bank_chest"])
    wood_log_template = path_from_config(templates["wood_log"])
    for path in (logs_status_template, bank_chest_template, wood_log_template):
        if not path.exists():
            logger.error("missing template: %s", path)
            return 1

    region_manager = RegionManager(ROOT / "config" / "regions.json")
    region_names = config["regions"]
    status_region = region_manager.get_region(region_names["status"])
    chest_region = region_manager.get_region(region_names["bank_chest"])
    bank_region = region_manager.get_region(region_names["bank_interface"])
    last_slot_region = region_manager.get_region(region_names["last_slot"])

    mouse_settings = config.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.08)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.20)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.06)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 4)),
            point_tolerance_pixels=int(mouse_settings.get("point_tolerance_pixels", 6)),
        )
    )

    keyboard_settings = config.get("keyboard", {})
    keyboard = KeyboardController(
        KeyboardConfig(
            press_pause_min=float(keyboard_settings.get("press_pause_min", 0.05)),
            press_pause_max=float(keyboard_settings.get("press_pause_max", 0.12)),
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

    confirm_key = config.get("input", {}).get("confirm_key", "space")
    points = config.get("points", {})
    fire_point = points["fire"]
    bank_log_point = points.get("bank_log")

    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        vision = Vision(screen=screen, debug_enabled=bool(config.get("debug")), debug_dir=ROOT / config.get("debug_dir", "logs/debug"))
        safety.start()
        state.transition_to(AutomationState.RUNNING, "firemaking workflow started")

        try:
            checkpoint_index = len(action_checkpoints)
            while not safety.should_stop():
                status_match = vision.find_template(
                    logs_status_template,
                    region=status_region,
                    threshold=float(thresholds.get("logs_status", threshold)),
                )
                if status_match is not None:
                    sleep_seconds, checkpoint_index = next_status_delay(action_checkpoints, checkpoint_index, timing.idle_poll_seconds)
                    logger.info(
                        "logs status active center=%s score=%.3f; next_check_seconds=%.1f",
                        status_match.center,
                        status_match.score,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue

                logger.info("logs status missing; refilling inventory")
                checkpoint_index = len(action_checkpoints)
                chest = wait_for_match(
                    vision,
                    safety,
                    bank_chest_template,
                    timeout=3.0,
                    region=chest_region,
                    threshold=float(thresholds.get("bank_chest", threshold)),
                    poll_interval=loop_delay,
                )
                if chest is None:
                    logger.warning("bank chest not found")
                    time.sleep(loop_delay)
                    continue
                click_match_or_log("bank chest", chest, mouse, dry_run, logger)
                time.sleep(timing.after_chest_click_seconds)

                if bank_log_point:
                    click_point_or_log("bank wood log", bank_log_point, mouse, dry_run, logger)
                else:
                    bank_log = wait_for_match(
                        vision,
                        safety,
                        wood_log_template,
                        timeout=timing.bank_log_timeout_seconds,
                        region=bank_region,
                        threshold=float(thresholds.get("bank_log", threshold)),
                        poll_interval=loop_delay,
                    )
                    if bank_log is None:
                        logger.warning("wood log not found in bank")
                        time.sleep(loop_delay)
                        continue
                    click_match_or_log("bank wood log", bank_log, mouse, dry_run, logger)
                time.sleep(timing.after_withdraw_seconds)

                inventory_log = wait_for_match(
                    vision,
                    safety,
                    wood_log_template,
                    timeout=timing.inventory_refill_timeout_seconds,
                    region=last_slot_region,
                    threshold=float(thresholds.get("inventory_log", threshold)),
                    poll_interval=loop_delay,
                )
                if inventory_log is None:
                    logger.warning("last inventory slot did not refill with logs")
                    time.sleep(loop_delay)
                    continue

                click_point_or_log("fire", fire_point, mouse, dry_run, logger)
                time.sleep(timing.after_fire_click_seconds)
                press_or_log(keyboard, confirm_key, dry_run, logger)
                time.sleep(timing.after_confirm_status_wait_seconds)

                if timing.confirm_retry_if_status_missing and not status_is_active(
                    vision,
                    logs_status_template,
                    status_region,
                    float(thresholds.get("logs_status", threshold)),
                ):
                    logger.info("logs status not visible after confirm; retrying key=%s", confirm_key)
                    press_or_log(keyboard, confirm_key, dry_run, logger)
                    time.sleep(timing.after_confirm_status_wait_seconds)

                checkpoint_index = 0

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("firemaking workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "firemaking workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
