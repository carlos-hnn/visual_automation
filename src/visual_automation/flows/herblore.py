from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay
from visual_automation.actions.templates import TemplateActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.keyboard import KeyboardController
from visual_automation.core.safety import report_progress
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import (
    best_color_marker,
    capture_color_markers,
    marker_click_point,
    marker_settings_from_config,
)
from visual_automation.game_states.inventory import detect_inventory_grid_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState
from visual_automation.platforming import add_platform_argument, resolve_platform
from visual_automation.runtime import (
    click_color_marker,
    make_template,
    relative_region,
    wait_for_state,
)
from visual_automation.template_config import find_window_bounds

install_timestamped_print()

SCRIPT_NAME = "herblore"
DEFAULT_CONFIG_PATH = ROOT / "config" / "herblore.example.json"


@dataclass(frozen=True)
class Defaults:
    monitor: int = 1
    poll_seconds: float = 0.12
    retry_seconds: float = 1.0
    state_timeout: float = 120.0
    threshold: float = 0.82
    click_timeout: float = 3.0
    template_scales: str = "0.5"
    countdown: float = 2.0
    empty_slots_required: int = 28
    inventory_first_x: int = 40
    inventory_first_y: int = 31
    inventory_column_spacing: float = 44.0
    inventory_row_spacing: float = 38.5
    inventory_patch_radius: int = 13
    inventory_occupied_std: float = 8.0
    first_item_x: int = 106
    first_item_y: int = 129
    second_item_x: int = 151
    second_item_y: int = 129
    deposit_hover_x: int = 484
    deposit_hover_y: int = 851
    inventory_park_x: int = 450
    inventory_park_y: int = 500
    between_bank_items_seconds: float = 0.15
    chat_prompt_timeout: float = 8.0
    chat_poll_seconds: float = 0.12
    chat_change_threshold: float = 4.0
    tick_seconds: float = 0.6
    production_wait_ticks: float = 25.0
    completion_poll_seconds: float = 1.0
    time_jitter: float = 0.06
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    dry_run: bool = True


DEFAULTS = Defaults()


def region_change_score(before: np.ndarray, after: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(before, after)))


def ingredient_markers_finished(green_count: int, red_count: int) -> bool:
    return green_count == 0 and red_count == 0


def wait_for_chat_prompt(screen, region, before: np.ndarray, args, stop_keys: StopKeys) -> bool:
    if args.dry_run:
        print("chat prompt: would wait for a visual change")
        return True
    deadline = time.monotonic() + args.chat_prompt_timeout
    best_score = 0.0
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        score = region_change_score(before, screen.capture(region).image)
        best_score = max(best_score, score)
        if score >= args.chat_change_threshold:
            print(f"chat prompt: confirmed change score={score:.2f}")
            return True
        print(f"chat prompt: waiting; change score={score:.2f}")
        time.sleep(args.chat_poll_seconds)
    print(f"chat prompt: timed out; best change score={best_score:.2f}")
    return False


def wait_for_ingredients_finished(predicate, args, stop_keys: StopKeys) -> bool:
    deadline = time.monotonic() + args.state_timeout
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        if predicate():
            print("all ingredients consumed: confirmed")
            return True
        print("all ingredients consumed: waiting")
        time.sleep(args.completion_poll_seconds)
    print("all ingredients consumed: timed out")
    return False


def click_bank_items(window, mouse, args) -> None:
    for number, (relative_x, relative_y) in enumerate(
        ((args.first_item_x, args.first_item_y), (args.second_item_x, args.second_item_y)), 1
    ):
        x, y = window["left"] + relative_x, window["top"] + relative_y
        print(f"bank item {number}/2: clicking once near ({x}, {y})")
        if not args.dry_run:
            mouse.click_point(x, y, tolerance_pixels=args.spot_jitter)
            if number == 1:
                delay = max(0.0, args.between_bank_items_seconds + random.uniform(-0.04, 0.04))
                time.sleep(delay)


def click_inventory_marker(label, screen, region, settings, mouse, args) -> bool:
    marker = best_color_marker(screen.capture(region), settings)
    if marker is None:
        print(f"{label}: marker not found")
        return False
    point = marker_click_point(marker, args.click_scale, args.spot_jitter)
    print(f"{label}: marker at {marker.center}; clicking {point}")
    if not args.dry_run:
        if args.pre_click_jitter > 0:
            time.sleep(random.uniform(0.0, args.pre_click_jitter))
        mouse.click(*point)
    return True


def build_runtime(args, config):
    window = find_window_bounds(str(value_from_config(config, "window_title", "RuneLite")))
    if window is None:
        raise ValueError("RuneLite window not found")
    raw = value_from_config(config, "regions", {})
    if not isinstance(raw, dict):
        raise ValueError("config regions must be a mapping")
    required = ("game", "bank_world", "bank", "inventory", "chat")
    for name in required:
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"Missing region: {name}")
    regions = {name: relative_region(window, raw[name]) for name in required}
    templates_dir = ROOT / str(value_from_config(config, "templates_dir", "templates/shared/bank"))
    templates = {
        name: make_template(name, templates_dir / f"{name}.png", args, config, regions["bank"])
        for name in ("deposit_all", "bank_close")
    }
    missing = [item.path for item in templates.values() if not item.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    return window, regions, templates


def inventory_status(screen, region, args):
    return detect_inventory_grid_status(
        screen,
        region,
        args.empty_slots_required,
        (args.inventory_first_x, args.inventory_first_y),
        (args.inventory_column_spacing, args.inventory_row_spacing),
        patch_radius=args.inventory_patch_radius,
        occupied_std_threshold=args.inventory_occupied_std,
    )


def run_calibration(args, config) -> int:
    window, regions, templates = build_runtime(args, config)
    stop_keys = StopKeys()
    with ScreenCapture(args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        close_match, close_score, close_scale = state.find(templates["bank_close"], 0.0)
        _deposit_match, deposit_score, deposit_scale = state.find(templates["deposit_all"], 0.0)
        slots = inventory_status(screen, regions["inventory"], args)
        bank_marker = best_color_marker(
            screen.capture(regions["bank_world"]), marker_settings_from_config(config, "bank_marker")
        )
        green = capture_color_markers(
            screen, regions["inventory"], marker_settings_from_config(config, "green_marker")
        )
        red = capture_color_markers(
            screen, regions["inventory"], marker_settings_from_config(config, "red_marker")
        )
        print(f"window: {window}")
        print(
            f"bank open: {close_match is not None}; bank_close={close_score:.3f}@{close_scale:g}; "
            f"deposit_all={deposit_score:.3f}@{deposit_scale:g}"
        )
        print(f"inventory: empty_slots={slots.empty_slots}; green={len(green)}; red={len(red)}")
        print(f"bank marker: {bank_marker.center if bank_marker else 'not visible'}")
    return 0


def run_flow(args, config) -> int:
    window, regions, templates = build_runtime(args, config)
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: Herblore")
    print(f"RuneLite window: {window}; stop with Esc or Cmd+Shift+Q")
    time.sleep(max(0.0, args.countdown))
    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    keyboard = KeyboardController()
    stop_keys.start()
    completed = 0
    try:
        with ScreenCapture(args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            clicks = TemplateActions(state, mouse, args, args.dry_run)
            bank_settings = marker_settings_from_config(config, "bank_marker")
            green_settings = marker_settings_from_config(config, "green_marker")
            red_settings = marker_settings_from_config(config, "red_marker")

            def bank_open() -> bool:
                return state.exists(templates["bank_close"], 0.0)

            def bank_close_ready() -> bool:
                return state.exists(templates["bank_close"], 0.0)

            def ensure_bank_open() -> bool:
                if bank_open():
                    print("bank open: already confirmed")
                    return True
                if not click_color_marker(
                    "bank", screen, regions["bank_world"], bank_settings, mouse, args, stop_keys
                ):
                    return False
                if not args.dry_run:
                    hover_point = (
                        window["left"] + args.deposit_hover_x,
                        window["top"] + args.deposit_hover_y,
                    )
                    mouse.move_to(*hover_point)
                    print(f"bank opening: cursor pre-positioned near Deposit All at {hover_point}")
                return wait_for_state("bank open", bank_open, args, stop_keys)

            def empty() -> bool:
                status = inventory_status(screen, regions["inventory"], args)
                print(f"inventory: {status.empty_slots} empty slot(s)")
                return status.is_empty

            def full() -> bool:
                status = inventory_status(screen, regions["inventory"], args)
                print(f"inventory: {status.empty_slots} empty slot(s)")
                return status.is_full

            def ingredients_finished() -> bool:
                green = capture_color_markers(screen, regions["inventory"], green_settings)
                red = capture_color_markers(screen, regions["inventory"], red_settings)
                print(f"mixing: green={len(green)}, red={len(red)}")
                # Mixing is complete only after both tagged reagents disappear.
                return ingredient_markers_finished(len(green), len(red))

            def ingredients_ready() -> bool:
                green = capture_color_markers(screen, regions["inventory"], green_settings)
                red = capture_color_markers(screen, regions["inventory"], red_settings)
                print(f"ingredients ready: green={len(green)}, red={len(red)}")
                return bool(green and red)

            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                print(f"Herblore loop {completed + 1}")
                if not ensure_bank_open():
                    return 1
                if not clicks.find_and_click(templates["deposit_all"]):
                    return 1
                if not wait_for_state("inventory empty", empty, args, stop_keys):
                    return 1
                click_bank_items(window, mouse, args)
                if not wait_for_state("inventory full", full, args, stop_keys):
                    return 1
                if not wait_for_state("bank close available", bank_close_ready, args, stop_keys):
                    return 1
                if not clicks.find_and_click(templates["bank_close"]):
                    return 1
                if not wait_for_state("bank closed", lambda: not bank_open(), args, stop_keys):
                    return 1
                # bank_close being absent is the expected closed-bank state; do not
                # leave the template watchdog armed during the production wait.
                report_progress("template:bank_close")

                if not wait_for_state("green and red markers available", ingredients_ready, args, stop_keys):
                    return 1

                before_chat = screen.capture(regions["chat"]).image
                if not click_inventory_marker("green item", screen, regions["inventory"], green_settings, mouse, args):
                    return 1
                if not click_inventory_marker("red item", screen, regions["inventory"], red_settings, mouse, args):
                    return 1
                if not args.dry_run:
                    park_point = (
                        window["left"] + args.inventory_park_x,
                        window["top"] + args.inventory_park_y,
                    )
                    mouse.move_to(*park_point)
                    print(f"inventory monitoring: cursor parked outside inventory at {park_point}")
                if not wait_for_chat_prompt(screen, regions["chat"], before_chat, args, stop_keys):
                    return 1
                if args.dry_run:
                    print("mix prompt: would press Space")
                else:
                    keyboard.press("space")
                    print("mix prompt: Space pressed")
                production_delay = humanized_delay(
                    args.production_wait_ticks * args.tick_seconds, args.time_jitter
                )
                print(
                    f"production: waiting {args.production_wait_ticks:g} ticks "
                    f"({production_delay:.2f}s) before validation"
                )
                if not args.dry_run:
                    time.sleep(production_delay)
                if not wait_for_ingredients_finished(ingredients_finished, args, stop_keys):
                    return 1
                completed += 1
                print(f"Herblore round complete: {completed}")
                if args.loops > 0 and completed >= args.loops:
                    break
                if not ensure_bank_open():
                    return 1
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    known, _ = pre.parse_known_args()
    try:
        config = load_json_config(known.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1
    parser = argparse.ArgumentParser(description="Bank two ingredients and mix them until their tags disappear.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    for name, kind, default in (
        ("monitor", int, DEFAULTS.monitor), ("poll-seconds", float, DEFAULTS.poll_seconds),
        ("retry-seconds", float, DEFAULTS.retry_seconds), ("state-timeout", float, DEFAULTS.state_timeout),
        ("threshold", float, DEFAULTS.threshold), ("click-timeout", float, DEFAULTS.click_timeout),
        ("countdown", float, DEFAULTS.countdown), ("empty-slots-required", int, DEFAULTS.empty_slots_required),
        ("inventory-first-x", int, DEFAULTS.inventory_first_x), ("inventory-first-y", int, DEFAULTS.inventory_first_y),
        ("inventory-column-spacing", float, DEFAULTS.inventory_column_spacing),
        ("inventory-row-spacing", float, DEFAULTS.inventory_row_spacing),
        ("inventory-patch-radius", int, DEFAULTS.inventory_patch_radius),
        ("inventory-occupied-std", float, DEFAULTS.inventory_occupied_std),
        ("first-item-x", int, DEFAULTS.first_item_x), ("first-item-y", int, DEFAULTS.first_item_y),
        ("second-item-x", int, DEFAULTS.second_item_x), ("second-item-y", int, DEFAULTS.second_item_y),
        ("deposit-hover-x", int, DEFAULTS.deposit_hover_x),
        ("deposit-hover-y", int, DEFAULTS.deposit_hover_y),
        ("inventory-park-x", int, DEFAULTS.inventory_park_x),
        ("inventory-park-y", int, DEFAULTS.inventory_park_y),
        ("between-bank-items-seconds", float, DEFAULTS.between_bank_items_seconds),
        ("chat-prompt-timeout", float, DEFAULTS.chat_prompt_timeout),
        ("chat-poll-seconds", float, DEFAULTS.chat_poll_seconds),
        ("chat-change-threshold", float, DEFAULTS.chat_change_threshold),
        ("tick-seconds", float, DEFAULTS.tick_seconds),
        ("production-wait-ticks", float, DEFAULTS.production_wait_ticks),
        ("completion-poll-seconds", float, DEFAULTS.completion_poll_seconds),
        ("time-jitter", float, DEFAULTS.time_jitter), ("pre-click-jitter", float, DEFAULTS.pre_click_jitter),
        ("spot-jitter", int, DEFAULTS.spot_jitter), ("click-scale", float, DEFAULTS.click_scale),
        ("move-duration-min", float, DEFAULTS.move_duration_min),
        ("move-duration-max", float, DEFAULTS.move_duration_max), ("loops", int, 0),
    ):
        key = name.replace("-", "_")
        parser.add_argument(f"--{name}", type=kind, default=value_from_config(config, key, default))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--calibrate", action="store_true", help="Inspect bank, inventory, chat, and markers.")
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.template_scales = parse_scales(str(args.template_scales))
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
