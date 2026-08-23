from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from visual_automation.actions import StopKeys, build_mouse
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.core.vision import TemplateMatch
from visual_automation.definitions import ROOT
from visual_automation.flows.motherlode_mine import best_template_match, inventory_is_full, is_mining, parse_scales
from visual_automation.game_states.color_markers import capture_color_markers, marker_settings_from_config
from visual_automation.platforming import add_platform_argument, resolve_path
from visual_automation.template_config import resolve_regions

install_timestamped_print()

SCRIPT_NAME = "mine_bank"
DEFAULT_CONFIG_PATH = ROOT / "config" / "mine_bank.example.json"


@dataclass(frozen=True)
class Defaults:
    monitor: int = 1
    bank_wait_seconds: float = 4.0
    travel_wait_seconds: float = 4.0
    target_retry_seconds: float = 0.5
    mining_status_poll_seconds: float = 0.10
    mining_start_timeout_seconds: float = 1.5
    mining_end_timeout_seconds: float = 20.0
    failed_mining_attempts_before_inventory_check: int = 5
    template_timeout_seconds: float = 3.0
    template_poll_seconds: float = 0.15
    after_deposit_seconds: float = 0.25
    after_bank_close_seconds: float = 0.25
    start_mode: str = "banking"
    max_cycles: int = 0
    time_jitter: float = 0.0
    pre_click_jitter: float = 0.02
    spot_jitter: int = 1
    click_scale: float = 1.0
    countdown: float = 2.0
    dry_run: bool = True
    dry_run_sleep: bool = False
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32


DEFAULTS = Defaults()


def stop_requested(args) -> bool:
    return bool(getattr(args, "stop_keys", None) and args.stop_keys.stop_requested)


def wait_seconds(label: str, seconds: float, args) -> bool:
    delay = max(0.0, seconds)
    if args.time_jitter > 0:
        delay = max(0.0, random.uniform(delay * (1 - args.time_jitter), delay * (1 + args.time_jitter)))
    print(f"{label}: waiting {delay:.2f}s")
    if args.dry_run and not args.dry_run_sleep:
        return not stop_requested(args)
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if stop_requested(args):
            return False
        time.sleep(min(0.10, deadline - time.monotonic()))
    return not stop_requested(args)


def click_match(mouse, match: TemplateMatch, label: str, args, *, exact: bool = False) -> None:
    x, y = match.center
    if not exact and args.spot_jitter > 0:
        x += random.randint(-args.spot_jitter, args.spot_jitter)
        y += random.randint(-args.spot_jitter, args.spot_jitter)
    x, y = round(x / max(0.01, args.click_scale)), round(y / max(0.01, args.click_scale))
    if args.dry_run:
        print(f"{label}: would click ({x},{y}); score={match.score:.1f}")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked ({x},{y}); score={match.score:.1f}")


def click_point(mouse, point: tuple[int, int], label: str, args) -> None:
    x, y = point
    if args.dry_run:
        print(f"{label}: would click ({x},{y})")
        return
    mouse.click(round(x / max(0.01, args.click_scale)), round(y / max(0.01, args.click_scale)))
    print(f"{label}: clicked ({x},{y})")


def configured_point(config: dict[str, Any], key: str, window: dict[str, int] | None) -> tuple[int, int] | None:
    raw = value_from_config(config, key, None)
    if not isinstance(raw, dict) or "x" not in raw or "y" not in raw:
        return None
    x, y = int(raw["x"]), int(raw["y"])
    if bool(value_from_config(config, f"{key}_is_window_relative", True)) and window is not None:
        x += window["left"]
        y += window["top"]
    return x, y


def sort_rocks(markers: list[TemplateMatch]) -> list[TemplateMatch]:
    """Use a stable clockwise-ish order: top first, then lower rocks left-to-right."""
    return sorted(markers, key=lambda marker: (marker.center[1], marker.center[0]))


def marker_nearest_rock_center(
    travel_markers: list[TemplateMatch], rock_markers: list[TemplateMatch], max_distance: float
) -> TemplateMatch | None:
    """Reject unrelated yellow UI/world markers and keep the one inside the rock cluster."""
    if not travel_markers or not rock_markers:
        return None
    center_x = sum(marker.center[0] for marker in rock_markers) / len(rock_markers)
    center_y = sum(marker.center[1] for marker in rock_markers) / len(rock_markers)
    nearest = min(
        travel_markers,
        key=lambda marker: (marker.center[0] - center_x) ** 2 + (marker.center[1] - center_y) ** 2,
    )
    distance = ((nearest.center[0] - center_x) ** 2 + (nearest.center[1] - center_y) ** 2) ** 0.5
    return nearest if distance <= max(0.0, max_distance) else None


def required_regions(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, dict[str, int]]]:
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be an object")
    for name in ("bank_search", "mine_search", "mining_status", "inventory", "deposit_all", "bank_close"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    return config, window, regions


def first_marker(screen: ScreenCapture, region: dict[str, int], config: dict[str, Any], prefix: str) -> TemplateMatch | None:
    markers = capture_color_markers(screen, region, marker_settings_from_config(config, prefix))
    return markers[0] if markers else None


def click_template(screen, mouse, regions, config, args, name: str, default_path: str, default_threshold: float) -> bool:
    path = resolve_path(value_from_config(config, f"{name}_template", default_path))
    threshold = float(value_from_config(config, f"{name}_threshold", default_threshold))
    scales = parse_scales(value_from_config(config, f"{name}_scales", [0.5, 0.75, 1.0]))
    deadline = time.monotonic() + args.template_timeout_seconds
    best_score = -1.0
    while True:
        match, score, _scale = best_template_match(screen, path, regions[name], scales)
        best_score = max(best_score, score)
        if match is not None and score >= threshold:
            click_match(mouse, match, name.replace("_", " "), args, exact=True)
            return True
        if args.dry_run or stop_requested(args) or time.monotonic() >= deadline:
            print(f"{name.replace('_', ' ')}: not found; best_score={best_score:.3f}/{threshold:.3f}")
            return False
        time.sleep(max(0.05, args.template_poll_seconds))


def bank_inventory(screen, mouse, regions, config, args) -> bool:
    marker = first_marker(screen, regions["bank_search"], config, "bank_marker")
    if marker is None:
        print("bank: blue marker not found; retrying next cycle")
        return False
    click_match(mouse, marker, "bank", args, exact=True)
    if not wait_seconds("bank opening/movement", args.bank_wait_seconds, args):
        return False
    if not click_template(screen, mouse, regions, config, args, "deposit_all", "templates/gem_cutting/deposit_all.png", 0.88):
        return False
    if not wait_seconds("after deposit all", args.after_deposit_seconds, args):
        return False
    if not click_template(screen, mouse, regions, config, args, "bank_close", "templates/gem_cutting/bank_close.png", 0.40):
        return False
    return wait_seconds("after bank close", args.after_bank_close_seconds, args)


def move_to_rocks(screen, mouse, regions, config, window, args) -> bool:
    travel_markers = capture_color_markers(
        screen, regions["mine_search"], marker_settings_from_config(config, "travel_marker")
    )
    rock_markers = capture_color_markers(
        screen, regions["mine_search"], marker_settings_from_config(config, "rock_marker")
    )
    marker = marker_nearest_rock_center(
        travel_markers,
        rock_markers,
        float(value_from_config(config, "travel_marker_max_rock_distance", 80.0)),
    )
    if marker is not None:
        click_match(mouse, marker, "yellow travel marker", args, exact=True)
    else:
        fallback = configured_point(config, "travel_fallback_point", window)
        if fallback is None:
            print("travel: yellow marker missing and no travel_fallback_point configured")
            return False
        click_point(mouse, fallback, "travel fallback", args)
    return wait_seconds("travel to rocks", args.travel_wait_seconds, args)


def wait_for_mining_completion(screen, regions, config, args) -> bool:
    """Return True only when green Mining appeared and then disappeared."""
    start_deadline = time.monotonic() + max(0.0, args.mining_start_timeout_seconds)
    while not stop_requested(args):
        mining, pixels, fraction = is_mining(screen, regions["mining_status"], config)
        if mining:
            print(f"mining status: started; green_pixels={pixels}, fraction={fraction:.4f}")
            break
        if time.monotonic() >= start_deadline:
            print(f"mining status: did not appear; green_pixels={pixels}, fraction={fraction:.4f}")
            return False
        time.sleep(max(0.03, args.mining_status_poll_seconds))
    else:
        return False

    end_deadline = time.monotonic() + max(0.0, args.mining_end_timeout_seconds)
    while not stop_requested(args):
        mining, pixels, fraction = is_mining(screen, regions["mining_status"], config)
        if not mining:
            print("mining status: finished; selecting next rock")
            return True
        if time.monotonic() >= end_deadline:
            print("mining status: remained visible past timeout; rescanning")
            return True
        time.sleep(max(0.03, args.mining_status_poll_seconds))
    return False


def mine_attempt(screen, mouse, regions, config, args) -> bool | None:
    """Click one available rock; return None when no red rock exists."""
    rocks = sort_rocks(
        capture_color_markers(screen, regions["mine_search"], marker_settings_from_config(config, "rock_marker"))
    )
    if not rocks:
        print("rocks: no red rocks currently available; rescanning")
        wait_seconds("rock retry", args.target_retry_seconds, args)
        return None
    click_match(mouse, rocks[0], "next red rock", args)
    if args.dry_run:
        return False
    return wait_for_mining_completion(screen, regions, config, args)


def run_flow(args, config: dict[str, Any]) -> int:
    config, window, regions = required_regions(config)
    start_mode = str(args.start_mode).strip().lower()
    if start_mode not in {"banking", "mining"}:
        raise ValueError("start_mode must be banking or mining")
    mouse = build_mouse(
        args.move_duration_min,
        args.move_duration_max,
        spot_jitter_pixels=args.spot_jitter,
    )
    stop_keys = StopKeys()
    args.stop_keys = stop_keys
    stop_keys.start()
    try:
        if not wait_seconds("startup countdown", args.countdown, args):
            return 0
        cycles = 0
        with ScreenCapture(monitor=args.monitor) as screen:
            while not stop_requested(args) and (args.max_cycles <= 0 or cycles < args.max_cycles):
                if cycles > 0 or start_mode == "banking":
                    print(f"cycle {cycles + 1}: banking")
                    if not bank_inventory(screen, mouse, regions, config, args):
                        wait_seconds("bank retry", args.target_retry_seconds, args)
                        continue
                else:
                    print("cycle 1: starting at mining area; initial bank skipped")
                if not move_to_rocks(screen, mouse, regions, config, window, args):
                    wait_seconds("travel retry", args.target_retry_seconds, args)
                    continue
                failed_attempts = 0
                while not stop_requested(args):
                    mined = mine_attempt(screen, mouse, regions, config, args)
                    if mined is None:
                        continue
                    if mined:
                        failed_attempts = 0
                        continue
                    failed_attempts += 1
                    print(
                        f"mining status: failed attempt {failed_attempts}/"
                        f"{args.failed_mining_attempts_before_inventory_check}"
                    )
                    if failed_attempts < args.failed_mining_attempts_before_inventory_check:
                        continue
                    full, detail = inventory_is_full(screen, regions, config)
                    print(f"inventory after failed mining attempts: {'full' if full else 'not full'}; {detail}")
                    failed_attempts = 0
                    if full:
                        cycles += 1
                        break
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(args, config: dict[str, Any]) -> int:
    config, window, regions = required_regions(config)
    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture()
        annotated = frame.image.copy()
        for prefix, label, color in (
            ("bank_marker", "bank", (255, 255, 0)),
            ("travel_marker", "travel", (0, 255, 255)),
            ("rock_marker", "rock", (0, 0, 255)),
        ):
            region_name = "bank_search" if prefix == "bank_marker" else "mine_search"
            markers = capture_color_markers(screen, regions[region_name], marker_settings_from_config(config, prefix))
            print(f"{label}: found {len(markers)} marker(s): {[m.center for m in markers]}")
            for marker in markers:
                cv2.rectangle(annotated, (marker.x, marker.y), (marker.x + marker.width, marker.y + marker.height), color, 2)
        full, detail = inventory_is_full(screen, regions, config)
        print(f"inventory: {'full' if full else 'not full'}; {detail}")
        output = ROOT / "debug" / "mine_bank_calibration.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output), annotated)
        print(f"calibration image: {output}")
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
    parser = argparse.ArgumentParser(description="Bank and mine three marked rocks until inventory is full.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    for name, kind in (
        ("monitor", int), ("bank_wait_seconds", float), ("travel_wait_seconds", float),
        ("target_retry_seconds", float), ("template_timeout_seconds", float),
        ("template_poll_seconds", float), ("after_deposit_seconds", float),
        ("after_bank_close_seconds", float), ("max_cycles", int),
        ("mining_status_poll_seconds", float), ("mining_start_timeout_seconds", float),
        ("mining_end_timeout_seconds", float), ("failed_mining_attempts_before_inventory_check", int),
        ("time_jitter", float), ("pre_click_jitter", float), ("spot_jitter", int),
        ("click_scale", float), ("countdown", float), ("move_duration_min", float),
        ("move_duration_max", float),
    ):
        parser.add_argument(f"--{name.replace('_', '-')}", type=kind, default=value_from_config(config, name, getattr(DEFAULTS, name)))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--dry-run-sleep", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run_sleep", DEFAULTS.dry_run_sleep))
    parser.add_argument(
        "--start-mode",
        choices=("banking", "mining"),
        default=value_from_config(config, "start_mode", DEFAULTS.start_mode),
        help="banking starts with a deposit; mining skips only the first bank visit",
    )
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()
    try:
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
