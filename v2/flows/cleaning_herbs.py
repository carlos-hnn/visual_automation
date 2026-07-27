from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from core.screen import ScreenCapture
from core.safety import report_progress
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from v2.actions import StopKeys, build_mouse, humanized_delay
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.flows.herblore import bank_hover_is_safe, read_hover_action
from v2.flows.potion_fill import (
    bank_hover_slots,
    calibrate_bank_item_position,
    click_point,
    click_template,
    resolve_bank_point,
)
from v2.game_states.bank import detect_bank_status
from v2.game_states.color_markers import (
    capture_color_markers,
    marker_click_point,
    marker_settings_from_config,
    sorted_inventory_markers,
)
from v2.game_states.template_matching import parse_scales
from v2.game_states.template_state import TemplateMatcherState
from v2.platforming import add_platform_argument, platform_template_dir, resolve_platform
from v2.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "cleaning_herbs"
TEMPLATE_NAMES = ("deposit_all", "bank_close")
DEFAULT_CONFIG_PATH = ROOT / "config" / "cleaning_herbs.example.json"


@dataclass(frozen=True)
class Defaults:
    templates_dir: Path = ROOT / "templates" / "steel_cannonball"
    template_scales: str = "0.6"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    after_herb_click_ticks: float = 1.0
    after_bank_click_ticks: float = 5.0
    after_deposit_ticks: float = 2.0
    after_withdraw_ticks: float = 1.0
    after_bank_close_ticks: float = 1.0
    no_target_wait_ticks: float = 1.0
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


def wait_ticks(label: str, ticks: float, args, dry_run: bool) -> None:
    delay = humanized_delay(ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {ticks:g} tick(s), {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def herb_shaped_markers(markers: list[TemplateMatch], config: dict[str, Any]) -> list[TemplateMatch]:
    min_height = int(value_from_config(config, "herb_marker_shape_min_height", 30))
    max_width = int(value_from_config(config, "herb_marker_shape_max_width", 60))
    return [marker for marker in markers if marker.height >= min_height and marker.width <= max_width]


def bank_item_config_key(config: dict[str, Any], value: str) -> str:
    requested = value.strip().casefold()
    names = value_from_config(config, "bank_item_display_names", {})
    if isinstance(names, dict):
        for key, display_name in names.items():
            if str(display_name).strip().casefold() == requested:
                return str(key)
    return value.strip()


def click_marker(mouse, marker: TemplateMatch, label: str, args) -> None:
    x, y = marker_click_point(marker, args.click_scale, args.spot_jitter)
    if args.dry_run:
        print(f"{label}: would click=({x},{y}), pixels={marker.score:.0f}")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{label}: clicked=({x},{y}), pixels={marker.score:.0f}")


def prepare(config: dict[str, Any], args):
    config, window = resolve_regions(config)
    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("inventory_herbs", "game_bank_markers", "bank_hover_text"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")
    templates_dir = platform_template_dir(args.templates_dir, config, resolve_platform(args.platform))
    templates = build_template_states(
        TEMPLATE_NAMES, templates_dir, args.threshold, parse_scales(str(args.template_scales)), config
    )
    missing = [template.path for template in templates.values() if not template.path.exists()]
    if missing:
        raise FileNotFoundError("Missing template image(s): " + ", ".join(str(path) for path in missing))
    item_name = bank_item_config_key(config, str(args.bank_item_name))
    if not item_name:
        raise ValueError("bank_item_name cannot be empty")
    try:
        item_point = resolve_bank_point(config, item_name, window)
        point_was_saved = True
    except ValueError:
        item_point = bank_hover_slots(config, window)[0]
        point_was_saved = False
        print(f"bank item {item_name!r} has no saved X/Y; hover calibration will resolve it")
    return config, window, regions, templates, item_name, item_point, point_was_saved


def run_flow(args, config: dict[str, Any]) -> int:
    config, window, regions, templates, item_name, item_point, point_was_saved = prepare(config, args)
    herb_settings = marker_settings_from_config(config, "herb_marker")
    bank_settings = marker_settings_from_config(config, "bank_marker")
    print(
        f"{'DRY RUN' if args.dry_run else 'LIVE'}: cleaning herbs; "
        f"loads={'until stopped' if args.loops <= 0 else args.loops}; bank_item={item_name}"
    )
    if point_was_saved:
        print(f"bank position already saved at={item_point}; hover calibration skipped")
    if window:
        print(
            f"RuneLite window: left={window['left']}, top={window['top']}, "
            f"width={window['width']}, height={window['height']}"
        )
    print("Stop with global Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    pyautogui.FAILSAFE = False
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            completed = 0
            loaded_inventory = False
            position_calibrated = point_was_saved
            initial_bank_load_pending = True
            bank_open = detect_bank_status(state, templates["deposit_all"], 0.0).is_open
            if not bank_open:
                # A closed bank is the expected state while cleaning, not a failed target search.
                report_progress("template:deposit_all.png")
            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                if bank_open:
                    if not position_calibrated:
                        item_point = calibrate_bank_item_position(
                            mouse, config, window, regions, item_name, args
                        )
                        position_calibrated = True
                    if stop_keys.stop_requested:
                        break
                    if not click_template(state, mouse, templates["deposit_all"], args, args.dry_run):
                        continue
                    wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)
                    if loaded_inventory:
                        completed += 1
                        loaded_inventory = False
                        print(f"cleaned herb load deposited: {completed}")
                        if args.loops > 0 and completed >= args.loops:
                            if not click_template(state, mouse, templates["bank_close"], args, args.dry_run):
                                continue
                            wait_ticks("after final bank close", args.after_bank_close_ticks, args, args.dry_run)
                            bank_open = False
                            break
                    if stop_keys.stop_requested:
                        break
                    click_point(mouse, item_point, f"bank {item_name}", args, args.dry_run)
                    wait_ticks("after herb withdrawal", args.after_withdraw_ticks, args, args.dry_run)
                    withdrawal_deadline = time.monotonic() + float(
                        value_from_config(config, "withdraw_confirmation_timeout_seconds", 5.0)
                    )
                    withdrawn_markers: list[TemplateMatch] = []
                    while not stop_keys.stop_requested and time.monotonic() < withdrawal_deadline:
                        withdrawn_markers = herb_shaped_markers(
                            capture_color_markers(screen, regions["inventory_herbs"], herb_settings), config
                        )
                        if withdrawn_markers:
                            break
                        time.sleep(max(0.05, args.poll_seconds))
                    if not withdrawn_markers:
                        stop_keys.request_stop(
                            f"withdrawal of {item_name} was not confirmed in the inventory"
                        )
                        continue
                    print(f"withdrawal confirmed: green_herb_markers={len(withdrawn_markers)}")
                    if stop_keys.stop_requested:
                        break
                    if not click_template(state, mouse, templates["bank_close"], args, args.dry_run):
                        continue
                    wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)
                    bank_open = False
                    loaded_inventory = True
                    initial_bank_load_pending = False
                    print("grimy herb inventory loaded")
                    continue

                if not initial_bank_load_pending:
                    markers = sorted_inventory_markers(
                        herb_shaped_markers(
                            capture_color_markers(screen, regions["inventory_herbs"], herb_settings), config
                        )
                    )
                    print(f"inventory: green_herb_markers={len(markers)}")
                    if markers:
                        click_marker(mouse, markers[0], "herb", args)
                        wait_ticks("after herb click", args.after_herb_click_ticks, args, args.dry_run)
                        continue
                else:
                    print("startup: opening bank before processing inventory")

                bank_markers = capture_color_markers(screen, regions["game_bank_markers"], bank_settings)
                if not bank_markers:
                    wait_ticks("bank marker unavailable", args.no_target_wait_ticks, args, args.dry_run)
                    continue
                bank_point = marker_click_point(bank_markers[0], args.click_scale, 0)
                hover_action = read_hover_action(mouse, bank_point, regions)
                print(f"bank pre-click hover: {hover_action!r}")
                if not bank_hover_is_safe(hover_action):
                    stop_keys.request_stop("bank click blocked because hover was not a clean Bank booth action")
                    continue
                click_marker(mouse, bank_markers[0], "bank", args)
                wait_ticks("bank opening", args.after_bank_click_ticks, args, args.dry_run)
                bank_status = detect_bank_status(state, templates["deposit_all"], args.click_timeout)
                if not bank_status.is_open:
                    print(
                        "bank: interface is still closed; "
                        f"best deposit_all={bank_status.deposit_all_score:.3f}; retrying"
                    )
                    report_progress("template:deposit_all.png")
                    bank_open = False
                else:
                    bank_open = True
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
    templates_dir = platform_template_dir(
        value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value
    )
    parser = argparse.ArgumentParser(description="Green-tagged grimy herb cleaning automation.")
    parser.add_argument("--config", type=Path, default=known.config)
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    parser.add_argument("--bank-item-name", default=value_from_config(config, "bank_item_name", "grimy_kwuarm"))
    for name in (
        "after_herb_click_ticks", "after_bank_click_ticks", "after_deposit_ticks",
        "after_withdraw_ticks", "after_bank_close_ticks", "no_target_wait_ticks",
    ):
        parser.add_argument(
            f"--{name.replace('_', '-')}", type=float,
            default=value_from_config(config, name, getattr(DEFAULTS, name)),
        )
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown))
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 0))
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
