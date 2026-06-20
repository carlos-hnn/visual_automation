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
    after_fire_click_seconds: float = 0.8
    after_confirm_seconds: float = 1.0
    inventory_poll_seconds: float = 1.0
    missing_inventory_confirmations: int = 3
    bank_open_timeout_seconds: float = 8.0
    after_bank_click_seconds: float = 1.0
    after_deposit_seconds: float = 0.4
    after_withdraw_seconds: float = 0.8
    after_inventory_refill_seconds: float = 0.4
    inventory_refill_timeout_seconds: float = 5.0


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "cooking_swordfish.json"
    ensure_local_config(config_path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def path_from_config(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def region_from_name(regions: RegionManager, name: str | None) -> Region | None:
    return regions.get_region(name) if name else None


def build_timing(config: dict[str, Any]) -> Timing:
    raw = config.get("timing", {})
    return Timing(
        after_fire_click_seconds=float(raw.get("after_fire_click_seconds", 0.8)),
        after_confirm_seconds=float(raw.get("after_confirm_seconds", 1.0)),
        inventory_poll_seconds=float(raw.get("inventory_poll_seconds", 1.0)),
        missing_inventory_confirmations=int(raw.get("missing_inventory_confirmations", 3)),
        bank_open_timeout_seconds=float(raw.get("bank_open_timeout_seconds", 8.0)),
        after_bank_click_seconds=float(raw.get("after_bank_click_seconds", 1.0)),
        after_deposit_seconds=float(raw.get("after_deposit_seconds", 0.4)),
        after_withdraw_seconds=float(raw.get("after_withdraw_seconds", 0.8)),
        after_inventory_refill_seconds=float(raw.get("after_inventory_refill_seconds", 0.4)),
        inventory_refill_timeout_seconds=float(raw.get("inventory_refill_timeout_seconds", 5.0)),
    )


def runtime_seconds_from_config(config: dict[str, Any]) -> float | None:
    hours = config.get("max_runtime_hours")
    if hours is not None:
        return float(hours) * 60 * 60

    seconds = config.get("max_runtime_seconds", 900)
    if seconds is None:
        return None
    return float(seconds)


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


def click_match_or_log(
    label: str,
    match: TemplateMatch,
    mouse: MouseController,
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    if dry_run:
        mouse.move_to(*match.center)
        logger.info("dry-run would click %s center=%s score=%.3f", label, match.center, match.score)
        return

    clicked = mouse.click_match(match)
    logger.info("clicked %s at=%s score=%.3f", label, clicked, match.score)


def click_point_or_log(
    label: str,
    point: dict[str, Any],
    mouse: MouseController,
    dry_run: bool,
    logger: logging.Logger,
) -> None:
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


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    threshold = float(config.get("threshold", 0.85))
    thresholds = config.get("thresholds", {})
    loop_delay = float(config.get("loop_delay_seconds", 0.35))
    timing = build_timing(config)
    templates = config["templates"]
    points = config.get("points", {})

    fire_template = path_from_config(templates["fire"]) if not points.get("fire") else None
    raw_inventory_template = path_from_config(templates["raw_swordfish_inventory"])
    bank_npc_template = path_from_config(templates["bank_npc"]) if not points.get("bank_npc") else None
    deposit_template = path_from_config(templates["deposit_inventory"])
    raw_bank_template = path_from_config(templates["raw_swordfish_bank"])

    missing_templates = [
        path
        for path in (fire_template, raw_inventory_template, bank_npc_template, deposit_template, raw_bank_template)
        if path is not None and not path.exists()
    ]
    if missing_templates:
        for path in missing_templates:
            logger.error("missing template: %s", path)
        return 1

    region_manager = RegionManager(ROOT / "config" / "regions.json")
    region_names = config.get("regions", {})
    fire_region = region_from_name(region_manager, region_names.get("fire"))
    inventory_region = region_from_name(region_manager, region_names.get("inventory"))
    bank_npc_region = region_from_name(region_manager, region_names.get("bank_npc"))
    bank_interface_region = region_from_name(region_manager, region_names.get("bank_interface"))

    mouse_settings = config.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.08)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.25)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.08)),
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
    max_runtime_seconds = runtime_seconds_from_config(config)
    safety = SafetyController(
        logger,
        SafetyConfig(
            max_runtime_seconds=max_runtime_seconds,
            stop_hotkey=safety_settings.get("stop_hotkey", "cmd+shift+q"),
            enable_esc_stop=bool(safety_settings.get("enable_esc_stop", True)),
            failsafe_corner_pixels=int(safety_settings.get("failsafe_corner_pixels", 5)),
        ),
    )
    logger.info("max runtime seconds: %s", max_runtime_seconds)

    confirm_key = config.get("input", {}).get("confirm_key", "space")

    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        vision = Vision(screen=screen, debug_enabled=bool(config.get("debug")), debug_dir=ROOT / config.get("debug_dir", "logs/debug"))
        safety.start()
        state.transition_to(AutomationState.RUNNING, "cooking swordfish workflow started")

        try:
            while not safety.should_stop():
                logger.info("step: find fire")
                if points.get("fire"):
                    click_point_or_log("fire", points["fire"], mouse, dry_run, logger)
                else:
                    fire = wait_for_match(
                        vision,
                        safety,
                        fire_template,
                        timeout=10,
                        region=fire_region,
                        threshold=float(thresholds.get("fire", threshold)),
                        poll_interval=loop_delay,
                    )
                    if fire is None:
                        logger.warning("fire template not found; retrying")
                        time.sleep(loop_delay)
                        continue
                    click_match_or_log("fire", fire, mouse, dry_run, logger)
                time.sleep(timing.after_fire_click_seconds)

                press_or_log(keyboard, confirm_key, dry_run, logger)
                time.sleep(timing.after_confirm_seconds)

                logger.info("step: monitor inventory until raw swordfish disappears")
                missing_count = 0
                while not safety.should_stop():
                    raw_match = vision.find_template(
                        raw_inventory_template,
                        region=inventory_region,
                        threshold=float(thresholds.get("raw_swordfish_inventory", threshold)),
                    )
                    if raw_match is not None:
                        missing_count = 0
                        logger.debug("raw swordfish still present center=%s score=%.3f", raw_match.center, raw_match.score)
                    else:
                        missing_count += 1
                        logger.info("raw swordfish missing confirmation %s/%s", missing_count, timing.missing_inventory_confirmations)
                        if missing_count >= timing.missing_inventory_confirmations:
                            break
                    time.sleep(timing.inventory_poll_seconds)

                if safety.should_stop():
                    break

                logger.info("step: find bank npc")
                if points.get("bank_npc"):
                    click_point_or_log("bank npc", points["bank_npc"], mouse, dry_run, logger)
                else:
                    bank_npc = wait_for_match(
                        vision,
                        safety,
                        bank_npc_template,
                        timeout=10,
                        region=bank_npc_region,
                        threshold=float(thresholds.get("bank_npc", threshold)),
                        poll_interval=loop_delay,
                    )
                    if bank_npc is None:
                        logger.warning("bank npc template not found; restarting cycle")
                        continue
                    click_match_or_log("bank npc", bank_npc, mouse, dry_run, logger)
                time.sleep(timing.after_bank_click_seconds)

                logger.info("step: find deposit inventory control")
                deposit = wait_for_match(
                    vision,
                    safety,
                    deposit_template,
                    timeout=timing.bank_open_timeout_seconds,
                    region=bank_interface_region,
                    threshold=float(thresholds.get("deposit_inventory", threshold)),
                    poll_interval=loop_delay,
                )
                if deposit is None:
                    logger.warning("deposit inventory template not found; restarting cycle")
                    continue
                click_match_or_log("deposit inventory", deposit, mouse, dry_run, logger)
                time.sleep(timing.after_deposit_seconds)

                logger.info("step: withdraw raw swordfish")
                raw_bank = wait_for_match(
                    vision,
                    safety,
                    raw_bank_template,
                    timeout=timing.bank_open_timeout_seconds,
                    region=bank_interface_region,
                    threshold=float(thresholds.get("raw_swordfish_bank", threshold)),
                    poll_interval=loop_delay,
                )
                if raw_bank is None:
                    logger.warning("raw swordfish bank template not found; restarting cycle")
                    continue
                click_match_or_log("raw swordfish in bank", raw_bank, mouse, dry_run, logger)
                time.sleep(timing.after_withdraw_seconds)

                logger.info("step: validate inventory refill")
                refilled = wait_for_match(
                    vision,
                    safety,
                    raw_inventory_template,
                    timeout=timing.inventory_refill_timeout_seconds,
                    region=inventory_region,
                    threshold=float(thresholds.get("raw_swordfish_inventory", threshold)),
                    poll_interval=loop_delay,
                )
                if refilled is None:
                    logger.warning("raw swordfish not visible in inventory after withdraw; restarting cycle")
                    continue
                logger.info("inventory refilled center=%s score=%.3f", refilled.center, refilled.score)
                time.sleep(timing.after_inventory_refill_seconds)

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("cooking swordfish workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "cooking swordfish workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
