from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui
import cv2
import numpy as np

from core.screen import ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from v2.actions import StopKeys, build_mouse, humanized_delay
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.platforming import resolve_path
from v2.game_states.color_markers import (
    capture_color_markers,
    color_mask,
    marker_click_point,
    marker_settings_from_config,
    sorted_inventory_markers,
)
from v2.platforming import add_platform_argument, resolve_platform
from v2.template_config import resolve_regions

install_timestamped_print()

SCRIPT_NAME = "powermining"


@dataclass(frozen=True)
class Defaults:
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    mining_poll_ticks: float = 1.0
    post_rock_mining_confirm_ticks: float = 3.0
    pre_drop_status_confirm_ticks: float = 1.0
    no_target_wait_ticks: float = 1.0
    after_drop_ticks: float = 1.0
    inventory_full_marker_count: int = 27
    rock_click_count: int = 3
    max_cycles: int = 0
    max_status_polls: int = 0
    drop_mode: str = "shift_drop"
    target_strategy: str = "left_to_right"
    time_jitter: float = 0.06
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    countdown: float = 2.0
    monitor: int = 1
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    dry_run: bool = True


DEFAULTS = Defaults()
DEFAULT_CONFIG_PATH = ROOT / "config" / "powermining.example.json"


def wait_ticks(label: str, ticks: float, args, dry_run: bool) -> None:
    delay = humanized_delay(ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {ticks:g} tick(s), {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def green_text_pixels(frame, config: dict[str, Any]) -> tuple[int, float]:
    hsv_min = value_from_config(config, "mining_status_hsv_min", [35, 110, 70])
    hsv_max = value_from_config(config, "mining_status_hsv_max", [90, 255, 255])
    if not isinstance(hsv_min, list) or not isinstance(hsv_max, list) or len(hsv_min) != 3 or len(hsv_max) != 3:
        raise ValueError("mining_status_hsv_min and mining_status_hsv_max must contain three HSV values")
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_min, np.uint8), np.array(hsv_max, np.uint8))
    pixels = int(np.count_nonzero(mask))
    return pixels, float(pixels) / max(1, mask.size)


def is_mining(screen, status_region: dict[str, int], config: dict[str, Any]) -> tuple[bool, int, float]:
    pixels, fraction = green_text_pixels(screen.capture(status_region), config)
    min_pixels = int(value_from_config(config, "mining_status_min_green_pixels", 40))
    min_fraction = float(value_from_config(config, "mining_status_min_green_fraction", 0.004))
    return pixels >= min_pixels and fraction >= min_fraction, pixels, fraction


def parse_scales_value(value: Any) -> list[float]:
    if isinstance(value, str):
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [float(item) for item in value]
    return [1.0]


def inventory_full_chat_present(screen, chat_region: dict[str, int], config: dict[str, Any]) -> tuple[bool, float]:
    template_path = resolve_path(value_from_config(config, "inventory_full_chat_template", "templates/powermining/inventory_full_chat.png"))
    template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"inventory full chat template not found: {template_path}")
    frame = screen.capture(chat_region)
    gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    best = 0.0
    for scale in parse_scales_value(value_from_config(config, "inventory_full_chat_scales", [1.0])):
        if scale <= 0:
            continue
        resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if resized.shape[0] > gray.shape[0] or resized.shape[1] > gray.shape[1]:
            continue
        result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, _max_loc = cv2.minMaxLoc(result)
        best = max(best, float(max_val))
    threshold = float(value_from_config(config, "inventory_full_chat_threshold", 0.72))
    return best >= threshold, best


def wait_for_mining_status(screen, status_region: dict[str, int], chat_region: dict[str, int], config: dict[str, Any], args, dry_run: bool) -> str:
    max_seconds = max(0.0, args.post_rock_mining_confirm_ticks * args.tick_seconds)
    if dry_run:
        print(f"post-rock mining confirm: would poll status for up to {args.post_rock_mining_confirm_ticks:g} tick(s)")
        return "dry_run"
    deadline = time.monotonic() + max_seconds
    best_pixels = 0
    best_fraction = 0.0
    best_chat_score = 0.0
    while time.monotonic() < deadline:
        mining, pixels, fraction = is_mining(screen, status_region, config)
        best_pixels = max(best_pixels, pixels)
        best_fraction = max(best_fraction, fraction)
        if mining:
            print(f"post-rock mining confirm: mining green_pixels={pixels}, green_fraction={fraction:.4f}")
            return "mining"
        full_chat, chat_score = inventory_full_chat_present(screen, chat_region, config)
        best_chat_score = max(best_chat_score, chat_score)
        if full_chat:
            print(f"post-rock mining confirm: inventory full chat detected score={chat_score:.3f}")
            return "inventory_full"
        time.sleep(max(0.03, args.poll_seconds))
    print(
        "post-rock mining confirm: no mining status yet; "
        f"best_green_pixels={best_pixels}, best_fraction={best_fraction:.4f}, best_chat_score={best_chat_score:.3f}"
    )
    return "timeout"


def sort_rock_targets(markers: list[TemplateMatch], strategy: str) -> list[TemplateMatch]:
    if strategy == "best":
        return markers
    if strategy == "top_left":
        return sorted(markers, key=lambda match: (match.center[1], match.center[0]))
    if strategy == "left_to_right":
        return sorted(markers, key=lambda match: match.center[0])
    if strategy == "random":
        shuffled = list(markers)
        random.shuffle(shuffled)
        return shuffled
    raise ValueError("target_strategy must be best, random, left_to_right, or top_left")


def filter_edge_markers(markers: list[TemplateMatch], region: dict[str, int], margin: int) -> list[TemplateMatch]:
    if margin <= 0:
        return markers
    left = int(region["left"]) + margin
    top = int(region["top"]) + margin
    right = int(region["left"]) + int(region["width"]) - margin
    bottom = int(region["top"]) + int(region["height"]) - margin
    return [
        marker
        for marker in markers
        if left <= marker.center[0] <= right and top <= marker.center[1] <= bottom
    ]


def split_large_color_marker(screen, region: dict[str, int], marker_settings, target_count: int, min_pixels: int) -> list[TemplateMatch]:
    frame = screen.capture(region)
    mask = color_mask(frame.image, marker_settings)
    ys, xs = np.where(mask > 0)
    if len(xs) < min_pixels:
        return []
    cluster_count = min(max(1, target_count), len(xs))
    points = np.column_stack([xs.astype(np.float32), ys.astype(np.float32)])
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _compactness, labels, centers = cv2.kmeans(points, cluster_count, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
    markers: list[TemplateMatch] = []
    for index, center in enumerate(centers):
        members = points[labels.ravel() == index]
        if len(members) < min_pixels / max(1, target_count * 2):
            continue
        x1, y1 = members.min(axis=0)
        x2, y2 = members.max(axis=0)
        markers.append(
            TemplateMatch(
                x=frame.left + int(round(x1)),
                y=frame.top + int(round(y1)),
                width=max(1, int(round(x2 - x1 + 1))),
                height=max(1, int(round(y2 - y1 + 1))),
                score=float(len(members)),
            )
        )
    return markers


def capture_rock_markers(screen, region: dict[str, int], marker_settings, config: dict[str, Any], args) -> list[TemplateMatch]:
    markers = capture_color_markers(screen, region, marker_settings)
    edge_margin = int(value_from_config(config, "rock_marker_edge_margin", 0))
    markers = filter_edge_markers(markers, region, edge_margin)
    if markers or not bool(value_from_config(config, "rock_split_large_markers", True)):
        return markers
    min_pixels = int(value_from_config(config, "rock_large_marker_min_pixels", 1000))
    split_markers = split_large_color_marker(screen, region, marker_settings, args.rock_click_count, min_pixels)
    return filter_edge_markers(split_markers, region, edge_margin)


def click_marker(mouse, marker: TemplateMatch, label: str, args, dry_run: bool) -> None:
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if dry_run:
        print(f"{label}: marker score={marker.score:.0f}, would click=({x},{y}), rect=({marker.x},{marker.y},{marker.width},{marker.height})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked marker score={marker.score:.0f}, at=({x},{y})")


def click_point(mouse, x: int, y: int, label: str, args, dry_run: bool) -> None:
    jitter = max(0, int(args.spot_jitter))
    click_x = x + (random.randint(-jitter, jitter) if jitter else 0)
    click_y = y + (random.randint(-jitter, jitter) if jitter else 0)
    if dry_run:
        print(f"{label}: would click point=({click_x},{click_y})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(click_x, click_y)
    print(f"{label}: clicked point=({click_x},{click_y})")


def resolve_rock_points(config: dict[str, Any], window: dict[str, int] | None) -> list[tuple[int, int]]:
    raw_points = value_from_config(config, "rock_points", [])
    if not isinstance(raw_points, list):
        raise ValueError("rock_points must be a list of {x, y} objects")
    points: list[tuple[int, int]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict) or "x" not in raw_point or "y" not in raw_point:
            raise ValueError("each rock_points item must contain x and y")
        x = int(raw_point["x"])
        y = int(raw_point["y"])
        if bool(value_from_config(config, "rock_points_are_window_relative", True)) and window is not None:
            x += window["left"]
            y += window["top"]
        points.append((x, y))
    return points


def drop_inventory_markers(screen, mouse, inventory_region: dict[str, int], marker_settings, args) -> int:
    markers = sorted_inventory_markers(capture_color_markers(screen, inventory_region, marker_settings))
    if not markers:
        print("drop: no inventory ore markers found")
        return 0
    print(f"drop: {'would drop' if args.dry_run else 'dropping'} {len(markers)} inventory marker(s) using {args.drop_mode}")
    if args.drop_mode == "none":
        return 0
    if args.drop_mode not in {"shift_drop", "drop_key"}:
        raise ValueError("drop_mode must be shift_drop, drop_key, or none")

    dropped = 0
    shift_down = False
    try:
        if args.drop_mode == "shift_drop" and not args.dry_run:
            pyautogui.keyDown("shift")
            shift_down = True
            time.sleep(0.03)
        for marker in markers:
            if args.drop_mode == "drop_key" and not args.dry_run:
                pyautogui.press(args.drop_key)
                time.sleep(0.03)
            click_marker(mouse, marker, "inventory ore", args, args.dry_run)
            dropped += 1
            if not args.dry_run:
                time.sleep(humanized_delay(args.drop_click_pause, args.time_jitter / 2))
    finally:
        if shift_down:
            pyautogui.keyUp("shift")
    wait_ticks("after drop", args.after_drop_ticks, args, args.dry_run)
    return dropped


def dismiss_inventory_full_chat(config: dict[str, Any], args, dry_run: bool) -> None:
    key = str(value_from_config(config, "inventory_full_chat_dismiss_key", "space")).strip()
    if not key:
        return
    if dry_run:
        print(f"inventory full chat: would press {key}")
    else:
        pyautogui.press(key)
        print(f"inventory full chat: pressed {key}")
    wait_ticks("after chat dismiss", float(value_from_config(config, "after_chat_dismiss_ticks", 0.5)), args, dry_run)


def run_flow(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("mining_status", "rock_markers", "inventory_ore_markers", "chat_inventory_full"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")

    rock_marker_settings = marker_settings_from_config(config, "rock_marker")
    inventory_marker_settings = marker_settings_from_config(config, "inventory_marker")
    rock_points: list[tuple[int, int]] = []
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"{mode}: powermining; cycles={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            completed_cycles = 0
            status_polls = 0
            while not stop_keys.stop_requested and (args.loops <= 0 or completed_cycles < args.loops):
                if args.max_cycles > 0 and completed_cycles >= args.max_cycles:
                    print(f"max cycles reached: {args.max_cycles}")
                    break
                if args.max_status_polls > 0 and status_polls >= args.max_status_polls:
                    print(f"max status polls reached: {args.max_status_polls}")
                    break

                mining, green_pixels, green_fraction = is_mining(screen, regions["mining_status"], config)
                print(
                    f"mining_status: {'mining' if mining else 'idle'} "
                    f"green_pixels={green_pixels}, green_fraction={green_fraction:.4f}, completed={completed_cycles}"
                )
                if mining:
                    status_polls += 1
                    wait_ticks("mining status active", args.mining_poll_ticks, args, args.dry_run)
                    continue

                chat_full, chat_score = inventory_full_chat_present(screen, regions["chat_inventory_full"], config)
                inventory_markers = capture_color_markers(screen, regions["inventory_ore_markers"], inventory_marker_settings)
                inventory_full = chat_full or len(inventory_markers) >= args.inventory_full_marker_count
                print(
                    f"inventory: markers={len(inventory_markers)}/{args.inventory_full_marker_count}; "
                    f"chat_full={'yes' if chat_full else 'no'} score={chat_score:.3f}; "
                    f"{'full' if inventory_full else 'not full'}"
                )
                if inventory_full:
                    wait_ticks("pre-drop status confirm", args.pre_drop_status_confirm_ticks, args, args.dry_run)
                    mining, green_pixels, green_fraction = is_mining(screen, regions["mining_status"], config)
                    print(
                        f"pre_drop_mining_status: {'mining' if mining else 'idle'} "
                        f"green_pixels={green_pixels}, green_fraction={green_fraction:.4f}"
                    )
                    if mining:
                        print("drop skipped: mining status is active")
                        continue
                    if chat_full:
                        dismiss_inventory_full_chat(config, args, args.dry_run)
                    dropped = drop_inventory_markers(screen, mouse, regions["inventory_ore_markers"], inventory_marker_settings, args)
                    completed_cycles += 1
                    print(f"cycle complete: dropped={dropped}, completed={completed_cycles}")
                    continue

                rock_markers = capture_rock_markers(screen, regions["rock_markers"], rock_marker_settings, config, args)
                if not rock_points and not rock_markers:
                    print("rocks: no rock marker found")
                    if inventory_markers:
                        wait_ticks("pre-drop status confirm", args.pre_drop_status_confirm_ticks, args, args.dry_run)
                        mining, green_pixels, green_fraction = is_mining(screen, regions["mining_status"], config)
                        print(
                            f"pre_drop_mining_status: {'mining' if mining else 'idle'} "
                            f"green_pixels={green_pixels}, green_fraction={green_fraction:.4f}"
                        )
                        if mining:
                            print("drop skipped: mining status is active")
                            continue
                        dismiss_inventory_full_chat(config, args, args.dry_run)
                        dropped = drop_inventory_markers(screen, mouse, regions["inventory_ore_markers"], inventory_marker_settings, args)
                        completed_cycles += 1
                        print(f"cycle complete: dropped={dropped}, completed={completed_cycles}")
                        continue
                    status_polls += 1
                    wait_ticks("no rock target", args.no_target_wait_ticks, args, args.dry_run)
                    continue

                status_polls = 0
                targets: list[tuple[int, int]] = []
                marker_targets = sort_rock_targets(rock_markers, args.target_strategy)[:1]
                target_count = 1 if targets or marker_targets else 0
                source = "configured point" if targets else "rock marker"
                print(f"rocks: clicking {target_count} {source} target")
                if targets:
                    x, y = targets[0]
                    click_point(mouse, x, y, "rock", args, args.dry_run)
                else:
                    click_marker(mouse, marker_targets[0], "rock", args, args.dry_run)
                post_click_status = wait_for_mining_status(
                    screen,
                    regions["mining_status"],
                    regions["chat_inventory_full"],
                    config,
                    args,
                    args.dry_run,
                )
                if post_click_status == "inventory_full":
                    dismiss_inventory_full_chat(config, args, args.dry_run)
                    dropped = drop_inventory_markers(screen, mouse, regions["inventory_ore_markers"], inventory_marker_settings, args)
                    completed_cycles += 1
                    print(f"cycle complete: dropped={dropped}, completed={completed_cycles}")
                print("rock clicked; waiting for mining status before next decision")
    finally:
        stop_keys.stop()
        try:
            pyautogui.keyUp("shift")
        except Exception:
            pass
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    rock_marker_settings = marker_settings_from_config(config, "rock_marker")
    inventory_marker_settings = marker_settings_from_config(config, "inventory_marker")
    rock_points = resolve_rock_points(config, window)
    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture()
        print(f"Monitor {args.monitor}: left={frame.left}, top={frame.top}, width={frame.width}, height={frame.height}")
        if window:
            print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
        for label in ("rock_markers", "inventory_ore_markers"):
            region = regions.get(label)
            if not isinstance(region, dict):
                print(f"{label}: missing region")
                continue
            marker_settings = inventory_marker_settings if label == "inventory_ore_markers" else rock_marker_settings
            markers = (
                capture_color_markers(screen, region, marker_settings)
                if label == "inventory_ore_markers"
                else capture_rock_markers(screen, region, marker_settings, config, args)
            )
            if label == "rock_markers":
                markers = filter_edge_markers(
                    markers,
                    region,
                    int(value_from_config(config, "rock_marker_edge_margin", 0)),
                )
            print(f"{label}: found {len(markers)} cyan marker(s)")
            for index, marker in enumerate(markers[:10], start=1):
                x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
                print(
                    f"  {index}. score={marker.score:.0f}, center={marker.center}, "
                    f"click=({x},{y}), rect=({marker.x},{marker.y},{marker.width},{marker.height})"
                )
        if rock_points:
            print(f"rock_points: {len(rock_points)} configured point(s)")
            for index, (x, y) in enumerate(rock_points, start=1):
                print(f"  {index}. click=({x},{y})")
        status_region = regions.get("mining_status")
        if isinstance(status_region, dict):
            mining, pixels, fraction = is_mining(screen, status_region, config)
            print(f"mining_status: {'mining' if mining else 'idle'} green_pixels={pixels}, green_fraction={fraction:.4f}")
        chat_region = regions.get("chat_inventory_full")
        if isinstance(chat_region, dict):
            full_chat, score = inventory_full_chat_present(screen, chat_region, config)
            print(f"chat_inventory_full: {'found' if full_chat else 'not found'} score={score:.3f}")
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

    parser = argparse.ArgumentParser(description="Mine cyan-marked rocks and drop cyan-marked ores.", parents=[pre])
    add_platform_argument(parser, config)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--mining-poll-ticks", type=float, default=value_from_config(config, "mining_poll_ticks", DEFAULTS.mining_poll_ticks))
    parser.add_argument("--post-rock-mining-confirm-ticks", type=float, default=value_from_config(config, "post_rock_mining_confirm_ticks", DEFAULTS.post_rock_mining_confirm_ticks))
    parser.add_argument("--pre-drop-status-confirm-ticks", type=float, default=value_from_config(config, "pre_drop_status_confirm_ticks", DEFAULTS.pre_drop_status_confirm_ticks))
    parser.add_argument("--no-target-wait-ticks", type=float, default=value_from_config(config, "no_target_wait_ticks", DEFAULTS.no_target_wait_ticks))
    parser.add_argument("--after-drop-ticks", type=float, default=value_from_config(config, "after_drop_ticks", DEFAULTS.after_drop_ticks))
    parser.add_argument("--inventory-full-marker-count", type=int, default=value_from_config(config, "inventory_full_marker_count", DEFAULTS.inventory_full_marker_count))
    parser.add_argument("--rock-click-count", type=int, default=value_from_config(config, "rock_click_count", DEFAULTS.rock_click_count))
    parser.add_argument("--max-cycles", type=int, default=value_from_config(config, "max_cycles", DEFAULTS.max_cycles))
    parser.add_argument("--max-status-polls", type=int, default=value_from_config(config, "max_status_polls", DEFAULTS.max_status_polls))
    parser.add_argument("--drop-mode", default=value_from_config(config, "drop_mode", DEFAULTS.drop_mode))
    parser.add_argument("--drop-key", default=value_from_config(config, "drop_key", "shift"))
    parser.add_argument("--drop-click-pause", type=float, default=value_from_config(config, "drop_click_pause", 0.05))
    parser.add_argument("--target-strategy", default=value_from_config(config, "target_strategy", DEFAULTS.target_strategy))
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown))
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 1))
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min))
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()

    try:
        args.platform = resolve_platform(args.platform)
        args.rock_click_count = max(1, int(args.rock_click_count))
        args.inventory_full_marker_count = max(1, int(args.inventory_full_marker_count))
        args.max_cycles = max(0, int(args.max_cycles))
        args.max_status_polls = max(0, int(args.max_status_polls))
        args.drop_click_pause = max(0.0, float(args.drop_click_pause))
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
