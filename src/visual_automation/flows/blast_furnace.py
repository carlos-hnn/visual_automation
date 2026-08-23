from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse
from visual_automation.actions.markers import select_marker
from visual_automation.actions.templates import TemplateActions
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.color_markers import capture_color_markers, marker_settings_from_config
from visual_automation.game_states.inventory import detect_inventory_grid_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateMatcherState, TemplateState
from visual_automation.platforming import add_platform_argument, resolve_platform
from visual_automation.runtime import click_color_marker, relative_region, wait_for_state
from visual_automation.template_config import find_window_bounds

install_timestamped_print()

SCRIPT_NAME = "blast_furnace"
DEFAULT_CONFIG_PATH = ROOT / "config" / "blast_furnace.example.json"


@dataclass(frozen=True)
class Defaults:
    monitor: int = 1
    poll_seconds: float = 0.5
    retry_seconds: float = 1.0
    state_timeout: float = 120.0
    threshold: float = 0.82
    click_timeout: float = 3.0
    pre_click_jitter: float = 0.04
    template_scales: str = "0.5"
    countdown: float = 2.0
    first_item_clicks: int = 2
    second_item_clicks: int = 1
    loads_before_collection: int = 3
    empty_slots_required: int = 23
    inventory_first_x: int = 44
    inventory_first_y: int = 37
    inventory_column_spacing: float = 44.0
    inventory_row_spacing: float = 38.5
    inventory_patch_radius: int = 13
    inventory_occupied_std: float = 8.0
    item_click_gap: float = 0.14
    item_click_jitter: float = 0.05
    prompt_delay: float = 1.0
    spot_jitter: int = 3
    click_scale: float = 1.0
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32
    dry_run: bool = True


DEFAULTS = Defaults()


def inventory_status(screen: ScreenCapture, region: dict[str, int], args):
    return detect_inventory_grid_status(
        screen,
        region,
        args.empty_slots_required,
        (args.inventory_first_x, args.inventory_first_y),
        (args.inventory_column_spacing, args.inventory_row_spacing),
        patch_radius=args.inventory_patch_radius,
        occupied_std_threshold=args.inventory_occupied_std,
    )


def template(name: str, path: Path, args, region: dict[str, int] | None = None) -> TemplateState:
    return TemplateState(name, path, args.threshold, tuple(args.template_scales), region, (0, 0))


def click_bank_items(window, mouse, args) -> None:
    points = (
        (window["left"] + args.first_item_x, window["top"] + args.first_item_y, args.first_item_clicks, "first"),
        (window["left"] + args.second_item_x, window["top"] + args.second_item_y, args.second_item_clicks, "second"),
    )
    for x, y, count, label in points:
        for number in range(max(0, count)):
            print(f"bank {label} item: click {number + 1}/{count} near ({x}, {y})")
            if not args.dry_run:
                mouse.click_point(x, y, tolerance_pixels=args.spot_jitter)
                delay = max(0.0, args.item_click_gap + random.uniform(-args.item_click_jitter, args.item_click_jitter))
                time.sleep(delay)


def run_calibration(args, config: dict[str, Any]) -> int:
    window = find_window_bounds(str(value_from_config(config, "window_title", "RuneLite")))
    if window is None:
        print("RuneLite window not found.")
        return 1
    raw_regions = value_from_config(config, "regions", {})
    regions = {name: relative_region(window, raw_regions[name]) for name in ("game", "inventory", "bank", "chat")}
    deposit_all = template(
        "deposit_all",
        ROOT
        / value_from_config(
            config,
            "deposit_all_template",
            "templates/shared/bank/deposit_all.png",
        ),
        args,
        regions["bank"],
    )
    collector_prompt = template(
        "collector_prompt",
        ROOT / value_from_config(config, "collector_prompt_template", "templates/blast_furnace/collector_prompt.png"),
        args,
        regions["chat"],
    )
    stop_keys = StopKeys()
    with ScreenCapture(args.monitor) as screen:
        state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
        bank_match, bank_score, _ = state.find(deposit_all, 0.0)
        slots = inventory_status(screen, regions["inventory"], args)
        print(f"window: {window}")
        print(f"bank open: {bank_match is not None}; deposit_all score={bank_score:.3f}")
        print(f"empty inventory slots visible: {slots.empty_slots}; required={slots.empty_required}")
        prompt_match, prompt_score, _ = state.find(collector_prompt, 0.0)
        print(f"collector prompt: {prompt_match is not None}; score={prompt_score:.3f}")
        for name in ("bank", "furnace", "collector"):
            settings = marker_settings_from_config(config, f"{name}_marker")
            markers = capture_color_markers(screen, regions["game"], settings)
            match = select_marker(markers, regions["game"])
            print(f"{name} marker: {match.center if match else 'not visible'}")
    return 0


def run_flow(args, config: dict[str, Any]) -> int:
    window = find_window_bounds(str(value_from_config(config, "window_title", "RuneLite")))
    if window is None:
        print("RuneLite window not found.")
        return 1

    raw_regions = value_from_config(config, "regions", {})
    game_region = relative_region(window, raw_regions["game"])
    inventory_region = relative_region(window, raw_regions["inventory"])
    bank_region = relative_region(window, raw_regions["bank"])
    chat_region = relative_region(window, raw_regions["chat"])
    templates = {
        "deposit_all": template(
            "deposit_all",
            ROOT / value_from_config(config, "deposit_all_template", "templates/shared/bank/deposit_all.png"),
            args,
            bank_region,
        ),
        "bank_close": template(
            "bank_close",
            ROOT / value_from_config(config, "bank_close_template", "templates/shared/bank/bank_close.png"),
            args,
            bank_region,
        ),
        "collector_prompt": template(
            "collector_prompt",
            ROOT
            / value_from_config(
                config,
                "collector_prompt_template",
                "templates/blast_furnace/collector_prompt.png",
            ),
            args,
            chat_region,
        ),
    }
    missing = [item.path for item in templates.values() if not item.path.exists()]
    if missing:
        print("Missing templates: " + ", ".join(str(path) for path in missing))
        return 1

    print(
        f"{'DRY RUN' if args.dry_run else 'LIVE'}: Blast Furnace; {args.loads_before_collection} load(s) per collection"
    )
    print(f"RuneLite window: {window}; stop with Esc or Cmd+Shift+Q")
    time.sleep(max(0.0, args.countdown))
    stop_keys = StopKeys()
    mouse = build_mouse(args.move_duration_min, args.move_duration_max, spot_jitter_pixels=args.spot_jitter)
    pyautogui.FAILSAFE = False
    stop_keys.start()
    completed = 0
    try:
        with ScreenCapture(args.monitor) as screen:
            state = TemplateMatcherState(screen, args.monitor, args.poll_seconds, stop_keys)
            clicks = TemplateActions(state, mouse, args, args.dry_run)
            colors = {
                name: marker_settings_from_config(config, f"{name}_marker") for name in ("bank", "furnace", "collector")
            }

            def bank_open() -> bool:
                return state.exists(templates["deposit_all"], 0.0)

            def inventory_full() -> bool:
                status = inventory_status(screen, inventory_region, args)
                print(f"inventory: {status.empty_slots} empty slot(s) visible")
                return status.is_full

            def inventory_empty() -> bool:
                status = inventory_status(screen, inventory_region, args)
                print(f"inventory: {status.empty_slots}/{status.empty_required} empty slots required")
                return status.is_empty

            while not stop_keys.stop_requested and (args.loops <= 0 or completed < args.loops):
                print(f"Blast Furnace loop {completed + 1}")
                failed = False
                for load in range(1, args.loads_before_collection + 1):
                    print(f"Load {load}/{args.loads_before_collection}")
                    if not click_color_marker("bank", screen, game_region, colors["bank"], mouse, args, stop_keys):
                        failed = True
                        break
                    if not wait_for_state("bank open", bank_open, args, stop_keys):
                        failed = True
                        break
                    if not clicks.find_and_click(templates["deposit_all"]):
                        failed = True
                        break
                    click_bank_items(window, mouse, args)
                    if not wait_for_state("inventory full", inventory_full, args, stop_keys):
                        failed = True
                        break
                    if not clicks.find_and_click(templates["bank_close"]):
                        failed = True
                        break
                    if not wait_for_state("bank closed", lambda: not bank_open(), args, stop_keys):
                        failed = True
                        break
                    if not click_color_marker("furnace", screen, game_region, colors["furnace"], mouse, args, stop_keys):
                        failed = True
                        break
                    if not wait_for_state("inventory emptied at furnace", inventory_empty, args, stop_keys):
                        failed = True
                        break
                if failed:
                    print("Flow aborted in an intermediate state; restart manually after checking the game.")
                    return 1
                if not click_color_marker("collector", screen, game_region, colors["collector"], mouse, args, stop_keys):
                    return 1

                def prompt_visible() -> bool:
                    return state.exists(templates["collector_prompt"], 0.0)

                if not wait_for_state("collector prompt", prompt_visible, args, stop_keys):
                    return 1
                if not args.dry_run:
                    time.sleep(args.prompt_delay)
                    pyautogui.press("space")
                print("collector confirmation: Space")
                if not wait_for_state("collector prompt closed", lambda: not prompt_visible(), args, stop_keys):
                    return 1
                if not wait_for_state("bars collected / inventory full", inventory_full, args, stop_keys):
                    return 1
                completed += 1
    finally:
        stop_keys.stop()
    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    known, _ = parser.parse_known_args()
    try:
        config = load_json_config(known.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1
    parser = argparse.ArgumentParser(description="Blast Furnace automation")
    add_platform_argument(parser, config)
    parser.add_argument("--config", type=Path, default=known.config)
    for name, kind, default in (
        ("monitor", int, DEFAULTS.monitor),
        ("poll-seconds", float, DEFAULTS.poll_seconds),
        ("retry-seconds", float, DEFAULTS.retry_seconds),
        ("state-timeout", float, DEFAULTS.state_timeout),
        ("threshold", float, DEFAULTS.threshold),
        ("countdown", float, DEFAULTS.countdown),
        ("click-timeout", float, DEFAULTS.click_timeout),
        ("pre-click-jitter", float, DEFAULTS.pre_click_jitter),
        ("first-item-clicks", int, DEFAULTS.first_item_clicks),
        ("second-item-clicks", int, DEFAULTS.second_item_clicks),
        ("loads-before-collection", int, DEFAULTS.loads_before_collection),
        ("first-item-x", int, 106),
        ("first-item-y", int, 129),
        ("second-item-x", int, 151),
        ("second-item-y", int, 129),
        ("empty-slots-required", int, DEFAULTS.empty_slots_required),
        ("inventory-first-x", int, DEFAULTS.inventory_first_x),
        ("inventory-first-y", int, DEFAULTS.inventory_first_y),
        ("inventory-column-spacing", float, DEFAULTS.inventory_column_spacing),
        ("inventory-row-spacing", float, DEFAULTS.inventory_row_spacing),
        ("inventory-patch-radius", int, DEFAULTS.inventory_patch_radius),
        ("inventory-occupied-std", float, DEFAULTS.inventory_occupied_std),
        ("item-click-gap", float, DEFAULTS.item_click_gap),
        ("item-click-jitter", float, DEFAULTS.item_click_jitter),
        ("prompt-delay", float, DEFAULTS.prompt_delay),
        ("spot-jitter", int, DEFAULTS.spot_jitter),
        ("click-scale", float, DEFAULTS.click_scale),
        ("move-duration-min", float, DEFAULTS.move_duration_min),
        ("move-duration-max", float, DEFAULTS.move_duration_max),
        ("loops", int, 0),
    ):
        key = name.replace("-", "_")
        parser.add_argument(f"--{name}", type=kind, default=value_from_config(config, key, default))
    parser.add_argument(
        "--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales)
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=value_from_config(config, "dry_run", DEFAULTS.dry_run),
    )
    parser.add_argument(
        "--calibrate", action="store_true", help="Inspect templates, inventory slots, and markers without clicking."
    )
    args = parser.parse_args()
    try:
        args.platform = resolve_platform(args.platform)
        args.template_scales = parse_scales(str(args.template_scales))
        if args.loads_before_collection < 1:
            raise ValueError("loads_before_collection must be at least 1")
        return run_calibration(args, config) if args.calibrate else run_flow(args, config)
    except (KeyError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
