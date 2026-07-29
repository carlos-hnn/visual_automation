from __future__ import annotations

import argparse
import random
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay, match_click_coordinates, wait_ticks
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.ocr import recognized_text
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.core.vision import TemplateMatch
from visual_automation.definitions import ROOT
from visual_automation.game_states.bank import detect_bank_status
from visual_automation.game_states.color_markers import (
    capture_color_markers,
    marker_click_point,
    marker_settings_from_config,
    sorted_inventory_markers,
)
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "potion_fill"
TEMPLATE_NAMES = ("deposit_all", "bank_close")
DEFAULT_CONFIG_PATH = ROOT / "config" / "potion_fill.example.json"


@dataclass(frozen=True)
class Defaults:
    templates_dir: Path = ROOT / "templates" / "steel_cannonball"
    template_scales: str = "0.5"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    between_potions_ticks: float = 1.0
    after_fill_ticks: float = 2.0
    after_bank_click_ticks: float = 5.0
    after_deposit_ticks: float = 2.0
    after_withdraw_ticks: float = 1.0
    after_bank_close_ticks: float = 1.0
    no_pair_wait_ticks: float = 1.0
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



def click_marker(mouse, marker: TemplateMatch, label: str, args, dry_run: bool) -> None:
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if dry_run:
        print(f"{label}: would click=({x},{y}), pixels={marker.score:.0f}")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), pixels={marker.score:.0f}")


def potion_shaped_markers(markers: list[TemplateMatch], config: dict[str, Any]) -> list[TemplateMatch]:
    min_height = int(value_from_config(config, "potion_marker_shape_min_height", 35))
    max_width = int(value_from_config(config, "potion_marker_shape_max_width", 42))
    min_aspect = float(value_from_config(config, "potion_marker_shape_min_aspect", 1.2))
    return [
        marker
        for marker in markers
        if marker.height >= min_height
        and marker.width <= max_width
        and marker.height / max(1, marker.width) >= min_aspect
    ]


def click_template(state, mouse, template: TemplateState, args, dry_run: bool) -> bool:
    match, score, scale = state.find(template, args.click_timeout)
    if match is None:
        print(f"{template.name}: not found; best={score:.3f}, threshold={template.threshold:.3f}")
        return False
    x, y = match_click_coordinates(match, args.click_scale, args.spot_jitter)
    x += template.click_offset[0]
    y += template.click_offset[1]
    if dry_run:
        print(f"{template.name}: would click=({x},{y}), score={score:.3f}, scale={scale:g}")
    else:
        if args.pre_click_jitter > 0:
            time.sleep(random.uniform(0.0, args.pre_click_jitter))
        mouse.click(x, y)
        print(f"{template.name}: clicked=({x},{y}), score={score:.3f}, scale={scale:g}")
    return True


def resolve_bank_point(config: dict[str, Any], name: str, window: dict[str, int] | None) -> tuple[int, int]:
    points = value_from_config(config, "bank_item_points", {})
    if not isinstance(points, dict) or not isinstance(points.get(name), dict):
        raise ValueError(f"bank_item_points.{name} must contain x and y")
    point = points[name]
    if "x" not in point or "y" not in point:
        raise ValueError(f"bank_item_points.{name} must contain x and y")
    x, y = int(point["x"]), int(point["y"])
    if bool(value_from_config(config, "bank_item_points_are_window_relative", True)):
        if window is None:
            raise ValueError("RuneLite window is required for window-relative bank item points")
        x += window["left"]
        y += window["top"]
    return x, y


def click_point(mouse, point: tuple[int, int], label: str, args, dry_run: bool) -> None:
    jitter = max(0, int(args.spot_jitter))
    x = point[0] + (random.randint(-jitter, jitter) if jitter else 0)
    y = point[1] + (random.randint(-jitter, jitter) if jitter else 0)
    if dry_run:
        print(f"{label}: would click static bank point=({x},{y})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked static bank point=({x},{y})")


def bank_item_display_name(config: dict[str, Any], bank_item_name: str) -> str:
    names = value_from_config(config, "bank_item_display_names", {})
    if isinstance(names, dict) and str(names.get(bank_item_name, "")).strip():
        return str(names[bank_item_name]).strip()
    parts = bank_item_name.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return f"{parts[0].replace('_', ' ')}({parts[1]})"
    return bank_item_name.replace("_", " ")


def normalize_item_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def item_text_matches(expected: str, observed: str, threshold: float = 0.84) -> tuple[bool, float]:
    target = normalize_item_text(expected)
    text = normalize_item_text(observed)
    if not target or not text:
        return False, 0.0
    if target in text:
        return True, 1.0
    if len(text) < len(target):
        score = SequenceMatcher(None, target, text).ratio()
        return score >= threshold, score
    score = max(
        SequenceMatcher(None, target, text[index : index + len(target)]).ratio()
        for index in range(len(text) - len(target) + 1)
    )
    return score >= threshold, score


def bank_hover_slots(config: dict[str, Any], window: dict[str, int] | None) -> list[tuple[int, int]]:
    slots = value_from_config(config, "bank_hover_slots", [])
    if not isinstance(slots, list) or not slots:
        raise ValueError("bank_hover_slots must contain at least one {x, y} point")
    result: list[tuple[int, int]] = []
    for index, point in enumerate(slots):
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise ValueError(f"bank_hover_slots[{index}] must contain x and y")
        x, y = int(point["x"]), int(point["y"])
        if bool(value_from_config(config, "bank_item_points_are_window_relative", True)):
            if window is None:
                raise ValueError("RuneLite window is required for window-relative bank hover slots")
            x += window["left"]
            y += window["top"]
        result.append((x, y))
    return result


def calibrate_bank_item_position(
    mouse,
    config: dict[str, Any],
    window: dict[str, int] | None,
    regions: dict[str, Any],
    bank_item_name: str,
    args,
) -> tuple[int, int]:
    if "bank_hover_text" not in regions:
        raise ValueError("Missing calibration region: bank_hover_text")
    expected = bank_item_display_name(config, bank_item_name)
    if args.dry_run:
        print(f"calibrate-position: would hover bank slots looking for {expected!r}")
        return resolve_bank_point(config, bank_item_name, window)
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise ValueError("--calibrate-position requires rapidocr; install requirements.txt") from exc
    ocr = RapidOCR()
    region = regions["bank_hover_text"]
    match_threshold = float(value_from_config(config, "bank_hover_name_match_threshold", 0.84))
    hover_ticks = float(value_from_config(config, "bank_hover_read_ticks", 1.0))
    observed: list[str] = []
    for index, point in enumerate(bank_hover_slots(config, window), 1):
        mouse.move_to(*point)
        wait_ticks(f"calibrate-position hover slot {index}", hover_ticks, args, False)
        image = pyautogui.screenshot(
            region=(int(region["left"]), int(region["top"]), int(region["width"]), int(region["height"]))
        )
        text = recognized_text(ocr(image))
        observed.append(text)
        print(f"calibrate-position: slot {index} at={point}, hover={text!r}")
        matched, name_score = item_text_matches(expected, text, match_threshold)
        if matched:
            relative = (point[0] - window["left"], point[1] - window["top"]) if window else point
            print(
                f"calibrate-position: {bank_item_name} found at={point}, "
                f"window_relative=({relative[0]},{relative[1]}), name_score={name_score:.3f}"
            )
            return point
    raise ValueError(
        f"calibrate-position: {expected!r} was not found in {len(observed)} visible bank slot(s); "
        "keep the tagged bank tab open and confirm the item is visible"
    )


def prepare(config: dict[str, Any], args):
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("inventory_potions", "game_bank_markers"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    templates_dir = platform_template_dir(args.templates_dir, config, resolve_platform(args.platform))
    templates = build_template_states(
        TEMPLATE_NAMES, templates_dir, args.threshold, parse_scales(str(args.template_scales)), config
    )
    missing = [template.path for template in templates.values() if not template.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    bank_item_name = str(value_from_config(config, "bank_item_name", "super_strength_3")).strip()
    if not bank_item_name:
        raise ValueError("bank_item_name cannot be empty")
    bank_item_point = resolve_bank_point(config, bank_item_name, window)
    return config, window, regions, templates, bank_item_name, bank_item_point


def run_flow(args, config: dict[str, Any]) -> int:
    config, window, regions, templates, bank_item_name, bank_item_point = prepare(config, args)
    if (
        not args.dry_run
        and not args.fill_only
        and not args.calibrate_position
        and not bool(value_from_config(config, "bank_item_points_calibrated", False))
    ):
        raise ValueError("Live mode blocked: bank item points are not calibrated")
    potion_settings = marker_settings_from_config(config, "potion_marker")
    bank_settings = marker_settings_from_config(config, "bank_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: potion fill; loads={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print(f"bank item: {bank_item_name}, static point={bank_item_point}")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            completed_loads = 0
            position_calibrated = False
            while not stop_keys.stop_requested and (args.loops <= 0 or completed_loads < args.loops):
                markers = sorted_inventory_markers(
                    potion_shaped_markers(
                        capture_color_markers(screen, regions["inventory_potions"], potion_settings),
                        config,
                    )
                )
                print(f"inventory: green_potion_markers={len(markers)}")
                if len(markers) >= 2:
                    click_marker(mouse, markers[0], "source potion", args, args.dry_run)
                    wait_ticks("between potions", args.between_potions_ticks, args, args.dry_run)
                    click_marker(mouse, markers[1], "target potion", args, args.dry_run)
                    wait_ticks("after fill", args.after_fill_ticks, args, args.dry_run)
                    continue
                if len(markers) == 1:
                    print("inventory: one green potion remains; banking incomplete pair")
                if args.fill_only:
                    print("fill-only complete: fewer than two green potion markers remain")
                    return 0

                bank_status = detect_bank_status(state, templates["deposit_all"], 0.0)
                if not bank_status.is_open:
                    bank_markers = capture_color_markers(screen, regions["game_bank_markers"], bank_settings)
                    if not bank_markers:
                        wait_ticks("bank marker unavailable", args.no_pair_wait_ticks, args, args.dry_run)
                        continue
                    click_marker(mouse, bank_markers[0], "bank", args, args.dry_run)
                    wait_ticks("bank opening", args.after_bank_click_ticks, args, args.dry_run)
                    bank_status = detect_bank_status(state, templates["deposit_all"], args.click_timeout)
                    if not bank_status.is_open:
                        print(
                            "bank: click sent, but interface is not open yet; "
                            f"best deposit_all={bank_status.deposit_all_score:.3f}"
                        )
                        continue
                if (
                    (args.calibrate_position or bool(value_from_config(config, "auto_calibrate_bank_item_position", False)))
                    and not position_calibrated
                ):
                    bank_item_point = calibrate_bank_item_position(
                        mouse,
                        config,
                        window,
                        regions,
                        bank_item_name,
                        args,
                    )
                    position_calibrated = True
                if not click_template(state, mouse, templates["deposit_all"], args, args.dry_run):
                    continue
                wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)
                click_point(mouse, bank_item_point, f"bank {bank_item_name}", args, args.dry_run)
                wait_ticks("after potion withdrawal", args.after_withdraw_ticks, args, args.dry_run)
                if not click_template(state, mouse, templates["bank_close"], args, args.dry_run):
                    continue
                wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)
                completed_loads += 1
                print(f"potion fill load ready: {completed_loads}")
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
    platform_value = resolve_platform(value_from_config(config, "platform", "auto"))
    templates_dir = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value)
    parser = argparse.ArgumentParser(description="Potion decant/fill automation.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    for name in ("between_potions_ticks", "after_fill_ticks", "after_bank_click_ticks", "after_deposit_ticks", "after_withdraw_ticks", "after_bank_close_ticks", "no_pair_wait_ticks"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=float, default=value_from_config(config, name, getattr(DEFAULTS, name)))
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown))
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 1))
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min))
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--fill-only", action="store_true", help="Fill current inventory and stop before banking.")
    parser.add_argument(
        "--calibrate-position",
        action="store_true",
        help="On the first bank opening, hover visible bank slots and OCR the bank_item_name.",
    )
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        return run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
