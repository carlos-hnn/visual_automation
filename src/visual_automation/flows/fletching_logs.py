from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay, wait_ticks
from visual_automation.actions.bank import BankActions
from visual_automation.actions.templates import TemplateActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.keyboard import KeyboardController
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.core.vision import TemplateMatch
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import (
    best_color_marker,
    marker_click_point,
    marker_settings_from_config,
)
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateState
from visual_automation.game_states.woodcut_firemake import WoodcutFiremakeState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "fletching_logs"
TEMPLATE_NAMES = (
    "deposit_all",
    "bank_maple_log",
    "bank_close",
    "knife",
    "fletching_status_icon",
)


@dataclass(frozen=True)
class Defaults:
    templates_dir: Path = ROOT / "templates" / SCRIPT_NAME
    template_scales: str = "0.5"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    bank_open_ticks: float = 3.0
    after_deposit_ticks: float = 1.0
    after_withdraw_ticks: float = 1.0
    after_bank_close_ticks: float = 1.0
    choice_timeout_ticks: float = 3.0
    choice_poll_seconds: float = 0.12
    fletching_ticks: float = 80.0
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
DEFAULT_CONFIG_PATH = ROOT / "config" / "fletching_logs.example.json"


def build_templates(
    templates_dir: Path,
    threshold: float,
    default_scales: list[float],
    config: dict[str, Any],
) -> dict[str, TemplateState]:
    return build_template_states(TEMPLATE_NAMES, templates_dir, threshold, default_scales, config)



def click_cyan_marker(screen, mouse, region: dict[str, int], label: str, args, config: dict[str, Any], dry_run: bool) -> bool:
    match = best_color_marker(screen.capture(region), marker_settings_from_config(config))
    if match is None:
        print(f"{label}: no cyan marker found")
        return False
    x, y = marker_click_point(match, args.click_scale, args.spot_jitter)
    if dry_run:
        print(f"{label}: cyan score={match.score:.0f}, would click=({x},{y}), rect=({match.x},{match.y},{match.width},{match.height})")
        return True
    mouse.click(x, y)
    print(f"{label}: clicked cyan score={match.score:.0f}, at=({x},{y})")
    return True



def region_changed(before: np.ndarray, after: np.ndarray, threshold: float) -> tuple[bool, float]:
    diff = cv2.absdiff(before, after)
    score = float(np.mean(diff))
    return score >= threshold, score


def wait_for_chat_choice(screen, chat_region: dict[str, int], before: np.ndarray, args, dry_run: bool) -> bool:
    timeout = max(0.0, args.choice_timeout_ticks * args.tick_seconds)
    if dry_run:
        print(f"chat choice: would wait up to {args.choice_timeout_ticks:g} ticks")
        return True
    deadline = time.monotonic() + timeout
    best_score = 0.0
    while time.monotonic() < deadline:
        after = screen.capture(chat_region).image
        changed, score = region_changed(before, after, args.chat_change_threshold)
        best_score = max(best_score, score)
        if changed:
            print(f"chat choice: detected change score={score:.2f}")
            return True
        time.sleep(max(0.01, args.choice_poll_seconds))
    print(f"chat choice: no change detected; best={best_score:.2f}; pressing space anyway")
    return False


def run_flow(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    args.templates_dir = platform_template_dir(args.templates_dir, config, args.platform)
    templates = build_templates(args.templates_dir, args.threshold, args.template_scales, config)
    required = [
        templates["deposit_all"],
        templates["bank_maple_log"],
        templates["bank_close"],
        templates["knife"],
    ]
    missing = [template.path for template in required if not template.path.exists()]
    if missing:
        print("Missing template image(s):")
        for path in missing:
            print(f"  {path}")
        return 1

    regions = value_from_config(config, "regions", {})
    if not isinstance(regions, dict):
        raise ValueError("config regions must be a mapping")
    for name in ("bank_marker", "inventory_cyan_log", "chat"):
        if name not in regions:
            raise ValueError(f"Missing region: {name}")

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"{mode}: fletching logs; loops={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    keyboard = KeyboardController()
    pyautogui.FAILSAFE = False
    stop_keys.start()
    try:
        with ScreenCapture(monitor=args.monitor) as screen:
            state = WoodcutFiremakeState(screen, args.monitor, args.poll_seconds, stop_keys)
            clicks = TemplateActions(state, mouse, args, args.dry_run)
            bank = BankActions(templates, clicks)
            completed = 0
            attempt = 0
            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                attempt += 1
                target = "continuous" if args.loops <= 0 else str(args.loops)
                print(f"Fletching round {attempt}; completed={completed}/{target}")

                bank_status = bank.status()
                if bank_status.is_open:
                    print("bank: already open")
                else:
                    if not click_cyan_marker(screen, mouse, regions["bank_marker"], "bank", args, config, args.dry_run):
                        continue
                    wait_ticks("bank opening", args.bank_open_ticks, args, args.dry_run)

                if not bank.deposit_all():
                    continue
                wait_ticks("after deposit all", args.after_deposit_ticks, args, args.dry_run)

                if not bank.withdraw("bank_maple_log"):
                    continue
                wait_ticks("after maple logs", args.after_withdraw_ticks, args, args.dry_run)

                if not bank.close():
                    continue
                wait_ticks("after bank close", args.after_bank_close_ticks, args, args.dry_run)

                before_chat = screen.capture(regions["chat"]).image
                if not clicks.find_and_click(templates["knife"]):
                    continue
                if not click_cyan_marker(screen, mouse, regions["inventory_cyan_log"], "inventory log", args, config, args.dry_run):
                    continue
                wait_for_chat_choice(screen, regions["chat"], before_chat, args, args.dry_run)
                if args.dry_run:
                    print("space: would press")
                else:
                    keyboard.press("space")
                    print("space: pressed")

                wait_ticks("fletching", args.fletching_ticks, args, args.dry_run)
                completed += 1
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    args.templates_dir = platform_template_dir(args.templates_dir, config, args.platform)
    templates = build_templates(args.templates_dir, args.threshold, args.template_scales, config)
    regions = value_from_config(config, "regions", {})
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture()
        print(f"Monitor {args.monitor}: left={frame.left}, top={frame.top}, width={frame.width}, height={frame.height}")
        if window:
            print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
        state = WoodcutFiremakeState(screen, args.monitor, args.poll_seconds, stop_keys)
        for template in templates.values():
            if not template.path.exists():
                print(f"{template.name}: missing at {template.path}")
                continue
            match, score, scale = state.find(template, 0.05)
            status = "found" if match is not None else "NOT found"
            detail = f", center={match.center}, rect=({match.x},{match.y},{match.width},{match.height})" if match else ""
            print(f"{template.name}: {status} best={score:.3f}, threshold={template.threshold:.3f}, scale={scale:g}{detail}")
        if isinstance(regions, dict):
            for label in ("bank_marker", "inventory_cyan_log"):
                region = regions.get(label)
                if isinstance(region, dict):
                    click_cyan_marker(screen, None, region, label, args, config, True)
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

    parser = argparse.ArgumentParser(description="Bank maple logs and fletch one inventory into bows.", parents=[pre])
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    parser.add_argument("--bank-open-ticks", type=float, default=value_from_config(config, "bank_open_ticks", DEFAULTS.bank_open_ticks))
    parser.add_argument("--after-deposit-ticks", type=float, default=value_from_config(config, "after_deposit_ticks", DEFAULTS.after_deposit_ticks))
    parser.add_argument("--after-withdraw-ticks", type=float, default=value_from_config(config, "after_withdraw_ticks", DEFAULTS.after_withdraw_ticks))
    parser.add_argument("--after-bank-close-ticks", type=float, default=value_from_config(config, "after_bank_close_ticks", DEFAULTS.after_bank_close_ticks))
    parser.add_argument("--choice-timeout-ticks", type=float, default=value_from_config(config, "choice_timeout_ticks", DEFAULTS.choice_timeout_ticks))
    parser.add_argument("--choice-poll-seconds", type=float, default=value_from_config(config, "choice_poll_seconds", DEFAULTS.choice_poll_seconds))
    parser.add_argument("--chat-change-threshold", type=float, default=value_from_config(config, "chat_change_threshold", 4.0))
    parser.add_argument("--fletching-ticks", type=float, default=value_from_config(config, "fletching_ticks", DEFAULTS.fletching_ticks))
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
        args.template_scales = parse_scales(str(args.template_scales))
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
