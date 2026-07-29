from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyautogui

from visual_automation.actions import StopKeys, build_mouse, match_click_coordinates
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import Frame, ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.actions.timing import wait_ticks
from visual_automation.flows.woodcutting import click_marker, nearest_to_center, region_center
from visual_automation.game_states.bank import detect_bank_status
from visual_automation.game_states.color_markers import (
    find_color_markers,
    marker_settings_from_config,
)
from visual_automation.game_states.inventory import detect_inventory_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, resolve_path, resolve_platform
from visual_automation.template_config import click_offset_for, region_for, resolve_regions, scales_for, threshold_for

install_timestamped_print()

DEFAULT_CONFIG_PATH = ROOT / "config" / "wc_fossil.example.json"
TEMPLATE_NAMES = ("woodcutting_status", "empty_inventory_slot", "deposit_all", "bank_close")


def wait_seconds(label: str, seconds: float, args, dry_run: bool) -> None:
    delay = max(0.0, seconds)
    print(f"{label}: waiting {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def red_fill_fraction(frame: Frame, marker, config: dict[str, Any]) -> tuple[int, float]:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    saturation = int(value_from_config(config, "tree_reject_red_min_saturation", 120))
    value = int(value_from_config(config, "tree_reject_red_min_value", 80))
    low = cv2.inRange(hsv, np.array([0, saturation, value], np.uint8), np.array([12, 255, 255], np.uint8))
    high = cv2.inRange(hsv, np.array([170, saturation, value], np.uint8), np.array([179, 255, 255], np.uint8))
    red_mask = cv2.bitwise_or(low, high)
    x1 = max(0, marker.x - frame.left)
    y1 = max(0, marker.y - frame.top)
    x2 = min(frame.width, x1 + marker.width)
    y2 = min(frame.height, y1 + marker.height)
    crop = red_mask[y1:y2, x1:x2]
    pixels = int(np.count_nonzero(crop))
    return pixels, float(pixels) / max(1, crop.size)


def exclude_red_filled_targets(frame: Frame, markers: list, config: dict[str, Any]) -> list:
    max_fraction = float(value_from_config(config, "tree_reject_red_fill_fraction", 0.15))
    eligible = []
    for marker in markers:
        pixels, fraction = red_fill_fraction(frame, marker, config)
        if fraction >= max_fraction:
            print(
                f"tree target rejected: center={marker.center}, red_pixels={pixels}, "
                f"red_fill={fraction:.3f} >= {max_fraction:.3f}"
            )
            continue
        eligible.append(marker)
    return eligible


def configured_point(config: dict[str, Any], name: str, window: dict[str, int] | None) -> tuple[int, int]:
    points = value_from_config(config, "travel_points", {})
    raw = points.get(name) if isinstance(points, dict) else None
    if not isinstance(raw, dict) or "x" not in raw or "y" not in raw:
        return (0, 0)
    x, y = int(raw["x"]), int(raw["y"])
    if bool(value_from_config(config, "travel_points_are_window_relative", False)) and window is not None:
        x += window["left"]
        y += window["top"]
    return x, y


def build_templates(config: dict[str, Any], args) -> dict[str, TemplateState]:
    paths = value_from_config(config, "template_paths", {})
    if not isinstance(paths, dict):
        raise ValueError("template_paths must be a mapping")
    default_scales = parse_scales(str(args.template_scales))
    templates: dict[str, TemplateState] = {}
    for name in TEMPLATE_NAMES:
        raw_path = paths.get(name)
        if not raw_path:
            raise ValueError(f"Missing template path: {name}")
        templates[name] = TemplateState(
            name=name,
            path=resolve_path(raw_path),
            threshold=threshold_for(config, name, args.threshold),
            scales=scales_for(config, name, default_scales),
            region=region_for(config, name),
            click_offset=click_offset_for(config, name),
        )
    missing = [template.path for template in templates.values() if not template.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    return templates


def prepare(config: dict[str, Any], args):
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("woodcutting_status", "empty_inventory_slot", "game_targets", "hole", "bank_marker", "deposit_all", "bank_close"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    return config, window, regions, build_templates(config, args)


def click_template(state, mouse, template: TemplateState, args, label: str) -> bool:
    match, score, scale = state.find(template, args.click_timeout)
    if match is None:
        print(f"{label}: not found; best={score:.3f}, threshold={template.threshold:.3f}")
        return False
    x, y = match_click_coordinates(match, args.click_scale, args.spot_jitter)
    x += template.click_offset[0]
    y += template.click_offset[1]
    if args.dry_run:
        print(f"{label}: would click=({x},{y}), score={match.score:.3f}, scale={scale:g}")
        return True
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), score={match.score:.3f}")
    return True


def click_color_target(screen, mouse, region, settings, label: str, args, nearest: bool = False, config=None, reject_red_fill: bool = False) -> bool:
    frame = screen.capture(region)
    markers = find_color_markers(frame, settings)
    if reject_red_fill:
        markers = exclude_red_filled_targets(frame, markers, config or {})
    target = nearest_to_center(markers, region_center(region)) if nearest else (markers[0] if markers else None)
    if target is None:
        print(f"{label}: no color marker found")
        return False
    print(f"{label}: {len(markers)} marker(s)")
    click_marker(mouse, target, label, args, args.dry_run)
    return True


def click_travel_point(mouse, point: tuple[int, int], label: str, args) -> None:
    x, y = point
    if args.dry_run:
        print(f"{label}: would click configured point=({x},{y})")
        return
    jitter = max(0, args.spot_jitter)
    x += random.randint(-jitter, jitter) if jitter else 0
    y += random.randint(-jitter, jitter) if jitter else 0
    mouse.click(x, y)
    print(f"{label}: clicked configured point=({x},{y})")


def bank_logs(screen, state, mouse, regions, templates, cyan_settings, args) -> bool:
    bank = detect_bank_status(state, templates["deposit_all"], 0.0)
    if not bank.is_open:
        if not click_color_target(screen, mouse, regions["bank_marker"], cyan_settings, "bank", args):
            return False
        wait_ticks("bank opening", args.bank_open_ticks, args, args.dry_run)
    if not click_template(state, mouse, templates["deposit_all"], args, "deposit all"):
        return False
    wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)
    if not click_template(state, mouse, templates["bank_close"], args, "close bank"):
        return False
    wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)
    return True


def show_mouse_position(interval: float) -> int:
    print("Rest the mouse on a travel point and copy x/y. Stop with Ctrl+C.")
    try:
        while True:
            point = pyautogui.position()
            print(f"mouse: x={point.x}, y={point.y}", flush=True)
            time.sleep(max(0.05, interval))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def run_flow(config: dict[str, Any], args) -> int:
    config, window, regions, templates = prepare(config, args)
    outbound = configured_point(config, "outbound", window)
    returning = configured_point(config, "return", window)
    if not args.dry_run and ((0, 0) in (outbound, returning)):
        raise ValueError("Fill travel_points.outbound and travel_points.return before live execution")
    cyan_settings = marker_settings_from_config(config, "cyan_marker")
    green_settings = marker_settings_from_config(config, "green_hole_marker")
    tree_settings = marker_settings_from_config(config, "game_target_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: wc_fossil; cycles={'until stopped' if args.loops <= 0 else args.loops}")
    print(f"travel points: outbound={outbound}, return={returning}; travel={args.travel_seconds:g}s each way")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            completed = 0
            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                active = state.exists(templates["woodcutting_status"], args.status_timeout)
                print(f"woodcutting_status: {'active' if active else 'idle'}")
                if active:
                    wait_ticks("woodcutting active", args.active_wait_ticks, args, args.dry_run)
                    continue
                inventory = detect_inventory_status(state, templates["empty_inventory_slot"], args.inventory_timeout)
                print(f"inventory: {'not full' if inventory.has_empty_slot else 'full'}")
                if inventory.has_empty_slot:
                    if click_color_target(
                        screen, mouse, regions["game_targets"], tree_settings, "nearest tree", args,
                        nearest=True, config=config, reject_red_fill=True,
                    ):
                        wait_ticks("after tree click", args.after_tree_click_ticks, args, args.dry_run)
                    else:
                        wait_ticks("no tree target", args.no_target_wait_ticks, args, args.dry_run)
                    continue

                if not click_color_target(screen, mouse, regions["hole"], green_settings, "outbound green hole", args, nearest=True):
                    wait_ticks("hole missing", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                wait_ticks("after outbound hole", args.after_hole_ticks, args, args.dry_run)
                click_travel_point(mouse, outbound, "outbound travel", args)
                wait_seconds("travel to bank", args.travel_seconds, args, args.dry_run)
                if not bank_logs(screen, state, mouse, regions, templates, cyan_settings, args):
                    wait_ticks("bank route retry", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                click_travel_point(mouse, returning, "return travel", args)
                wait_seconds("travel from bank", args.travel_seconds, args, args.dry_run)
                if not click_color_target(screen, mouse, regions["hole"], green_settings, "return green hole", args, nearest=True):
                    wait_ticks("return hole missing", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                wait_ticks("after return hole", args.after_hole_ticks, args, args.dry_run)
                completed += 1
                print(f"wc_fossil cycle complete: {completed}")
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(config: dict[str, Any], args) -> int:
    config, window, regions, templates = prepare(config, args)
    print(f"travel points: outbound={configured_point(config, 'outbound', window)}, return={configured_point(config, 'return', window)}")
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        for name, template in templates.items():
            match, score, scale = state.find(template, 0.05)
            print(f"{name}: {'found' if match else 'NOT found'} score={score:.3f}, threshold={template.threshold:.3f}, scale={scale:g}")
        for name, prefix in (("game_targets", "game_target_marker"), ("hole", "green_hole_marker"), ("bank_marker", "cyan_marker")):
            frame = screen.capture(regions[name])
            markers = find_color_markers(frame, marker_settings_from_config(config, prefix))
            if name == "game_targets":
                markers = exclude_red_filled_targets(frame, markers, config)
            print(f"{name}: {len(markers)} eligible marker(s); centers={[marker.center for marker in markers]}")
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
    parser = argparse.ArgumentParser(description="Fossil Island woodcutting and bank route.", parents=[pre])
    add_platform_argument(parser, config)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", 1))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", "0.5"))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", 0.82))
    parser.add_argument("--status-timeout", type=float, default=value_from_config(config, "status_timeout", 0.0))
    parser.add_argument("--inventory-timeout", type=float, default=value_from_config(config, "inventory_timeout", 0.0))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", 3.0))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", 0.15))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", 0.6))
    for flag, default in (("active-wait-ticks", 2.0), ("after-tree-click-ticks", 6.0), ("after-hole-ticks", 9.0), ("bank-open-ticks", 3.0), ("after-deposit-ticks", 1.0), ("after-bank-close-ticks", 1.0), ("no-target-wait-ticks", 1.0)):
        parser.add_argument(f"--{flag}", type=float, default=value_from_config(config, flag.replace("-", "_"), default))
    parser.add_argument("--travel-seconds", type=float, default=value_from_config(config, "travel_seconds", 10.0))
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", 0.06))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", 0.04))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", 3))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", 1.0))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", 2.0))
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 0))
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", 0.16))
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", 0.32))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", True))
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--show-mouse-position", action="store_true")
    parser.add_argument("--position-interval", type=float, default=0.25)
    args = parser.parse_args()
    args.platform = resolve_platform(args.platform)
    try:
        if args.show_mouse_position:
            return show_mouse_position(args.position_interval)
        return run_calibration(config, args) if args.calibrate else run_flow(config, args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
