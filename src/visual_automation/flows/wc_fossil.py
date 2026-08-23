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
from visual_automation.actions.timing import wait_ticks
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import Frame, ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.flows.woodcutting import click_marker, nearest_to_center, region_center
from visual_automation.game_states.bank import detect_bank_status
from visual_automation.game_states.color_markers import (
    find_color_markers,
    marker_settings_from_config,
)
from visual_automation.game_states.inventory import detect_inventory_grid_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, resolve_path, resolve_platform
from visual_automation.template_config import click_offset_for, region_for, resolve_regions, scales_for, threshold_for

install_timestamped_print()

DEFAULT_CONFIG_PATH = ROOT / "config" / "wc_fossil.example.json"
TEMPLATE_NAMES = ("deposit_all", "bank_close")


def wait_seconds(label: str, seconds: float, args, dry_run: bool) -> None:
    delay = max(0.0, seconds)
    print(f"{label}: waiting {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def detect_woodcutting_active(screen, region, config: dict[str, Any]) -> tuple[bool, int]:
    frame = screen.capture(region)
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    hsv_min = np.array(value_from_config(config, "woodcutting_active_hsv_min", [50, 150, 100]), np.uint8)
    hsv_max = np.array(value_from_config(config, "woodcutting_active_hsv_max", [70, 255, 255]), np.uint8)
    pixels = int(np.count_nonzero(cv2.inRange(hsv, hsv_min, hsv_max)))
    required = int(value_from_config(config, "woodcutting_active_min_pixels", 120))
    return pixels >= required, pixels


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
    for name in (
        "woodcutting_status", "inventory", "game_targets", "green_marker",
        "red_marker", "red_ui_exclusion", "pink_marker", "bank_marker", "deposit_all", "bank_close",
    ):
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


def find_color_target(screen, region, settings, nearest: bool = False, exclusions=()):
    frame = screen.capture(region)
    markers = find_color_markers(frame, settings)
    markers = [marker for marker in markers if not any(
        excluded["left"] <= marker.center[0] < excluded["left"] + excluded["width"]
        and excluded["top"] <= marker.center[1] < excluded["top"] + excluded["height"]
        for excluded in exclusions
    )]
    target = nearest_to_center(markers, region_center(region)) if nearest else (markers[0] if markers else None)
    return target, markers


def click_color_target(screen, mouse, region, settings, label: str, args, nearest: bool = False) -> bool:
    target, markers = find_color_target(screen, region, settings, nearest)
    if target is None:
        print(f"{label}: no color marker found")
        return False
    print(f"{label}: {len(markers)} marker(s)")
    click_marker(mouse, target, label, args, args.dry_run)
    return True


def wait_and_click_color_target(
    screen, mouse, region, settings, label: str, args, stop_keys, nearest: bool = False, exclusions=(),
) -> bool:
    deadline = time.monotonic() + max(0.0, args.marker_timeout)
    while not stop_keys.stop_requested:
        target, markers = find_color_target(screen, region, settings, nearest, exclusions)
        if target is not None:
            print(f"{label}: {len(markers)} marker(s)")
            click_marker(mouse, target, label, args, args.dry_run)
            return target
        if time.monotonic() >= deadline:
            print(f"{label}: no color marker found before timeout")
            return None
        time.sleep(max(0.01, args.poll_seconds))
    return None


def wait_and_click_stable_color_target(
    screen, mouse, region, settings, label: str, args, stop_keys,
) -> bool:
    deadline = time.monotonic() + max(0.0, args.marker_timeout)
    previous = None
    stable_samples = 0
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        target, markers = find_color_target(screen, region, settings, nearest=True)
        if target is None:
            previous = None
            stable_samples = 0
        else:
            if previous is not None and (
                abs(target.center[0] - previous.center[0]) <= args.stable_marker_tolerance
                and abs(target.center[1] - previous.center[1]) <= args.stable_marker_tolerance
            ):
                stable_samples += 1
            else:
                stable_samples = 1
            previous = target
            print(
                f"{label}: candidate center={target.center}; "
                f"stable={stable_samples}/{args.stable_marker_samples}"
            )
            if stable_samples >= args.stable_marker_samples:
                final_target, final_markers = find_color_target(screen, region, settings, nearest=True)
                if final_target is not None and (
                    abs(final_target.center[0] - target.center[0]) <= args.stable_marker_tolerance
                    and abs(final_target.center[1] - target.center[1]) <= args.stable_marker_tolerance
                ):
                    print(f"{label}: final fresh capture confirmed; {len(final_markers)} marker(s)")
                    click_marker(mouse, final_target, label, args, args.dry_run)
                    return True
                previous = None
                stable_samples = 0
                print(f"{label}: moved during final confirmation; restarting stability check")
        time.sleep(max(0.05, args.stable_marker_interval))
    print(f"{label}: no stable marker found before timeout")
    return False


def wait_clicked_marker_gone(screen, region, settings, clicked, label: str, args, stop_keys) -> bool:
    deadline = time.monotonic() + max(0.0, args.marker_timeout)
    radius = max(clicked.width, clicked.height, 24)
    while not stop_keys.stop_requested:
        _, markers = find_color_target(screen, region, settings)
        still_present = any(
            abs(marker.center[0] - clicked.center[0]) <= radius
            and abs(marker.center[1] - clicked.center[1]) <= radius
            for marker in markers
        )
        if not still_present:
            print(f"{label}: transition confirmed")
            return True
        if time.monotonic() >= deadline:
            print(f"{label}: clicked marker did not disappear")
            return False
        time.sleep(max(0.01, args.poll_seconds))
    return False


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


def bank_logs(screen, state, mouse, regions, templates, blue_settings, args, stop_keys) -> bool:
    bank = detect_bank_status(state, templates["deposit_all"], 0.0)
    if not bank.is_open and not wait_and_click_stable_color_target(
        screen, mouse, regions["bank_marker"], blue_settings, "cyan bank", args, stop_keys,
    ):
        return False
    match, score, scale = state.find(templates["deposit_all"], args.bank_state_timeout)
    if match is None:
        print(f"bank did not open: deposit all not found; best={score:.3f}")
        return False
    x, y = match_click_coordinates(match, args.click_scale, args.spot_jitter)
    x += templates["deposit_all"].click_offset[0]
    y += templates["deposit_all"].click_offset[1]
    if args.dry_run:
        print(f"deposit all: would click=({x},{y}), score={match.score:.3f}, scale={scale:g}")
    else:
        mouse.click(x, y)
        print(f"deposit all: clicked=({x},{y}), score={match.score:.3f}")
    if stop_keys.stop_requested:
        return False
    wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)
    if not click_template(state, mouse, templates["bank_close"], args, "close bank"):
        return False
    wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)
    return True


def return_to_trees(
    screen, mouse, regions, red_settings, green_settings,
    tree_settings, args, stop_keys,
) -> bool:
    red = wait_and_click_stable_color_target(
        screen, mouse, regions["red_marker"], red_settings,
        "return red marker", args, stop_keys,
    )
    if not red:
        return False
    wait_seconds(
        "before return green search",
        args.before_return_green_seconds,
        args,
        args.dry_run,
    )
    green = wait_and_click_stable_color_target(
        screen, mouse, regions["green_marker"], green_settings,
        "return green marker", args, stop_keys,
    )
    if not green:
        return False
    wait_seconds(
        "after return green click",
        args.after_return_green_seconds,
        args,
        args.dry_run,
    )
    deadline = time.monotonic() + max(0.0, args.marker_timeout)
    while not stop_keys.stop_requested:
        tree, markers = find_color_target(
            screen, regions["game_targets"], tree_settings, nearest=True,
        )
        if tree is not None:
            print(f"yellow tree area ready: {len(markers)} marker(s)")
            return True
        if time.monotonic() >= deadline:
            print("yellow tree area not found before timeout")
            return False
        time.sleep(max(0.01, args.poll_seconds))
    return False


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
    blue_settings = marker_settings_from_config(config, "blue_marker")
    green_settings = marker_settings_from_config(config, "green_marker")
    red_settings = marker_settings_from_config(config, "red_marker")
    pink_settings = marker_settings_from_config(config, "pink_marker")
    tree_settings = marker_settings_from_config(config, "yellow_tree_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: wc_fossil; cycles={'until stopped' if args.loops <= 0 else args.loops}")
    print("route markers: yellow tree -> green -> red -> pink -> cyan bank -> red -> green -> yellow tree")
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
            wait_before_status_check = False
            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                if wait_before_status_check:
                    wait_seconds("before first woodcutting status check", args.first_status_check_seconds, args, args.dry_run)
                    wait_before_status_check = False
                active, status_pixels = detect_woodcutting_active(
                    screen, regions["woodcutting_status"], config,
                )
                print(f"woodcutting_status: {'active' if active else 'idle'}; green_pixels={status_pixels}")
                if active:
                    wait_ticks("woodcutting active", args.active_wait_ticks, args, args.dry_run)
                    continue
                inventory = detect_inventory_grid_status(
                    screen,
                    regions["inventory"],
                    args.empty_slots_required,
                    (args.inventory_first_x, args.inventory_first_y),
                    (args.inventory_column_spacing, args.inventory_row_spacing),
                    patch_radius=args.inventory_patch_radius,
                    occupied_std_threshold=args.inventory_occupied_std,
                )
                print(f"inventory: {'full' if inventory.is_full else 'not full'}; empty_slots={inventory.empty_slots}/28")
                if not inventory.is_full:
                    if click_color_target(
                        screen, mouse, regions["game_targets"], tree_settings, "nearest yellow tree", args,
                        nearest=True,
                    ):
                        wait_before_status_check = True
                    else:
                        wait_ticks("no tree target", args.no_target_wait_ticks, args, args.dry_run)
                    continue

                green = wait_and_click_color_target(
                    screen, mouse, regions["green_marker"], green_settings, "outbound green marker", args, stop_keys, nearest=True,
                )
                if not green:
                    wait_ticks("green marker missing", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                wait_seconds(
                    "after outbound green click",
                    args.after_outbound_green_seconds,
                    args,
                    args.dry_run,
                )
                if not wait_clicked_marker_gone(screen, regions["green_marker"], green_settings, green, "outbound green", args, stop_keys):
                    continue
                red = wait_and_click_color_target(
                    screen, mouse, regions["red_marker"], red_settings,
                    "outbound red marker", args, stop_keys, nearest=True,
                )
                if not red:
                    wait_ticks("red marker missing", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                pink = wait_and_click_stable_color_target(
                    screen, mouse, regions["pink_marker"], pink_settings,
                    "outbound pink marker", args, stop_keys,
                )
                if not pink:
                    wait_ticks("pink marker missing", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                if not bank_logs(screen, state, mouse, regions, templates, blue_settings, args, stop_keys):
                    wait_ticks("bank route retry", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                if not return_to_trees(
                    screen, mouse, regions, red_settings,
                    green_settings, tree_settings, args, stop_keys,
                ):
                    continue
                completed += 1
                print(f"wc_fossil cycle complete: {completed}")
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(config: dict[str, Any], args) -> int:
    config, window, regions, templates = prepare(config, args)
    print(f"window: {window}")
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        for name, template in templates.items():
            match, score, scale = state.find(template, 0.05)
            print(f"{name}: {'found' if match else 'NOT found'} score={score:.3f}, threshold={template.threshold:.3f}, scale={scale:g}")
        active, status_pixels = detect_woodcutting_active(screen, regions["woodcutting_status"], config)
        print(f"woodcutting status color: {'active' if active else 'idle'}; green_pixels={status_pixels}")
        inventory = detect_inventory_grid_status(
            screen, regions["inventory"], args.empty_slots_required,
            (args.inventory_first_x, args.inventory_first_y),
            (args.inventory_column_spacing, args.inventory_row_spacing),
            patch_radius=args.inventory_patch_radius,
            occupied_std_threshold=args.inventory_occupied_std,
        )
        print(f"inventory grid: empty_slots={inventory.empty_slots}/28; {'full' if inventory.is_full else 'not full'}")
        for name, prefix in (
            ("game_targets", "yellow_tree_marker"),
            ("green_marker", "green_marker"),
            ("red_marker", "red_marker"),
            ("pink_marker", "pink_marker"),
            ("bank_marker", "blue_marker"),
        ):
            frame = screen.capture(regions[name])
            markers = find_color_markers(frame, marker_settings_from_config(config, prefix))
            if name == "red_marker":
                excluded = regions["red_ui_exclusion"]
                markers = [marker for marker in markers if not (
                    excluded["left"] <= marker.center[0] < excluded["left"] + excluded["width"]
                    and excluded["top"] <= marker.center[1] < excluded["top"] + excluded["height"]
                )]
            print(f"{name}: {len(markers)} eligible marker(s); centers={[marker.center for marker in markers]}")
    return 0


def run_bank_stage_test(config: dict[str, Any], args) -> int:
    config, _window, regions, templates = prepare(config, args)
    blue_settings = marker_settings_from_config(config, "blue_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: wc_fossil bank stage test")
    print("Stage: stable cyan bank -> open -> deposit all -> close bank")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))
    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            success = bank_logs(screen, state, mouse, regions, templates, blue_settings, args, stop_keys)
            print(f"bank stage test: {'complete' if success else 'failed'}")
            return 0 if success else 1
    finally:
        stop_keys.stop()


def run_return_stage_test(config: dict[str, Any], args) -> int:
    config, _window, regions, _templates = prepare(config, args)
    red_settings = marker_settings_from_config(config, "red_marker")
    green_settings = marker_settings_from_config(config, "green_marker")
    tree_settings = marker_settings_from_config(config, "yellow_tree_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: wc_fossil return route test")
    print("Stage: stable red -> wait 3s -> stable green -> wait 5s -> yellow area")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))
    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            success = return_to_trees(
                screen, mouse, regions, red_settings,
                green_settings, tree_settings, args, stop_keys,
            )
            print(f"return route test: {'complete' if success else 'failed'}")
            return 0 if success else 1
    finally:
        stop_keys.stop()


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
    parser.add_argument("--empty-slots-required", type=int, default=value_from_config(config, "empty_slots_required", 28))
    parser.add_argument("--inventory-first-x", type=int, default=value_from_config(config, "inventory_first_x", 40))
    parser.add_argument("--inventory-first-y", type=int, default=value_from_config(config, "inventory_first_y", 31))
    parser.add_argument("--inventory-column-spacing", type=float, default=value_from_config(config, "inventory_column_spacing", 44.0))
    parser.add_argument("--inventory-row-spacing", type=float, default=value_from_config(config, "inventory_row_spacing", 38.5))
    parser.add_argument("--inventory-patch-radius", type=int, default=value_from_config(config, "inventory_patch_radius", 13))
    parser.add_argument("--inventory-occupied-std", type=float, default=value_from_config(config, "inventory_occupied_std", 8.0))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", 3.0))
    parser.add_argument("--marker-timeout", type=float, default=value_from_config(config, "marker_timeout", 30.0))
    parser.add_argument("--bank-state-timeout", type=float, default=value_from_config(config, "bank_state_timeout", 15.0))
    parser.add_argument("--first-status-check-seconds", type=float, default=value_from_config(config, "first_status_check_seconds", 10.0))
    parser.add_argument("--after-outbound-green-seconds", type=float, default=value_from_config(config, "after_outbound_green_seconds", 5.0))
    parser.add_argument("--after-return-green-seconds", type=float, default=value_from_config(config, "after_return_green_seconds", 5.0))
    parser.add_argument("--before-return-green-seconds", type=float, default=value_from_config(config, "before_return_green_seconds", 3.0))
    parser.add_argument("--stable-marker-interval", type=float, default=value_from_config(config, "stable_marker_interval", 0.5))
    parser.add_argument("--stable-marker-samples", type=int, default=value_from_config(config, "stable_marker_samples", 3))
    parser.add_argument("--stable-marker-tolerance", type=int, default=value_from_config(config, "stable_marker_tolerance", 4))
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
    parser.add_argument("--test-bank-stage", action="store_true")
    parser.add_argument("--test-return-stage", action="store_true")
    parser.add_argument("--show-mouse-position", action="store_true")
    parser.add_argument("--position-interval", type=float, default=0.25)
    args = parser.parse_args()
    args.platform = resolve_platform(args.platform)
    try:
        if args.show_mouse_position:
            return show_mouse_position(args.position_interval)
        if args.calibrate:
            return run_calibration(config, args)
        if args.test_bank_stage:
            return run_bank_stage_test(config, args)
        if args.test_return_stage:
            return run_return_stage_test(config, args)
        return run_flow(config, args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
