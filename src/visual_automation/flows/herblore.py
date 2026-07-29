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
from visual_automation.core.safety import report_progress
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.core.vision import TemplateMatch
from visual_automation.definitions import ROOT
from visual_automation.game_states.bank import detect_bank_status
from visual_automation.game_states.color_markers import (
    capture_color_markers,
    marker_click_point,
    marker_settings_from_config,
)
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "herblore"
TEMPLATE_NAMES = ("bank", "deposit_all", "bank_close")
DEFAULT_CONFIG_PATH = ROOT / "config" / "herblore.example.json"


@dataclass(frozen=True)
class Defaults:
    templates_dir: Path = ROOT / "templates" / "steel_cannonball"
    template_scales: str = "0.5"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    mixing_ticks: float = 15.0
    after_item_ticks: float = 1.0
    after_confirm_ticks: float = 2.0
    after_bank_click_ticks: float = 5.0
    after_deposit_ticks: float = 2.0
    after_withdraw_ticks: float = 1.0
    after_bank_close_ticks: float = 1.0
    no_items_wait_ticks: float = 1.0
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
_HOVER_OCR = None



def click_marker(mouse, marker: TemplateMatch, label: str, args, dry_run: bool) -> None:
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if dry_run:
        print(f"{label}: would click=({x},{y}), pixels={marker.score:.0f}")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), pixels={marker.score:.0f}")


def inventory_item_markers(
    markers: list[TemplateMatch], config: dict[str, Any], item_number: int
) -> list[TemplateMatch]:
    min_height = int(
        value_from_config(
            config,
            f"inventory_marker_{item_number}_shape_min_height",
            value_from_config(config, "inventory_marker_shape_min_height", 30),
        )
    )
    max_width = int(
        value_from_config(
            config,
            f"inventory_marker_{item_number}_shape_max_width",
            value_from_config(config, "inventory_marker_shape_max_width", 60),
        )
    )
    return [marker for marker in markers if marker.height >= min_height and marker.width <= max_width]


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


def item_key(name: str) -> str:
    key = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.casefold())).strip("_")
    return key.replace("rannar", "ranarr")


def normalized_item_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return normalized.replace("rannar", "ranarr")


def item_name_matches(expected: str, observed: str, threshold: float = 0.84) -> tuple[bool, float]:
    target = normalized_item_text(expected)
    text = normalized_item_text(observed)
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


def read_hover_action(mouse, point: tuple[int, int], regions: dict[str, Any]) -> str:
    global _HOVER_OCR
    if "bank_hover_text" not in regions:
        raise ValueError("Missing region: bank_hover_text")
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise ValueError("Bank click safety requires rapidocr") from exc
    if _HOVER_OCR is None:
        _HOVER_OCR = RapidOCR()
    mouse.move_to(*point)
    time.sleep(0.6)
    region = regions["bank_hover_text"]
    image = pyautogui.screenshot(
        region=(int(region["left"]), int(region["top"]), int(region["width"]), int(region["height"]))
    )
    return recognized_text(_HOVER_OCR(image))


def bank_hover_is_safe(action: str) -> bool:
    normalized = normalized_item_text(action)
    return "bankbooth" in normalized and "use" not in normalized


def resolve_bank_point(
    config: dict[str, Any], name: str, window: dict[str, int] | None, required: bool = True
) -> tuple[int, int] | None:
    points = value_from_config(config, "bank_item_points", {})
    key = item_key(name)
    if not isinstance(points, dict) or not isinstance(points.get(key), dict):
        if required:
            raise ValueError(f"bank_item_points.{key} must contain x and y")
        return None
    point = points[key]
    if "x" not in point or "y" not in point:
        if required:
            raise ValueError(f"bank_item_points.{key} must contain x and y")
        return None
    x, y = int(point["x"]), int(point["y"])
    if bool(value_from_config(config, "bank_item_points_are_window_relative", True)):
        if window is None:
            raise ValueError("RuneLite window is required for window-relative bank item points")
        x += window["left"]
        y += window["top"]
    return x, y


def bank_hover_slots(config: dict[str, Any], window: dict[str, int] | None) -> list[tuple[int, int]]:
    raw_slots = value_from_config(config, "bank_hover_slots", [])
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ValueError("bank_hover_slots must contain at least one {x, y} point")
    slots: list[tuple[int, int]] = []
    for index, raw in enumerate(raw_slots):
        if not isinstance(raw, dict) or "x" not in raw or "y" not in raw:
            raise ValueError(f"bank_hover_slots[{index}] must contain x and y")
        x, y = int(raw["x"]), int(raw["y"])
        if bool(value_from_config(config, "bank_item_points_are_window_relative", True)):
            if window is None:
                raise ValueError("RuneLite window is required for window-relative bank slots")
            x += window["left"]
            y += window["top"]
        slots.append((x, y))
    return slots


def calibrate_missing_bank_items(
    mouse,
    config: dict[str, Any],
    window: dict[str, int] | None,
    regions: dict[str, Any],
    names: list[str],
    args,
) -> dict[str, tuple[int, int]]:
    missing = list(dict.fromkeys(names))
    if not missing:
        return {}
    if "bank_hover_text" not in regions:
        raise ValueError("Missing region: bank_hover_text")
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise ValueError("Automatic bank calibration requires rapidocr_onnxruntime") from exc
    if args.dry_run:
        print(f"bank calibration: would hover slots looking for {missing}")
        return {}
    ocr = RapidOCR()
    region = regions["bank_hover_text"]
    expected = {name: normalized_item_text(name) for name in missing}
    match_threshold = float(value_from_config(config, "bank_hover_name_match_threshold", 0.84))
    found: dict[str, tuple[int, int]] = {}
    hover_ticks = float(value_from_config(config, "bank_hover_read_ticks", 1.0))
    for index, point in enumerate(bank_hover_slots(config, window), 1):
        mouse.move_to(*point)
        wait_ticks(f"bank calibration hover slot {index}", hover_ticks, args, False)
        image = pyautogui.screenshot(
            region=(int(region["left"]), int(region["top"]), int(region["width"]), int(region["height"]))
        )
        text = recognized_text(ocr(image))
        print(f"bank calibration: slot {index} at={point}, hover={text!r}")
        for name in expected:
            matched, name_score = item_name_matches(name, text, match_threshold)
            if name not in found and matched:
                found[name] = point
                relative = (point[0] - window["left"], point[1] - window["top"]) if window else point
                print(
                    f"bank calibration: {name!r} found window_relative={relative}, "
                    f"name_score={name_score:.3f}"
                )
        if len(found) == len(expected):
            return found
    unresolved = [name for name in missing if name not in found]
    raise ValueError(f"Bank item(s) not found in visible tagged slots: {', '.join(unresolved)}")


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


def refill_open_bank(
    state,
    mouse,
    config: dict[str, Any],
    window: dict[str, int] | None,
    regions: dict[str, Any],
    templates: dict[str, TemplateState],
    item_names: list[str],
    bank_points: dict[str, tuple[int, int] | None],
    args,
) -> None:
    missing = [name for name in item_names if bank_points[name] is None]
    if missing:
        bank_points.update(calibrate_missing_bank_items(mouse, config, window, regions, missing, args))
    if not click_template(state, mouse, templates["deposit_all"], args, args.dry_run):
        raise ValueError("Bank is open, but deposit_all was not found")
    wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)
    for name in item_names:
        point = bank_points[name]
        if point is None:
            raise ValueError(f"No bank point resolved for {name}")
        click_point(mouse, point, f"bank {name}", args, args.dry_run)
        wait_ticks(f"after {name} withdrawal", args.after_withdraw_ticks, args, args.dry_run)
    if not click_template(state, mouse, templates["bank_close"], args, args.dry_run):
        raise ValueError("Bank is open, but bank_close was not found")
    wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)


def prepare(config: dict[str, Any], args):
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("inventory_items", "game_bank_markers"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    templates_dir = platform_template_dir(args.templates_dir, config, resolve_platform(args.platform))
    templates = build_template_states(
        TEMPLATE_NAMES, templates_dir, args.threshold, parse_scales(str(args.template_scales)), config
    )
    missing = [template.path for template in templates.values() if not template.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    item_names = [args.bank_item_1, args.bank_item_2]
    bank_points = {name: resolve_bank_point(config, name, window, required=False) for name in item_names}
    return config, window, regions, templates, item_names, bank_points


def run_flow(args, config: dict[str, Any]) -> int:
    config, window, regions, templates, item_names, bank_points = prepare(config, args)
    if args.calibrate_position:
        bank_points = {name: None for name in item_names}
        print("bank calibration forced: ignoring saved X/Y for both items")
    item_1_settings = marker_settings_from_config(config, "ranarr_marker")
    item_2_settings = marker_settings_from_config(config, "water_marker")
    bank_marker_settings = marker_settings_from_config(config, "bank_marker")
    print(f"{'DRY RUN' if args.dry_run else 'LIVE'}: herblore; loops={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print(f"bank items: {item_names[0]!r}={bank_points[item_names[0]]}, {item_names[1]!r}={bank_points[item_names[1]]}")
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
                bank_status = detect_bank_status(state, templates["deposit_all"], 0.0)
                if bank_status.is_open:
                    print("bank: already open; preparing initial/next inventory load")
                    refill_open_bank(
                        state, mouse, config, window, regions, templates,
                        item_names, bank_points, args,
                    )
                    continue
                report_progress("template:deposit_all.png")

                item_1_markers = inventory_item_markers(
                    capture_color_markers(screen, regions["inventory_items"], item_1_settings), config, 1
                )
                item_2_markers = inventory_item_markers(
                    capture_color_markers(screen, regions["inventory_items"], item_2_settings), config, 2
                )
                print(
                    f"inventory: {item_names[0]} markers={len(item_1_markers)}, "
                    f"{item_names[1]} markers={len(item_2_markers)}"
                )
                if not item_1_markers or not item_2_markers:
                    wait_ticks("inventory pair unavailable", args.no_items_wait_ticks, args, args.dry_run)
                    continue

                click_marker(mouse, item_1_markers[0], item_names[0], args, args.dry_run)
                wait_ticks(f"after {item_names[0]}", args.after_item_ticks, args, args.dry_run)
                click_marker(mouse, item_2_markers[0], item_names[1], args, args.dry_run)
                wait_ticks("before confirmation", args.after_confirm_ticks, args, args.dry_run)
                if args.dry_run:
                    print("mix confirmation: would press space")
                else:
                    pyautogui.press("space")
                    print("mix confirmation: pressed space")
                wait_ticks("mixing all items", args.mixing_ticks, args, args.dry_run)

                bank_status = detect_bank_status(state, templates["deposit_all"], 0.0)
                if not bank_status.is_open:
                    bank_markers = capture_color_markers(screen, regions["game_bank_markers"], bank_marker_settings)
                    if not bank_markers:
                        print("bank: no blue game-area marker found")
                        continue
                    bank_point = marker_click_point(bank_markers[0], args.click_scale, 0)
                    hover_action = read_hover_action(mouse, bank_point, regions)
                    print(f"bank pre-click hover: {hover_action!r}")
                    mixing_deadline = time.monotonic() + float(
                        value_from_config(config, "mixing_completion_timeout_seconds", 30.0)
                    )
                    while "mixing" in normalized_item_text(hover_action) and not stop_keys.stop_requested:
                        if time.monotonic() >= mixing_deadline:
                            stop_keys.request_stop("mixing status did not finish before the safety timeout")
                            break
                        wait_ticks(
                            "mixing still active",
                            float(value_from_config(config, "mixing_completion_poll_ticks", 2.0)),
                            args,
                            args.dry_run,
                        )
                        hover_action = read_hover_action(mouse, bank_point, regions)
                        print(f"bank pre-click hover while waiting for mix: {hover_action!r}")
                    if stop_keys.stop_requested:
                        continue
                    if not bank_hover_is_safe(hover_action):
                        normalized_hover = normalized_item_text(hover_action)
                        if "use" in normalized_hover and "bankbooth" in normalized_hover:
                            print(
                                f"bank safety: an inventory item is still selected; "
                                f"retrying {item_names[1]} before banking"
                            )
                            retry_targets = inventory_item_markers(
                                capture_color_markers(screen, regions["inventory_items"], item_2_settings),
                                config,
                                2,
                            )
                            if retry_targets:
                                click_marker(mouse, retry_targets[0], item_names[1], args, args.dry_run)
                                wait_ticks("before retry confirmation", args.after_confirm_ticks, args, args.dry_run)
                                if not args.dry_run:
                                    pyautogui.press("space")
                                    print("retry confirmation: pressed space")
                                wait_ticks("retry mixing", args.mixing_ticks, args, args.dry_run)
                                refreshed_bank_markers = capture_color_markers(
                                    screen, regions["game_bank_markers"], bank_marker_settings
                                )
                                if not refreshed_bank_markers:
                                    stop_keys.request_stop("bank marker disappeared during mix retry")
                                    continue
                                bank_markers = refreshed_bank_markers
                                bank_point = marker_click_point(bank_markers[0], args.click_scale, 0)
                                hover_action = read_hover_action(mouse, bank_point, regions)
                                print(f"bank pre-click hover after retry: {hover_action!r}")
                        if not bank_hover_is_safe(hover_action):
                            stop_keys.request_stop(
                                "bank click blocked because hover was not a clean 'Bank Bank booth' action"
                            )
                            continue
                    click_marker(mouse, bank_markers[0], "bank", args, args.dry_run)
                    wait_ticks("bank opening", args.after_bank_click_ticks, args, args.dry_run)
                    bank_status = detect_bank_status(state, templates["deposit_all"], args.click_timeout)
                    if not bank_status.is_open:
                        print(
                            "bank: click sent, but interface is still closed; "
                            f"best deposit_all={bank_status.deposit_all_score:.3f}; retrying"
                        )
                        continue

                refill_open_bank(
                    state, mouse, config, window, regions, templates,
                    item_names, bank_points, args,
                )
                completed += 1
                print(f"herblore round complete: {completed}")
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def main() -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None)
    known, _ = pre_parser.parse_known_args()
    try:
        config = load_json_config(known.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    platform_value = resolve_platform(value_from_config(config, "platform", "auto"))
    templates_dir = platform_template_dir(
        value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value
    )
    parser = argparse.ArgumentParser(description="Ranarr potion-unfinished herblore automation.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    parser.add_argument("--bank-item-1", default=value_from_config(config, "bank_item_1_name", "Ranarr weed"))
    parser.add_argument("--bank-item-2", default=value_from_config(config, "bank_item_2_name", "Vial of water"))
    parser.add_argument(
        "--calibrate-position",
        action="store_true",
        help="Ignore saved X/Y and hover-calibrate both bank items on the first open bank.",
    )
    for name in (
        "mixing_ticks", "after_item_ticks", "after_confirm_ticks", "after_bank_click_ticks",
        "after_deposit_ticks", "after_withdraw_ticks", "after_bank_close_ticks", "no_items_wait_ticks",
    ):
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
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        return run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
