from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay
from visual_automation.actions.templates import TemplateActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import (
    best_color_marker,
    marker_click_point,
    marker_settings_from_config,
)
from visual_automation.game_states.inventory import detect_inventory_grid_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, resolve_platform
from visual_automation.template_config import find_window_bounds, threshold_for

install_timestamped_print()

SCRIPT_NAME = "cleaning_herbs"
DEFAULT_CONFIG_PATH = ROOT / "config" / "cleaning_herbs.example.json"


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
    after_herb_click_ticks: float = 0.05
    herb_time_jitter: float = 0.015
    herb_spot_jitter: int = 3
    tick_seconds: float = 0.6
    time_jitter: float = 0.06
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    dry_run: bool = True


DEFAULTS = Defaults()


def relative_region(window: dict[str, int], raw: dict[str, Any]) -> dict[str, int]:
    left, top = int(raw["left"]), int(raw["top"])
    return {
        "left": window["left"] + left,
        "top": window["top"] + top,
        "width": min(int(raw["width"]), max(1, window["width"] - left)),
        "height": min(int(raw["height"]), max(1, window["height"] - top)),
    }


def make_template(name: str, path: Path, args, config, region) -> TemplateState:
    return TemplateState(
        name, path, threshold_for(config, name, args.threshold), tuple(args.template_scales), region, (0, 0)
    )


def wait_until(label: str, predicate: Callable[[], bool], args, stop_keys: StopKeys) -> bool:
    deadline = time.monotonic() + args.state_timeout
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        if predicate():
            print(f"{label}: confirmed")
            return True
        print(f"{label}: waiting")
        time.sleep(args.poll_seconds)
    print(f"{label}: timed out")
    return False


def click_color_marker(label, screen, region, settings, mouse, args, stop_keys: StopKeys) -> bool:
    deadline = time.monotonic() + args.state_timeout
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        marker = best_color_marker(screen.capture(region), settings)
        if marker is not None:
            point = marker_click_point(marker, args.click_scale, args.spot_jitter)
            print(f"{label}: marker at {marker.center}; clicking {point}")
            if not args.dry_run:
                mouse.click(*point)
            return True
        print(f"{label}: marker not found; retrying")
        time.sleep(args.retry_seconds)
    return False


def click_first_bank_item(window, mouse, args) -> None:
    x, y = window["left"] + args.first_item_x, window["top"] + args.first_item_y
    print(f"bank first item: clicking once near ({x}, {y})")
    if not args.dry_run:
        mouse.click_point(x, y, tolerance_pixels=args.spot_jitter)


def click_all_herbs(region, mouse, args, stop_keys: StopKeys) -> bool:
    for row in range(7):
        for column in range(4):
            if stop_keys.stop_requested:
                return False
            x = region["left"] + round(args.inventory_first_x + column * args.inventory_column_spacing)
            y = region["top"] + round(args.inventory_first_y + row * args.inventory_row_spacing)
            number = row * 4 + column + 1
            print(f"herb {number}/28: clicking inventory slot near ({x}, {y})")
            if not args.dry_run:
                mouse.click_point(x, y, tolerance_pixels=args.herb_spot_jitter)
            delay = humanized_delay(args.after_herb_click_ticks * args.tick_seconds, args.herb_time_jitter)
            print(f"after herb click: waiting {delay:.3f}s")
            if not args.dry_run:
                time.sleep(delay)
    return True


def build_runtime(args, config):
    window = find_window_bounds(str(value_from_config(config, "window_title", "RuneLite")))
    if window is None:
        raise ValueError("RuneLite window not found")
    raw = value_from_config(config, "regions", {})
    if not isinstance(raw, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("game", "bank_world", "bank", "inventory"):
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"Missing region: {name}")
    regions = {
        name: relative_region(window, raw[name])
        for name in ("game", "bank_world", "bank", "inventory")
    }
    templates_dir = ROOT / str(value_from_config(config, "templates_dir", "templates/steel_cannonball"))
    templates = {
        name: make_template(name, templates_dir / f"{name}.png", args, config, regions["bank"])
        for name in ("deposit_all", "bank_close")
    }
    missing = [item.path for item in templates.values() if not item.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    return window, regions, templates


def run_calibration(args, config) -> int:
    window, regions, templates = build_runtime(args, config)
    stop_keys = StopKeys()
    with ScreenCapture(args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        bank_match, bank_score, _ = state.find(templates["bank_close"], 0.0)
        _deposit_match, deposit_score, _ = state.find(templates["deposit_all"], 0.0)
        slots = detect_inventory_grid_status(
            screen,
            regions["inventory"],
            args.empty_slots_required,
            (args.inventory_first_x, args.inventory_first_y),
            (args.inventory_column_spacing, args.inventory_row_spacing),
            patch_radius=args.inventory_patch_radius,
            occupied_std_threshold=args.inventory_occupied_std,
        )
        bank_marker = best_color_marker(
            screen.capture(regions["bank_world"]), marker_settings_from_config(config, "bank_marker")
        )
        print(f"window: {window}")
        print(
            f"bank open: {bank_match is not None}; bank_close score={bank_score:.3f}; "
            f"deposit_all score={deposit_score:.3f}"
        )
        print(f"inventory: empty_slots={slots.empty_slots}; empty_required={slots.empty_required}")
        print(f"bank marker: {bank_marker.center if bank_marker else 'not visible'}")
    return 0


def run_flow(args, config) -> int:
    window, regions, templates = build_runtime(args, config)
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: Cleaning Herbs")
    print(f"RuneLite window: {window}; stop with Esc or Cmd+Shift+Q")
    time.sleep(max(0.0, args.countdown))
    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    completed = 0
    try:
        with ScreenCapture(args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            clicks = TemplateActions(state, mouse, args, args.dry_run)
            bank_settings = marker_settings_from_config(config, "bank_marker")

            def bank_open() -> bool:
                return state.exists(templates["bank_close"], 0.0)

            def inventory_status():
                result = detect_inventory_grid_status(
                    screen,
                    regions["inventory"],
                    args.empty_slots_required,
                    (args.inventory_first_x, args.inventory_first_y),
                    (args.inventory_column_spacing, args.inventory_row_spacing),
                    patch_radius=args.inventory_patch_radius,
                    occupied_std_threshold=args.inventory_occupied_std,
                )
                print(f"inventory: {result.empty_slots} empty slot(s) visible")
                return result

            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                print(f"Cleaning Herbs loop {completed + 1}")
                if bank_open():
                    print("bank open: already confirmed")
                else:
                    if not click_color_marker(
                        "bank", screen, regions["bank_world"], bank_settings, mouse, args, stop_keys
                    ):
                        return 1
                    if not wait_until("bank open", bank_open, args, stop_keys):
                        return 1
                if not clicks.find_and_click(templates["deposit_all"]):
                    return 1
                if not wait_until("inventory empty", lambda: inventory_status().is_empty, args, stop_keys):
                    return 1
                click_first_bank_item(window, mouse, args)
                if not wait_until("inventory full", lambda: inventory_status().is_full, args, stop_keys):
                    return 1
                if not clicks.find_and_click(templates["bank_close"]):
                    return 1
                if not wait_until("bank closed", lambda: not bank_open(), args, stop_keys):
                    return 1
                if not click_all_herbs(regions["inventory"], mouse, args, stop_keys):
                    return 1
                completed += 1
                print(f"Cleaning Herbs load complete: {completed}")
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
    parser = argparse.ArgumentParser(description="Bank and clean full inventories of tagged grimy herbs.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    for name, kind, default in (
        ("monitor", int, DEFAULTS.monitor), ("poll-seconds", float, DEFAULTS.poll_seconds),
        ("retry-seconds", float, DEFAULTS.retry_seconds), ("state-timeout", float, DEFAULTS.state_timeout),
        ("threshold", float, DEFAULTS.threshold), ("click-timeout", float, DEFAULTS.click_timeout),
        ("countdown", float, DEFAULTS.countdown), ("empty-slots-required", int, DEFAULTS.empty_slots_required),
        ("inventory-first-x", int, DEFAULTS.inventory_first_x),
        ("inventory-first-y", int, DEFAULTS.inventory_first_y),
        ("inventory-column-spacing", float, DEFAULTS.inventory_column_spacing),
        ("inventory-row-spacing", float, DEFAULTS.inventory_row_spacing),
        ("inventory-patch-radius", int, DEFAULTS.inventory_patch_radius),
        ("inventory-occupied-std", float, DEFAULTS.inventory_occupied_std),
        ("first-item-x", int, DEFAULTS.first_item_x), ("first-item-y", int, DEFAULTS.first_item_y),
        ("after-herb-click-ticks", float, DEFAULTS.after_herb_click_ticks),
        ("herb-time-jitter", float, DEFAULTS.herb_time_jitter),
        ("herb-spot-jitter", int, DEFAULTS.herb_spot_jitter),
        ("tick-seconds", float, DEFAULTS.tick_seconds), ("time-jitter", float, DEFAULTS.time_jitter),
        ("pre-click-jitter", float, DEFAULTS.pre_click_jitter), ("spot-jitter", int, DEFAULTS.spot_jitter),
        ("click-scale", float, DEFAULTS.click_scale), ("move-duration-min", float, DEFAULTS.move_duration_min),
        ("move-duration-max", float, DEFAULTS.move_duration_max), ("loops", int, 0),
    ):
        key = name.replace("-", "_")
        parser.add_argument(f"--{name}", type=kind, default=value_from_config(config, key, default))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--calibrate", action="store_true", help="Inspect bank, inventory, and markers.")
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.template_scales = parse_scales(str(args.template_scales))
        if args.empty_slots_required < 1:
            raise ValueError("empty_slots_required must be at least 1")
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
