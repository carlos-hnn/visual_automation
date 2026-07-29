from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay
from visual_automation.actions.bank import BankActions
from visual_automation.actions.templates import TemplateActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateState
from visual_automation.game_states.woodcut_firemake import WoodcutFiremakeState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states

install_timestamped_print()

SCRIPT_NAME = "steel_cannonball"
TEMPLATE_NAMES = (
    "casting",
    "bank",
    "deposit_all",
    "bank_close",
    "bar_in_bank",
    "steel_bars",
    "furnace",
    "confirm",
)


@dataclass(frozen=True)
class SteelCannonballDefaults:
    templates_dir: Path = ROOT / "templates" / SCRIPT_NAME
    template_scales: str = "0.5"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    action_ticks: float = 1.0
    deposit_load_seconds: float = 1.2
    cutting_ticks: float = 270.0
    time_jitter: float = 0.06
    pre_click_jitter: float = 0.04
    spot_jitter: int = 3
    click_scale: float = 1.0
    countdown: float = 2.0
    monitor: int = 1
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    dry_run: bool = True


DEFAULTS = SteelCannonballDefaults()
DEFAULT_CONFIG_PATH = ROOT / "config" / "steel_cannonball.example.json"


def build_templates(
    templates_dir: Path,
    threshold: float,
    default_scales: list[float],
    config: dict[str, Any],
) -> dict[str, TemplateState]:
    return build_template_states(TEMPLATE_NAMES, templates_dir, threshold, default_scales, config)



def wait_action(label: str, args, dry_run: bool) -> None:
    delay = humanized_delay(args.action_ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def wait_for_casting(args, dry_run: bool) -> None:
    delay = humanized_delay(args.cutting_ticks * args.tick_seconds, args.time_jitter)
    print(f"casting: waiting {args.cutting_ticks:g} full ticks, {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def wait_after_deposit(args, dry_run: bool) -> None:
    delay = max(0.0, args.deposit_load_seconds)
    print(f"after deposit all: waiting {delay:.2f}s for bank inventory to load")
    if not dry_run:
        time.sleep(delay)


def click_with_retries(
    clicks: TemplateActions,
    template: TemplateState,
    args,
    dry_run: bool,
    retries: int = 2,
    retry_delay: float = 1.0,
) -> bool:
    for attempt in range(1, retries + 1):
        if clicks.find_and_click(template):
            return True
        print(f"{template.name}: attempt {attempt}/{retries} failed")
        if attempt < retries and not dry_run:
            time.sleep(retry_delay)
    return False


def run_flow(args, config: dict[str, Any]) -> int:
    config, window = config, None
    templates_dir = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, args.platform)
    templates = build_templates(templates_dir, args.threshold, args.template_scales, config)
    required = [templates[name] for name in TEMPLATE_NAMES]
    missing = [template.path for template in required if not template.path.exists()]
    if missing:
        print("Missing template image(s):")
        for path in missing:
            print(f"  {path}")
        return 1

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"{mode}: steel cannonball furnace flow; loops={'until stopped' if args.loops <= 0 else args.loops}")
    if window:
        print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
    print("Stop with Esc or Cmd+Shift+Q.")
    time.sleep(max(0.0, args.countdown))

    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
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
                print(f"Steel cannonball round {attempt}; completed={completed}/{target}")

                if state.exists(templates["casting"], 0.0):
                    print("casting: still active")
                    wait_action("casting active", args, args.dry_run)
                    continue

                bank_was_open = bank.status().is_open
                if not bank.open():
                    continue
                if not bank_was_open:
                    print("bank: clicked")
                    if not args.dry_run:
                        time.sleep(4.0)

                if not bank.deposit_all():
                    continue
                wait_action("after deposit all", args, args.dry_run)
                wait_after_deposit(args, args.dry_run)

                if not click_with_retries(clicks, templates["bar_in_bank"], args, args.dry_run, retries=3, retry_delay=1.0):
                    print("bar_in_bank: failed to select after retries")
                    continue
                wait_action("after steel bars", args, args.dry_run)

                if not bank.close():
                    continue
                wait_action("after bank close", args, args.dry_run)

                if not clicks.find_and_click(templates["furnace"]):
                    continue
                if not args.dry_run:
                    time.sleep(4.0)

                if not clicks.find_and_click(templates["confirm"]):
                    print("confirm: not found; retrying next loop")
                    continue
                print("confirm: clicked")

                wait_for_casting(args, args.dry_run)
                completed += 1

    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(args, config: dict[str, Any]) -> int:
    templates_dir = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, args.platform)
    templates = build_templates(templates_dir, args.threshold, args.template_scales, config)
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        state = WoodcutFiremakeState(screen, args.monitor, args.poll_seconds, stop_keys)
        for template in templates.values():
            match, score, scale = state.find(template, 0.5)
            print(
                f"{template.name}: {'found' if match is not None else 'NOT found'} "
                f"score={score:.3f} threshold={template.threshold:.3f} scale={scale:g} scales={template.scales}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None)
    known, _remaining = parser.parse_known_args()
    try:
        config = load_json_config(known.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    platform_value = resolve_platform(value_from_config(config, "platform", "auto"))
    templates_dir = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value)

    parser = argparse.ArgumentParser(description="Steel cannonball furnace automation.")
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    parser.add_argument("--action-ticks", type=float, default=value_from_config(config, "action_ticks", DEFAULTS.action_ticks))
    parser.add_argument("--deposit-load-seconds", type=float, default=value_from_config(config, "deposit_load_seconds", DEFAULTS.deposit_load_seconds))
    parser.add_argument("--cutting-ticks", type=float, default=value_from_config(config, "cutting_ticks", DEFAULTS.cutting_ticks))
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter))
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown))
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 1))
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min))
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run))
    parser.add_argument("--calibrate", action="store_true", help="Verify all steel cannonball templates on screen.")
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.template_scales = parse_scales(str(args.template_scales))
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1
