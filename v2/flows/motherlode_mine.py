from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyautogui

from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from v2.actions import StopKeys, build_mouse, humanized_delay
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.game_states.color_markers import capture_color_markers, marker_settings_from_config
from v2.platforming import add_platform_argument
from v2.platforming import resolve_path
from v2.template_config import resolve_regions

install_timestamped_print()

SCRIPT_NAME = "motherlode_mine"
DEFAULT_CONFIG_PATH = ROOT / "config" / "motherlode_mine.example.json"


@dataclass(frozen=True)
class Defaults:
    monitor: int = 1
    mine_start_wait_seconds: float = 5.0
    mining_poll_seconds: float = 1.0
    cart_wait_seconds: float = 10.0
    cart_empty_check_wait_seconds: float = 1.0
    cart_max_emptying_clicks: int = 8
    waterwheel_repair_wait_seconds: float = 5.0
    waterwheel_max_repairs: int = 8
    sack_wait_seconds: float = 5.0
    sack_retry_attempts: int = 3
    sack_retry_wait_seconds: float = 2.0
    bank_open_timeout_seconds: float = 8.0
    bank_ui_timeout_seconds: float = 8.0
    after_deposit_seconds: float = 0.5
    after_bank_close_seconds: float = 0.75
    action_retry_wait_seconds: float = 2.0
    max_cycles: int = 0
    ore_target_strategy: str = "left_to_right"
    time_jitter: float = 0.08
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    countdown: float = 2.0
    dry_run_sleep: bool = True
    dry_run: bool = True
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32


DEFAULTS = Defaults()


def parse_scales(value: Any) -> list[float]:
    if isinstance(value, str):
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [float(item) for item in value if float(item) > 0]
    return [1.0]


def stop_requested(args) -> bool:
    stop_keys = getattr(args, "stop_keys", None)
    return bool(stop_keys is not None and stop_keys.stop_requested)


def seconds_delay(label: str, base_seconds: float, args, dry_run: bool) -> bool:
    delay = humanized_delay(max(0.0, base_seconds), args.time_jitter)
    print(f"{label}: waiting {delay:.2f}s")
    if not dry_run or getattr(args, "dry_run_sleep", False):
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if stop_requested(args):
                print(f"{label}: interrupted")
                return False
            time.sleep(min(0.10, max(0.0, deadline - time.monotonic())))
    return not stop_requested(args)


def configured_point(config: dict[str, Any], key: str, window: dict[str, int] | None) -> tuple[int, int] | None:
    raw = value_from_config(config, key, None)
    if not isinstance(raw, dict) or "x" not in raw or "y" not in raw:
        return None
    x = int(raw["x"])
    y = int(raw["y"])
    if bool(value_from_config(config, f"{key}_is_window_relative", value_from_config(config, "regions_are_window_relative", False))) and window is not None:
        x += int(window["left"])
        y += int(window["top"])
    return x, y


def park_mouse(mouse, args, dry_run: bool, label: str) -> None:
    point = getattr(args, "mouse_park_point", None)
    if not getattr(args, "mouse_park_after_click", False) or point is None:
        return
    x, y = point
    if dry_run:
        print(f"{label}: would park mouse at=({x},{y})")
        return
    mouse.move_to(x, y)
    print(f"{label}: parked mouse at=({x},{y})")


def region_center(region: dict[str, int]) -> tuple[int, int]:
    return int(region["left"]) + int(region["width"]) // 2, int(region["top"]) + int(region["height"]) // 2


def click_point(mouse, point: tuple[int, int], label: str, args, dry_run: bool) -> None:
    if stop_requested(args):
        print(f"{label}: stop requested; skipping click")
        return
    x, y = point
    if dry_run:
        print(f"{label}: would click point=({x},{y})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked point=({x},{y})")
    park_mouse(mouse, args, dry_run, label)


def green_text_pixels(frame: Frame, config: dict[str, Any]) -> tuple[int, float]:
    hsv_min = value_from_config(config, "mining_status_hsv_min", [35, 110, 70])
    hsv_max = value_from_config(config, "mining_status_hsv_max", [90, 255, 255])
    if not isinstance(hsv_min, list) or not isinstance(hsv_max, list) or len(hsv_min) != 3 or len(hsv_max) != 3:
        raise ValueError("mining_status_hsv_min and mining_status_hsv_max must contain three HSV values")
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_min, np.uint8), np.array(hsv_max, np.uint8))
    pixels = int(np.count_nonzero(mask))
    return pixels, float(pixels) / max(1, mask.size)


def is_mining(screen: ScreenCapture, status_region: dict[str, int], config: dict[str, Any]) -> tuple[bool, int, float]:
    pixels, fraction = green_text_pixels(screen.capture(status_region), config)
    min_pixels = int(value_from_config(config, "mining_status_min_green_pixels", 40))
    min_fraction = float(value_from_config(config, "mining_status_min_green_fraction", 0.004))
    return pixels >= min_pixels and fraction >= min_fraction, pixels, fraction


def best_template_match(
    screen: ScreenCapture,
    template_path: Path,
    region: dict[str, int],
    scales: list[float],
) -> tuple[TemplateMatch | None, float, float]:
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"template not found or unreadable: {template_path}")
    frame = screen.capture(region)
    best: TemplateMatch | None = None
    best_score = -1.0
    best_scale = scales[0] if scales else 1.0
    for scale in scales or [1.0]:
        resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if resized.shape[0] > frame.image.shape[0] or resized.shape[1] > frame.image.shape[1]:
            continue
        result = cv2.matchTemplate(frame.image, resized, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if float(max_val) > best_score:
            best_score = float(max_val)
            best_scale = scale
            best = TemplateMatch(
                x=frame.left + int(max_loc[0]),
                y=frame.top + int(max_loc[1]),
                width=int(resized.shape[1]),
                height=int(resized.shape[0]),
                score=best_score,
            )
    return best, best_score, best_scale


def wait_for_template(
    screen: ScreenCapture,
    template_path: Path,
    region: dict[str, int],
    scales: list[float],
    threshold: float,
    timeout: float,
    poll_seconds: float,
    args=None,
) -> tuple[TemplateMatch | None, float, float]:
    deadline = time.monotonic() + max(0.0, timeout)
    best_match: TemplateMatch | None = None
    best_score = -1.0
    best_scale = scales[0] if scales else 1.0
    while True:
        if args is not None and stop_requested(args):
            return None, best_score, best_scale
        match, score, scale = best_template_match(screen, template_path, region, scales)
        if score > best_score:
            best_match, best_score, best_scale = match, score, scale
        if match is not None and score >= threshold:
            return match, score, scale
        if time.monotonic() >= deadline:
            return None, best_score, best_scale
        sleep_until = min(deadline, time.monotonic() + max(0.05, poll_seconds))
        while time.monotonic() < sleep_until:
            if args is not None and stop_requested(args):
                return None, best_score, best_scale
            time.sleep(min(0.10, max(0.0, sleep_until - time.monotonic())))


def inventory_is_full(screen: ScreenCapture, regions: dict[str, dict[str, int]], config: dict[str, Any]) -> tuple[bool, str]:
    mode = str(value_from_config(config, "inventory_full_mode", "empty_slot_template")).strip().lower()
    if mode == "always_false":
        return False, "inventory check disabled"
    if mode == "slot_occupancy":
        occupied, total, details = inventory_slot_occupancy(screen.capture(regions["inventory"]), config)
        allowed_empty = max(0, int(value_from_config(config, "inventory_full_allowed_empty_slots", 0)))
        empty = total - occupied
        return empty <= allowed_empty, f"slot_occupancy occupied={occupied}/{total}, empty={empty}, {details}"
    if mode == "empty_slot_template":
        template_path = resolve_path(value_from_config(config, "empty_inventory_slot_template", "templates/gem_cutting/empty_inventory_slot.png"))
        threshold = float(value_from_config(config, "empty_inventory_slot_threshold", 0.96))
        scales = parse_scales(value_from_config(config, "empty_inventory_slot_scales", [1.0]))
        match, score, scale = best_template_match(screen, template_path, regions["inventory"], scales)
        has_empty_slot = match is not None and score >= threshold
        return not has_empty_slot, f"empty_slot score={score:.3f}/{threshold:.3f} scale={scale:g}"
    if mode == "marker_count":
        settings = marker_settings_from_config(config, "inventory_marker")
        markers = capture_color_markers(screen, regions["inventory"], settings)
        full_count = int(value_from_config(config, "inventory_full_marker_count", 28))
        return len(markers) >= full_count, f"inventory markers={len(markers)}/{full_count}"
    raise ValueError("inventory_full_mode must be slot_occupancy, empty_slot_template, marker_count, or always_false")


def inventory_slot_occupancy(frame: Frame, config: dict[str, Any]) -> tuple[int, int, str]:
    rows = max(1, int(value_from_config(config, "inventory_slots_rows", 7)))
    cols = max(1, int(value_from_config(config, "inventory_slots_cols", 4)))
    sample_width = max(8, int(value_from_config(config, "inventory_slot_sample_width", 32)))
    sample_height = max(8, int(value_from_config(config, "inventory_slot_sample_height", 28)))
    std_threshold = float(value_from_config(config, "inventory_slot_std_threshold", 4.0))
    edge_threshold = float(value_from_config(config, "inventory_slot_edge_threshold", 0.01))
    occupied = 0
    occupied_slots: list[str] = []
    for row in range(rows):
        for col in range(cols):
            center_x = round((col + 0.5) * frame.width / cols)
            center_y = round((row + 0.5) * frame.height / rows)
            left = max(0, center_x - sample_width // 2)
            top = max(0, center_y - sample_height // 2)
            right = min(frame.width, left + sample_width)
            bottom = min(frame.height, top + sample_height)
            crop = frame.image[top:bottom, left:right]
            if crop.size == 0:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            std = float(gray.std())
            edge_fraction = float(np.count_nonzero(cv2.Canny(crop, 40, 100))) / max(1, crop.shape[0] * crop.shape[1])
            if std >= std_threshold or edge_fraction >= edge_threshold:
                occupied += 1
                occupied_slots.append(f"{row + 1}:{col + 1}")
    detail = f"occupied_slots={','.join(occupied_slots) if occupied_slots else 'none'}"
    return occupied, rows * cols, detail


def sort_targets(markers: list[TemplateMatch], strategy: str) -> list[TemplateMatch]:
    if strategy == "best":
        return markers
    if strategy == "top_left":
        return sorted(markers, key=lambda marker: (marker.center[1], marker.center[0]))
    if strategy == "random":
        shuffled = list(markers)
        random.shuffle(shuffled)
        return shuffled
    if strategy == "left_to_right":
        return sorted(markers, key=lambda marker: marker.center[0])
    raise ValueError("ore_target_strategy must be best, random, left_to_right, or top_left")


def click_offset_for(config: dict[str, Any], prefix: str) -> tuple[int, int]:
    offsets = value_from_config(config, f"{prefix}_click_offset", {})
    if not isinstance(offsets, dict):
        return (0, 0)
    return int(offsets.get("x", 0)), int(offsets.get("y", 0))


def spot_jitter_for(config: dict[str, Any], prefix: str, args) -> int:
    return max(0, int(value_from_config(config, f"{prefix}_spot_jitter", args.spot_jitter)))


def controlled_click_coordinates(
    match: TemplateMatch,
    args,
    spot_jitter_pixels: int,
    click_offset: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    center_x, center_y = match.center
    jitter = max(0, int(spot_jitter_pixels))
    max_x_offset = min(jitter, max(0, (match.width - 1) // 2))
    max_y_offset = min(jitter, max(0, (match.height - 1) // 2))
    center_x += random.randint(-max_x_offset, max_x_offset) if max_x_offset else 0
    center_y += random.randint(-max_y_offset, max_y_offset) if max_y_offset else 0
    center_x += click_offset[0]
    center_y += click_offset[1]
    scale = max(0.01, args.click_scale)
    return round(center_x / scale), round(center_y / scale)


def click_match(mouse, match: TemplateMatch, label: str, prefix: str, config: dict[str, Any], args, dry_run: bool) -> None:
    if stop_requested(args):
        print(f"{label}: stop requested; skipping click")
        return
    jitter = spot_jitter_for(config, prefix, args)
    offset = click_offset_for(config, prefix)
    x, y = controlled_click_coordinates(match, args, jitter, offset)
    if dry_run:
        print(
            f"{label}: would click=({x},{y}), score={match.score:.0f}, "
            f"jitter={jitter}, offset={offset}, rect=({match.x},{match.y},{match.width},{match.height})"
        )
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), score={match.score:.0f}, jitter={jitter}, offset={offset}")
    park_mouse(mouse, args, dry_run, label)


def click_template_match(mouse, match: TemplateMatch, label: str, config: dict[str, Any], args, dry_run: bool) -> None:
    if stop_requested(args):
        print(f"{label}: stop requested; skipping click")
        return
    jitter = spot_jitter_for(config, "template", args)
    x, y = controlled_click_coordinates(match, args, jitter)
    if dry_run:
        print(
            f"{label}: would click=({x},{y}), template_score={match.score:.3f}, "
            f"jitter={jitter}, rect=({match.x},{match.y},{match.width},{match.height})"
        )
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), template_score={match.score:.3f}, jitter={jitter}")
    park_mouse(mouse, args, dry_run, label)


def click_exact_center(mouse, match: TemplateMatch, label: str, args, dry_run: bool) -> None:
    if stop_requested(args):
        print(f"{label}: stop requested; skipping click")
        return
    center_x, center_y = match.center
    scale = max(0.01, args.click_scale)
    x, y = round(center_x / scale), round(center_y / scale)
    if dry_run:
        print(f"{label}: would click exact center=({x},{y}), score={match.score:.0f}, rect=({match.x},{match.y},{match.width},{match.height})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked exact center=({x},{y}), score={match.score:.0f}")
    park_mouse(mouse, args, dry_run, label)


def require_regions(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, dict[str, int]]]:
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    required = (
        "mining_status",
        "ore_veins",
        "deposit_cart",
        "waterwheel",
        "sack",
        "bank",
        "inventory",
        "deposit_all",
        "bank_close",
    )
    for name in required:
        if name not in regions or not isinstance(regions[name], dict):
            raise ValueError(f"Missing region: {name}")
    return config, window, regions


def first_marker(screen: ScreenCapture, regions: dict[str, dict[str, int]], config: dict[str, Any], prefix: str, region_name: str) -> TemplateMatch | None:
    settings = marker_settings_from_config(config, prefix)
    markers = capture_color_markers(screen, regions[region_name], settings)
    return markers[0] if markers else None


def find_marker_with_retries(
    screen: ScreenCapture,
    regions: dict[str, dict[str, int]],
    config: dict[str, Any],
    prefix: str,
    region_name: str,
    label: str,
    retries: int,
    wait_seconds: float,
    args,
) -> TemplateMatch | None:
    for attempt in range(max(0, retries) + 1):
        if stop_requested(args):
            return None
        marker = first_marker(screen, regions, config, prefix, region_name)
        if marker is not None:
            return marker
        if attempt >= retries:
            break
        print(f"{label}: not found; retry {attempt + 1}/{retries}")
        if not seconds_delay(f"{label} retry wait", wait_seconds, args, args.dry_run):
            return None
    return None


def run_waterwheel_repairs(screen: ScreenCapture, mouse, regions: dict[str, dict[str, int]], config: dict[str, Any], args) -> int:
    settings = marker_settings_from_config(config, "waterwheel_marker")
    max_repairs = max(0, int(value_from_config(config, "waterwheel_max_repairs", DEFAULTS.waterwheel_max_repairs)))
    wait_seconds = float(value_from_config(config, "waterwheel_repair_wait_seconds", DEFAULTS.waterwheel_repair_wait_seconds))
    repaired = 0
    while max_repairs <= 0 or repaired < max_repairs:
        if stop_requested(args):
            return repaired
        markers = capture_color_markers(screen, regions["waterwheel"], settings)
        if not markers:
            print(f"waterwheel: no gray marker found; repairs={repaired}")
            return repaired
        marker = markers[0]
        print(f"waterwheel: gray marker found count={len(markers)}, repair={repaired + 1}")
        click_match(mouse, marker, "waterwheel", "waterwheel", config, args, args.dry_run)
        repaired += 1
        if not seconds_delay("after waterwheel click", wait_seconds, args, args.dry_run):
            return repaired
    print(f"waterwheel: max repairs reached ({max_repairs}); continuing")
    return repaired


def run_bank_sequence(screen: ScreenCapture, mouse, regions: dict[str, dict[str, int]], config: dict[str, Any], args) -> bool:
    bank_marker = first_marker(screen, regions, config, "bank_marker", "bank")
    if bank_marker is None:
        print("bank: green marker not found; skipping bank click")
    else:
        click_exact_center(mouse, bank_marker, "bank", args, args.dry_run)
    print(f"deposit all: waiting up to {float(value_from_config(config, 'bank_open_timeout_seconds', DEFAULTS.bank_open_timeout_seconds)):.2f}s")

    deposit_template = resolve_path(value_from_config(config, "deposit_all_template", "templates/gem_cutting/deposit_all.png"))
    deposit_scales = parse_scales(value_from_config(config, "deposit_all_scales", [1.0]))
    deposit_threshold = float(value_from_config(config, "deposit_all_threshold", 0.88))
    deposit_match, deposit_score, deposit_scale = wait_for_template(
        screen,
        deposit_template,
        regions["deposit_all"],
        deposit_scales,
        deposit_threshold,
        float(value_from_config(config, "bank_open_timeout_seconds", DEFAULTS.bank_open_timeout_seconds)),
        args.mining_poll_seconds,
        args,
    )
    if deposit_match is None:
        if stop_requested(args):
            return False
        print(f"deposit all: not found; best_score={deposit_score:.3f}, scale={deposit_scale:g}; skipping")
    else:
        click_template_match(mouse, deposit_match, "deposit all", config, args, args.dry_run)
        if not seconds_delay("after deposit all", float(value_from_config(config, "after_deposit_seconds", DEFAULTS.after_deposit_seconds)), args, args.dry_run):
            return False

    close_template = resolve_path(value_from_config(config, "bank_close_template", "templates/gem_cutting/bank_close.png"))
    close_scales = parse_scales(value_from_config(config, "bank_close_scales", [1.0]))
    close_threshold = float(value_from_config(config, "bank_close_threshold", 0.45))
    close_match, close_score, close_scale = wait_for_template(
        screen,
        close_template,
        regions["bank_close"],
        close_scales,
        close_threshold,
        float(value_from_config(config, "bank_ui_timeout_seconds", DEFAULTS.bank_ui_timeout_seconds)),
        args.mining_poll_seconds,
        args,
    )
    if close_match is None:
        if stop_requested(args):
            return False
        fallback = region_center(regions["bank_close"])
        print(f"bank close: not found; best_score={close_score:.3f}, scale={close_scale:g}; using fallback")
        click_point(mouse, fallback, "bank close fallback", args, args.dry_run)
    else:
        click_template_match(mouse, close_match, "bank close", config, args, args.dry_run)
    if not seconds_delay("after bank close", float(value_from_config(config, "after_bank_close_seconds", DEFAULTS.after_bank_close_seconds)), args, args.dry_run):
        return False
    return True


def run_deposit_sequence(screen: ScreenCapture, mouse, regions: dict[str, dict[str, int]], config: dict[str, Any], args) -> bool:
    cart_marker = first_marker(screen, regions, config, "deposit_cart_marker", "deposit_cart")
    if cart_marker is None:
        print("deposit cart 1: red marker not found; skipping")
    else:
        click_match(mouse, cart_marker, "deposit cart 1", "deposit_cart", config, args, args.dry_run)
        if not seconds_delay("after cart click", args.cart_wait_seconds, args, args.dry_run):
            return False

    extra_cart_clicks = 0
    max_clicks = max(0, int(value_from_config(config, "cart_max_emptying_clicks", DEFAULTS.cart_max_emptying_clicks)))
    while not stop_requested(args):
        full, detail = inventory_is_full(screen, regions, config)
        print(f"cart empty check: inventory {'full' if full else 'not full'}; {detail}")
        if not full:
            break
        if max_clicks > 0 and extra_cart_clicks >= max_clicks:
            print(f"cart empty check: max extra cart clicks reached ({max_clicks}); continuing")
            break
        cart_marker = first_marker(screen, regions, config, "deposit_cart_marker", "deposit_cart")
        if cart_marker is None:
            print("deposit cart repeat: red marker not found; continuing")
            break
        extra_cart_clicks += 1
        click_match(mouse, cart_marker, f"deposit cart repeat {extra_cart_clicks}", "deposit_cart", config, args, args.dry_run)
        if not seconds_delay("after cart repeat click", args.cart_empty_check_wait_seconds, args, args.dry_run):
            return False
    run_waterwheel_repairs(screen, mouse, regions, config, args)
    if stop_requested(args):
        return False

    sack_marker = find_marker_with_retries(
        screen,
        regions,
        config,
        "sack_marker",
        "sack",
        "sack",
        max(0, int(value_from_config(config, "sack_retry_attempts", DEFAULTS.sack_retry_attempts))),
        float(value_from_config(config, "sack_retry_wait_seconds", DEFAULTS.sack_retry_wait_seconds)),
        args,
    )
    if sack_marker is None:
        print("sack: pink marker not found after retries; skipping")
    else:
        click_match(mouse, sack_marker, "sack", "sack", config, args, args.dry_run)
        if not seconds_delay("after sack click", args.sack_wait_seconds, args, args.dry_run):
            return False
    return run_bank_sequence(screen, mouse, regions, config, args)


def run_flow(args, config: dict[str, Any]) -> int:
    config, window, regions = require_regions(config)
    args.mouse_park_after_click = bool(value_from_config(config, "mouse_park_after_click", True))
    args.mouse_park_point = configured_point(config, "mouse_park_point", window)
    if args.mouse_park_after_click and args.mouse_park_point is None:
        args.mouse_park_point = region_center(regions["inventory"])
    ore_settings = marker_settings_from_config(config, "ore_vein_marker")
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"{mode}: motherlode mine; cycles={'until stopped' if args.max_cycles <= 0 else args.max_cycles}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    args.stop_keys = stop_keys
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=0)
    stop_keys.start()
    try:
        print("Stop with Esc, Cmd+C, Ctrl+C, or Cmd+Shift+Q.")
        seconds_delay("countdown", max(0.0, args.countdown), args, args.dry_run)
        with ScreenCapture(monitor=args.monitor) as screen:
            completed_cycles = 0
            while not stop_keys.stop_requested:
                if args.max_cycles > 0 and completed_cycles >= args.max_cycles:
                    print(f"max cycles reached: {args.max_cycles}")
                    break

                mining, pixels, fraction = is_mining(screen, regions["mining_status"], config)
                print(f"mining_status: {'mining' if mining else 'idle'} green_pixels={pixels}, green_fraction={fraction:.4f}")
                if mining:
                    seconds_delay("mining poll", args.mining_poll_seconds, args, args.dry_run)
                    continue

                full, detail = inventory_is_full(screen, regions, config)
                print(f"inventory: {'full' if full else 'not full'}; {detail}")
                if full:
                    if run_deposit_sequence(screen, mouse, regions, config, args):
                        completed_cycles += 1
                        print(f"cycle complete: completed={completed_cycles}")
                    else:
                        seconds_delay(
                            "deposit sequence incomplete; retry pause",
                            float(value_from_config(config, "action_retry_wait_seconds", DEFAULTS.action_retry_wait_seconds)),
                            args,
                            args.dry_run,
                        )
                    continue

                ore_markers = sort_targets(capture_color_markers(screen, regions["ore_veins"], ore_settings), args.ore_target_strategy)
                if not ore_markers:
                    print("ore vein: blue marker not found")
                    seconds_delay("no ore target", args.mining_poll_seconds, args, args.dry_run)
                    continue
                click_match(mouse, ore_markers[0], "ore vein", "ore_vein", config, args, args.dry_run)
                seconds_delay("after ore vein click", args.mine_start_wait_seconds, args, args.dry_run)
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def annotate_marker(image: np.ndarray, marker: TemplateMatch, label: str, color: tuple[int, int, int]) -> None:
    cv2.rectangle(image, (marker.x, marker.y), (marker.x + marker.width, marker.y + marker.height), color, 2)
    cv2.putText(image, label, (marker.x, max(18, marker.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def run_calibration(args, config: dict[str, Any]) -> int:
    config, window, regions = require_regions(config)
    marker_specs = (
        ("ore_vein_marker", "ore_veins", "ore vein", (255, 255, 0)),
        ("deposit_cart_marker", "deposit_cart", "deposit cart", (0, 0, 255)),
        ("waterwheel_marker", "waterwheel", "waterwheel", (190, 190, 190)),
        ("sack_marker", "sack", "sack", (255, 0, 255)),
        ("bank_marker", "bank", "bank", (0, 255, 0)),
    )
    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture()
        annotated = frame.image.copy()
        out_dir = ROOT / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = out_dir / "motherlode_initial_screenshot.png"
        annotated_path = out_dir / "motherlode_calibration.png"
        cv2.imwrite(str(screenshot_path), frame.image)
        print(f"Monitor {args.monitor}: left={frame.left}, top={frame.top}, width={frame.width}, height={frame.height}")
        print(f"screenshot: {screenshot_path}")
        if window:
            print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
        for region_name, region in regions.items():
            cv2.rectangle(
                annotated,
                (region["left"], region["top"]),
                (region["left"] + region["width"], region["top"] + region["height"]),
                (180, 180, 180),
                1,
            )
            cv2.putText(annotated, region_name, (region["left"], max(14, region["top"] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
        for prefix, region_name, label, color in marker_specs:
            settings = marker_settings_from_config(config, prefix)
            markers = capture_color_markers(screen, regions[region_name], settings)
            print(f"{label}: found {len(markers)} marker(s)")
            for index, marker in enumerate(markers[:10], start=1):
                click_prefix = prefix.removesuffix("_marker")
                if click_prefix == "bank":
                    scale = max(0.01, args.click_scale)
                    click_x, click_y = round(marker.center[0] / scale), round(marker.center[1] / scale)
                    print(
                        f"  {index}. score={marker.score:.0f}, center={marker.center}, click=({click_x},{click_y}), "
                        f"exact_center=yes, rect=({marker.x},{marker.y},{marker.width},{marker.height})"
                    )
                else:
                    jitter = spot_jitter_for(config, click_prefix, args)
                    offset = click_offset_for(config, click_prefix)
                    click_x, click_y = controlled_click_coordinates(marker, args, jitter, offset)
                    print(
                        f"  {index}. score={marker.score:.0f}, center={marker.center}, click=({click_x},{click_y}), "
                        f"jitter={jitter}, offset={offset}, rect=({marker.x},{marker.y},{marker.width},{marker.height})"
                    )
                annotate_marker(annotated, marker, f"{label} {index}", color)
        mining, pixels, fraction = is_mining(screen, regions["mining_status"], config)
        print(f"mining_status: {'mining' if mining else 'idle'} green_pixels={pixels}, green_fraction={fraction:.4f}")
        full, detail = inventory_is_full(screen, regions, config)
        print(f"inventory: {'full' if full else 'not full'}; {detail}")
        for template_label, template_key, region_name, threshold_key, scales_key in (
            ("deposit_all", "deposit_all_template", "deposit_all", "deposit_all_threshold", "deposit_all_scales"),
            ("bank_close", "bank_close_template", "bank_close", "bank_close_threshold", "bank_close_scales"),
        ):
            match, score, scale = best_template_match(
                screen,
                resolve_path(value_from_config(config, template_key, "")),
                regions[region_name],
                parse_scales(value_from_config(config, scales_key, [1.0])),
            )
            threshold = float(value_from_config(config, threshold_key, 0.8))
            print(f"{template_label}: {'found' if match and score >= threshold else 'not found'} score={score:.3f}/{threshold:.3f} scale={scale:g}")
            if match is not None:
                annotate_marker(annotated, match, template_label, (0, 165, 255))
        cv2.imwrite(str(annotated_path), annotated)
        print(f"annotated calibration: {annotated_path}")
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

    parser = argparse.ArgumentParser(description="Motherlode Mine flow using colored RuneLite markers.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--mine-start-wait-seconds", type=float, default=value_from_config(config, "mine_start_wait_seconds", DEFAULTS.mine_start_wait_seconds))
    parser.add_argument("--mining-poll-seconds", type=float, default=value_from_config(config, "mining_poll_seconds", DEFAULTS.mining_poll_seconds))
    parser.add_argument("--cart-wait-seconds", type=float, default=value_from_config(config, "cart_wait_seconds", DEFAULTS.cart_wait_seconds))
    parser.add_argument("--cart-empty-check-wait-seconds", type=float, default=value_from_config(config, "cart_empty_check_wait_seconds", DEFAULTS.cart_empty_check_wait_seconds))
    parser.add_argument("--cart-max-emptying-clicks", type=int, default=value_from_config(config, "cart_max_emptying_clicks", DEFAULTS.cart_max_emptying_clicks))
    parser.add_argument("--waterwheel-repair-wait-seconds", type=float, default=value_from_config(config, "waterwheel_repair_wait_seconds", DEFAULTS.waterwheel_repair_wait_seconds))
    parser.add_argument("--waterwheel-max-repairs", type=int, default=value_from_config(config, "waterwheel_max_repairs", DEFAULTS.waterwheel_max_repairs))
    parser.add_argument("--sack-wait-seconds", type=float, default=value_from_config(config, "sack_wait_seconds", DEFAULTS.sack_wait_seconds))
    parser.add_argument("--sack-retry-attempts", type=int, default=value_from_config(config, "sack_retry_attempts", DEFAULTS.sack_retry_attempts))
    parser.add_argument("--sack-retry-wait-seconds", type=float, default=value_from_config(config, "sack_retry_wait_seconds", DEFAULTS.sack_retry_wait_seconds))
    parser.add_argument("--action-retry-wait-seconds", type=float, default=value_from_config(config, "action_retry_wait_seconds", DEFAULTS.action_retry_wait_seconds))
    parser.add_argument("--ore-target-strategy", default=value_from_config(config, "ore_target_strategy", DEFAULTS.ore_target_strategy))
    parser.add_argument("--max-cycles", type=int, default=value_from_config(config, "max_cycles", DEFAULTS.max_cycles))
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown))
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min))
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max))
    parser.add_argument("--dry-run-sleep", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run_sleep", DEFAULTS.dry_run_sleep))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()

    try:
        if args.calibrate:
            return run_calibration(args, config)
        return run_flow(args, config)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
