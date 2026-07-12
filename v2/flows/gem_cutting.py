from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from core.screen import ScreenCapture
from core.terminal import install_timestamped_print
from v2.actions import StopKeys, build_mouse, humanized_delay, match_click_coordinates
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.game_states.bank import detect_bank_status
from v2.game_states.gem_cutting import GemCuttingState
from v2.game_states.template_matching import parse_scales
from v2.game_states.template_state import TemplateState
from v2.platforming import add_platform_argument, platform_template_dir, resolve_platform
from v2.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "gem_cutting"
TEMPLATE_NAMES = (
    "bank",
    "deposit_all",
    "bank_close",
    "gem_blue",
    "gem_red",
    "gem_green",
    "bank_gem_blue",
    "bank_gem_red",
    "chisel",
    "cut_confirmation_blue",
    "cut_confirmation_red",
    "cut_confirmation_green",
    "empty_inventory_slot",
)
GEM_NAMES = ("gem_blue", "gem_red", "gem_green")


@dataclass(frozen=True)
class Defaults:
    templates_dir: Path = ROOT / "templates" / SCRIPT_NAME
    template_scales: str = "0.5"
    threshold: float = 0.82
    poll_seconds: float = 0.12
    tick_seconds: float = 0.6
    click_timeout: float = 3.0
    action_ticks: float = 1.0
    cutting_ticks: float = 52.0
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
DEFAULT_CONFIG_PATH = ROOT / "config" / "gem_cutting.example.json"


def build_templates(
    templates_dir: Path,
    threshold: float,
    default_scales: list[float],
    config: dict[str, Any],
) -> dict[str, TemplateState]:
    return build_template_states(TEMPLATE_NAMES, templates_dir, threshold, default_scales, config)


def click_match(mouse, template: TemplateState, match, scale: float, args, dry_run: bool) -> None:
    x, y = match_click_coordinates(match, args.click_scale, args.spot_jitter)
    x += template.click_offset[0]
    y += template.click_offset[1]
    if dry_run:
        print(f"{template.name}: found score={match.score:.3f}, scale={scale:g}, would click=({x},{y})")
        return
    if args.pre_click_jitter > 0:
        time.sleep(random.uniform(0.0, args.pre_click_jitter))
    mouse.click(x, y)
    print(f"{template.name}: clicked score={match.score:.3f}, scale={scale:g}, at=({x},{y})")


def find_and_click(state, mouse, template: TemplateState, args, dry_run: bool) -> bool:
    match, score, scale = state.find(template, args.click_timeout)
    if match is None:
        print(f"{template.name}: not found; best={score:.3f}, threshold={template.threshold:.3f}")
        return False
    click_match(mouse, template, match, scale, args, dry_run)
    return True


def wait_action(label: str, args, dry_run: bool) -> None:
    delay = humanized_delay(args.action_ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def wait_for_cutting(args, dry_run: bool) -> None:
    delay = humanized_delay(args.cutting_ticks * args.tick_seconds, args.time_jitter)
    print(f"cutting: waiting {args.cutting_ticks:g} full ticks, {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def selected_gem_templates(templates: dict[str, TemplateState], gem_name: str) -> list[TemplateState]:
    if gem_name == "auto":
        return [templates[name] for name in GEM_NAMES if templates[name].path.exists()]
    return [templates[f"gem_{gem_name}"]]


def selected_bank_gem_templates(
    templates: dict[str, TemplateState],
    gem_name: str,
    inventory_gems: list[TemplateState],
) -> list[TemplateState]:
    names = GEM_NAMES if gem_name == "auto" else (f"gem_{gem_name}",)
    selected: list[TemplateState] = []
    for name in names:
        bank_name = f"bank_{name}"
        bank_template = templates.get(bank_name)
        if bank_template is not None and bank_template.path.exists():
            selected.append(bank_template)
        else:
            selected.append(templates[name])
    return selected or inventory_gems


def confirmation_template(templates: dict[str, TemplateState], gem_name: str) -> TemplateState:
    selected = "green" if gem_name == "auto" else gem_name
    return templates[f"cut_confirmation_{selected}"]


def run_flow(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    args.templates_dir = platform_template_dir(args.templates_dir, config, args.platform)
    templates = build_templates(args.templates_dir, args.threshold, args.template_scales, config)
    gems = selected_gem_templates(templates, args.gem)
    bank_gems = selected_bank_gem_templates(templates, args.gem, gems)
    confirmation = confirmation_template(templates, args.gem)
    required = [
        templates["bank"],
        templates["deposit_all"],
        templates["bank_close"],
        templates["chisel"],
        confirmation,
        *bank_gems,
        *gems,
    ]
    missing = [template.path for template in required if not template.path.exists()]
    if missing:
        print("Missing template image(s):")
        for path in missing:
            print(f"  {path}")
        return 1

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"{mode}: gem cutting; gem={args.gem}; loops={'until stopped' if args.loops <= 0 else args.loops}")
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
            state = GemCuttingState(screen, args.monitor, args.poll_seconds, stop_keys)
            completed = 0
            attempt = 0
            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                attempt += 1
                target = "continuous" if args.loops <= 0 else str(args.loops)
                print(f"Gem round attempt {attempt}; completed={completed}/{target}")
                bank_status = detect_bank_status(state, templates["deposit_all"], 0.0)
                if bank_status.is_open:
                    print("bank: already open")
                else:
                    if not find_and_click(state, mouse, templates["bank"], args, args.dry_run):
                        continue
                    wait_action("bank opening", args, args.dry_run)
                if not find_and_click(state, mouse, templates["deposit_all"], args, args.dry_run):
                    continue
                wait_action("after deposit all", args, args.dry_run)

                gem_template, gem_match, gem_score, gem_scale = state.first_present(bank_gems, args.click_timeout)
                if gem_template is None or gem_match is None:
                    print(f"bank gem: not found; best={gem_score:.3f}; bank is empty, stopping")
                    break
                click_match(mouse, gem_template, gem_match, gem_scale, args, args.dry_run)
                wait_action("after gem withdrawal", args, args.dry_run)
                if not find_and_click(state, mouse, templates["bank_close"], args, args.dry_run):
                    continue
                wait_action("after bank close", args, args.dry_run)
                if not find_and_click(state, mouse, templates["chisel"], args, args.dry_run):
                    continue

                inventory_gem, inventory_match, inventory_score, inventory_scale = state.first_present(gems, args.click_timeout)
                if inventory_gem is None or inventory_match is None:
                    print(f"inventory gem: not found; best={inventory_score:.3f}")
                    continue
                click_match(mouse, inventory_gem, inventory_match, inventory_scale, args, args.dry_run)
                if not find_and_click(state, mouse, confirmation, args, args.dry_run):
                    print("cut confirmation: expected gem icon did not appear")
                    continue
                print("cut confirmation: gem icon clicked")

                # A shrinking stack becomes visually harder to match near the end.
                # Never use that temporary score drop as permission to bank early.
                wait_for_cutting(args, args.dry_run)

                if args.dry_run:
                    print("gem inventory poll: would verify absence after the full cutting wait")
                    completed += 1
                    continue
                while not stop_keys.stop_requested:
                    found_template, found_match, score, _scale = state.first_present(gems, 0.0)
                    if found_match is None:
                        completed += 1
                        print(
                            f"uncut gem: no longer found; best={score:.3f}; "
                            f"completed round {completed}/{target}"
                        )
                        break
                    print(f"uncut gem: {found_template.name} still present; score={score:.3f}")
                    time.sleep(max(0.01, args.tick_seconds))
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(args, config: dict[str, Any]) -> int:
    config, window = resolve_regions(config)
    args.templates_dir = platform_template_dir(args.templates_dir, config, args.platform)
    templates = build_templates(args.templates_dir, args.threshold, args.template_scales, config)
    stop_keys = StopKeys()
    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture()
        print(f"Monitor {args.monitor}: left={frame.left}, top={frame.top}, width={frame.width}, height={frame.height}")
        if window:
            print(f"RuneLite window: left={window['left']}, top={window['top']}, width={window['width']}, height={window['height']}")
        state = GemCuttingState(screen, args.monitor, args.poll_seconds, stop_keys)
        for name, template in templates.items():
            if not template.path.exists():
                print(f"{name}: missing at {template.path}")
                continue
            match, score, scale = state.find(template, 0.05)
            status = "found" if match is not None else "NOT found"
            detail = f", center={match.center}, rect=({match.x},{match.y},{match.width},{match.height})" if match else ""
            print(f"{name}: {status} best={score:.3f}, threshold={template.threshold:.3f}, scale={scale:g}{detail}")
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
    parser = argparse.ArgumentParser(description="Bank, withdraw, cut, and monitor uncut gems.", parents=[pre])
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir)
    parser.add_argument("--gem", choices=("auto", "blue", "red", "green"), default=value_from_config(config, "gem", "green"))
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales))
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold))
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds))
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds))
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout))
    parser.add_argument("--action-ticks", type=float, default=value_from_config(config, "action_ticks", DEFAULTS.action_ticks))
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
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.template_scales = parse_scales(str(args.template_scales))
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (ValueError, FileNotFoundError) as exc:
        print(exc)
        return 1
