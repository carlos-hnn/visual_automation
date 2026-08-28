from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse
from visual_automation.actions.markers import MarkerActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import (
    capture_color_markers,
    marker_settings_from_config,
    sorted_inventory_markers,
)
from visual_automation.game_states.motion import classify_motion
from visual_automation.platforming import add_platform_argument, resolve_platform
from visual_automation.template_config import resolve_regions

install_timestamped_print()

SCRIPT_NAME = "hunter"
DEFAULT_CONFIG_PATH = ROOT / "config" / "hunter.example.json"


@dataclass(frozen=True)
class Defaults:
    poll_seconds: float = 0.12
    cleanup_every_clicks: int = 10
    inventory_toggle_wait_seconds: float = 0.6
    inventory_click_pause: float = 0.08
    motion_sample_seconds: float = 0.25
    motion_threshold: float = 2.0
    motion_stable_samples: int = 4
    motion_wait_timeout: float = 30.0
    motion_start_grace_seconds: float = 0.8
    motion_scale: float = 0.25
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    countdown: float = 2.0
    monitor: int = 1
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    loops: int = 0
    dry_run: bool = True


DEFAULTS = Defaults()


def click_region_center(mouse, region: dict[str, int], label: str, dry_run: bool) -> None:
    x = region["left"] + region["width"] // 2
    y = region["top"] + region["height"] // 2
    if dry_run:
        print(f"{label}: would click=({x},{y})")
        return
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y})")


def clean_inventory(screen, mouse, regions, settings, actions, args) -> int:
    click_region_center(mouse, regions["inventory_icon"], "open inventory", args.dry_run)
    if not args.dry_run:
        time.sleep(args.inventory_toggle_wait_seconds)
    markers = sorted_inventory_markers(capture_color_markers(screen, regions["inventory_markers"], settings))
    print(f"inventory cleanup: cyan items={len(markers)}")
    for marker in markers:
        actions.click(marker, "inventory item")
        if not args.dry_run:
            time.sleep(args.inventory_click_pause)
    click_region_center(mouse, regions["inventory_icon"], "close inventory", args.dry_run)
    if not args.dry_run:
        time.sleep(args.inventory_toggle_wait_seconds)
    return len(markers)


def cleanup_due(map_clicks: int, every_clicks: int) -> bool:
    return map_clicks > 0 and map_clicks % max(1, every_clicks) == 0


def wait_until_screen_stops(screen, region: dict[str, int], args, stop_keys) -> bool:
    if args.dry_run:
        print("walking: would wait until the game viewport is visually stable")
        return True
    deadline = time.monotonic() + args.motion_wait_timeout
    grace_deadline = time.monotonic() + args.motion_start_grace_seconds
    previous = screen.capture(region).image
    stable = 0
    saw_motion = False
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        time.sleep(args.motion_sample_seconds)
        current = screen.capture(region).image
        status = classify_motion(previous, current, args.motion_threshold, args.motion_scale)
        previous = current
        if status.is_moving:
            saw_motion = True
            stable = 0
        elif saw_motion or time.monotonic() >= grace_deadline:
            stable += 1
        print(
            f"walking: difference={status.difference:.2f}, "
            f"moving={'yes' if status.is_moving else 'no'}, stable={stable}/{args.motion_stable_samples}"
        )
        if stable >= args.motion_stable_samples:
            print("walking: screen stopped")
            return True
    print("walking: screen did not settle before timeout")
    return False


def next_target(markers, last_center: tuple[int, int] | None, region, exclusion_pixels: float):
    candidates = markers
    if last_center is not None:
        candidates = [item for item in markers if math.dist(item.center, last_center) > exclusion_pixels]
    if not candidates:
        return None
    region_center = (
        region["left"] + region["width"] / 2,
        region["top"] + region["height"] / 2,
    )
    return min(candidates, key=lambda item: math.dist(item.center, region_center))


def run_flow(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("game", "target_markers", "inventory_markers", "inventory_icon"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")

    target_settings = marker_settings_from_config(config, "target_marker")
    inventory_settings = marker_settings_from_config(config, "inventory_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: hunter; clicks={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print(f"Inventory cleanup every {args.cleanup_every_clicks} map click(s). Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    clicked = 0
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            actions = MarkerActions(screen, mouse, args, stop_keys)
            while not stop_keys.stop_requested and (args.loops <= 0 or clicked < args.loops):
                exclusions = []
                if "target_ui_exclusion" in regions:
                    exclusions.append(regions["target_ui_exclusion"])
                _marker, markers = actions.find(
                    regions["target_markers"],
                    target_settings,
                    strategy="nearest",
                    exclusions=tuple(exclusions),
                )
                marker = next_target(
                    markers,
                    None,
                    regions["target_markers"],
                    0.0,
                )
                if marker is None:
                    print("target: waiting for the next cyan marker")
                    time.sleep(args.poll_seconds)
                    continue

                actions.click(marker, "hunter target")
                clicked += 1
                print(f"hunter target: click {clicked}")
                wait_until_screen_stops(screen, regions["game"], args, stop_keys)
                if cleanup_due(clicked, args.cleanup_every_clicks) and not stop_keys.stop_requested:
                    cleaned = clean_inventory(screen, mouse, regions, inventory_settings, actions, args)
                    print(f"inventory cleanup complete after map click {clicked}: clicked={cleaned}")
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None)
    known, _ = pre.parse_known_args()
    try:
        config = load_json_config(known.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    parser = argparse.ArgumentParser(description="Follow cyan hunter targets and clean the inventory every ten clicks.", parents=[pre])
    add_platform_argument(parser, config)
    fields = (
        ("monitor", int), ("poll-seconds", float),
        ("cleanup-every-clicks", int), ("inventory-toggle-wait-seconds", float),
        ("inventory-click-pause", float),
        ("motion-sample-seconds", float),
        ("motion-threshold", float), ("motion-stable-samples", int),
        ("motion-wait-timeout", float), ("motion-start-grace-seconds", float),
        ("motion-scale", float),
        ("pre-click-jitter", float), ("spot-jitter", int), ("click-scale", float),
        ("countdown", float), ("move-duration-min", float), ("move-duration-max", float),
        ("loops", int),
    )
    for cli_name, kind in fields:
        key = cli_name.replace("-", "_")
        parser.add_argument(f"--{cli_name}", type=kind, default=value_from_config(config, key, getattr(DEFAULTS, key)))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.cleanup_every_clicks = max(1, args.cleanup_every_clicks)
        args.motion_sample_seconds = max(0.03, args.motion_sample_seconds)
        args.motion_stable_samples = max(1, args.motion_stable_samples)
        args.motion_wait_timeout = max(args.motion_sample_seconds, args.motion_wait_timeout)
        return run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
