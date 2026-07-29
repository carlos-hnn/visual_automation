from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay, wait_ticks
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.core.vision import TemplateMatch
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import (
    capture_color_markers,
    marker_click_point,
    marker_settings_from_config,
    sorted_inventory_markers,
)
from visual_automation.game_states.inventory import detect_inventory_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "woodcutting"
DEFAULT_CONFIG_PATH = ROOT / "config" / "woodcutting.example.json"


def region_center(region: dict[str, int]) -> tuple[int, int]:
    return (
        int(region["left"]) + int(region["width"]) // 2,
        int(region["top"]) + int(region["height"]) // 2,
    )


def nearest_to_center(markers: list[TemplateMatch], center: tuple[int, int]) -> TemplateMatch | None:
    if not markers:
        return None
    return min(markers, key=lambda marker: math.dist(marker.center, center))



def click_marker(mouse, marker: TemplateMatch, label: str, args, dry_run: bool) -> None:
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if dry_run:
        print(
            f"{label}: would click=({x},{y}), center={marker.center}, "
            f"pixels={marker.score:.0f}, rect=({marker.x},{marker.y},{marker.width},{marker.height})"
        )
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), center={marker.center}, pixels={marker.score:.0f}")


def drop_logs(screen, mouse, region, settings, stop_keys: StopKeys, args) -> int:
    markers = sorted_inventory_markers(capture_color_markers(screen, region, settings))
    if not markers:
        print("inventory logs: full inventory detected, but no cyan log markers found")
        return 0
    print(f"inventory logs: {'would click' if args.dry_run else 'clicking'} {len(markers)} cyan marker(s)")
    shift_down = False
    try:
        if args.drop_mode == "shift_drop" and not args.dry_run:
            pyautogui.keyDown("shift")
            shift_down = True
            time.sleep(0.03)
        for marker in markers:
            if stop_keys.stop_requested:
                break
            click_marker(mouse, marker, "inventory log", args, args.dry_run)
            if not args.dry_run:
                time.sleep(humanized_delay(args.drop_click_pause, args.time_jitter / 2))
    finally:
        if shift_down:
            pyautogui.keyUp("shift")
    return len(markers)


def prepare(config: dict[str, Any], args):
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("woodcutting_status", "empty_inventory_slot", "inventory_logs", "game_targets"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    platform = resolve_platform(args.platform)
    templates_dir = platform_template_dir(args.templates_dir, config, platform)
    templates = build_template_states(
        ("woodcutting_status", "empty_inventory_slot"),
        templates_dir,
        args.threshold,
        parse_scales(str(args.template_scales)),
        config,
    )
    missing = [template.path for template in templates.values() if not template.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    return config, window, regions, templates


def run_flow(config: dict[str, Any], args) -> int:
    config, window, regions, templates = prepare(config, args)
    log_settings = marker_settings_from_config(config, "inventory_log_marker")
    target_settings = marker_settings_from_config(config, "game_target_marker")
    target_center = region_center(regions["game_targets"])
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: woodcutting; loops={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print(f"character anchor: center of game_targets at {target_center}")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            loop = 0
            while not stop_keys.stop_requested and (args.loops <= 0 or loop < args.loops):
                loop += 1
                active = state.exists(templates["woodcutting_status"], args.status_timeout)
                print(f"woodcutting_status: {'active' if active else 'idle'}")
                if active:
                    wait_ticks("woodcutting active", args.active_wait_ticks, args, args.dry_run)
                    continue

                inventory = detect_inventory_status(state, templates["empty_inventory_slot"], args.inventory_timeout)
                print(
                    f"inventory: {'not full' if inventory.has_empty_slot else 'full'}; "
                    f"empty_slot_score={inventory.empty_slot_score:.3f}, threshold={inventory.empty_slot_threshold:.3f}"
                )
                if inventory.is_full:
                    dropped = drop_logs(screen, mouse, regions["inventory_logs"], log_settings, stop_keys, args)
                    print(f"inventory cleanup complete: cyan_logs={dropped}")
                    wait_ticks("after inventory cleanup", args.after_drop_ticks, args, args.dry_run)
                    continue

                markers = capture_color_markers(screen, regions["game_targets"], target_settings)
                target = nearest_to_center(markers, target_center)
                if target is None:
                    print("game targets: no cyan marker found")
                    wait_ticks("no target", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                distance = math.dist(target.center, target_center)
                print(f"game targets: {len(markers)} marker(s); nearest distance={distance:.1f}px")
                click_marker(mouse, target, "nearest tree", args, args.dry_run)
                wait_ticks("after tree click", args.after_tree_click_ticks, args, args.dry_run)
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(config: dict[str, Any], args) -> int:
    config, window, regions, templates = prepare(config, args)
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        for name, template in templates.items():
            match, score, scale = state.find(template, 0.05)
            print(f"{name}: {'found' if match else 'NOT found'} score={score:.3f}, threshold={template.threshold:.3f}, scale={scale:g}")
        for name, prefix in (("inventory_logs", "inventory_log_marker"), ("game_targets", "game_target_marker")):
            markers = capture_color_markers(screen, regions[name], marker_settings_from_config(config, prefix))
            print(f"{name}: {len(markers)} cyan marker(s)")
            for index, marker in enumerate(markers, 1):
                print(f"  {index}. center={marker.center}, pixels={marker.score:.0f}, rect=({marker.x},{marker.y},{marker.width},{marker.height})")
        if window:
            print(f"RuneLite window: {window}")
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

    parser = argparse.ArgumentParser(description="Woodcut cyan-marked trees and clear cyan-marked logs when inventory is full.", parents=[pre])
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=Path(value_from_config(config, "templates_dir", "templates/woodcutting")))
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", 1))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", "1.0"))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", 0.82))
    parser.add_argument("--status-timeout", type=float, default=value_from_config(config, "status_timeout", 0.0))
    parser.add_argument("--inventory-timeout", type=float, default=value_from_config(config, "inventory_timeout", 0.0))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", 0.15))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", 0.6))
    parser.add_argument("--active-wait-ticks", type=float, default=value_from_config(config, "active_wait_ticks", 2.0))
    parser.add_argument("--after-tree-click-ticks", type=float, default=value_from_config(config, "after_tree_click_ticks", 2.0))
    parser.add_argument("--after-drop-ticks", type=float, default=value_from_config(config, "after_drop_ticks", 1.0))
    parser.add_argument("--no-target-wait-ticks", type=float, default=value_from_config(config, "no_target_wait_ticks", 1.0))
    parser.add_argument("--drop-mode", choices=("shift_drop", "click"), default=value_from_config(config, "drop_mode", "shift_drop"))
    parser.add_argument("--drop-click-pause", type=float, default=value_from_config(config, "drop_click_pause", 0.10))
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
    args = parser.parse_args()
    try:
        return run_calibration(config, args) if args.calibrate else run_flow(config, args)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
