from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui

from visual_automation.actions import StopKeys, build_mouse, humanized_delay, match_click_coordinates
from visual_automation.config import load_json_config, value_from_config
from visual_automation.core.keyboard import KeyboardController
from visual_automation.core.screen import ScreenCapture
from visual_automation.core.terminal import install_timestamped_print
from visual_automation.definitions import ROOT
from visual_automation.game_states.inventory import detect_inventory_status
from visual_automation.game_states.template_matching import parse_scales
from visual_automation.game_states.template_state import TemplateState
from visual_automation.game_states.woodcut_firemake import WoodcutFiremakeState
from visual_automation.platforming import add_platform_argument, platform_template_dir, resolve_platform
from visual_automation.template_config import build_template_states, resolve_regions

install_timestamped_print()

SCRIPT_NAME = "woodcut_firemake"
TEMPLATE_NAMES = (
    "woodcutting_status",
    "empty_inventory_slot",
    "tree",
    "tree2",
    "tree3",
    "tree4",
    "tinderbox",
    "maple_log",
    "fire",
)
REQUIRED_TEMPLATE_NAMES = (
    "woodcutting_status",
    "empty_inventory_slot",
    "tree",
    "tinderbox",
    "maple_log",
    "fire",
)
TREE_TEMPLATE_NAMES = ("tree", "tree2", "tree3", "tree4")


@dataclass(frozen=True)
class WoodcutFiremakeDefaults:
    templates_dir: Path = ROOT / "templates" / SCRIPT_NAME
    template_scales: str = "0.35,0.4,0.45,0.5,0.55,0.6,0.65,0.7,0.8,1.0"
    threshold: float = 0.82
    status_timeout: float = 0.0
    inventory_timeout: float = 0.0
    click_timeout: float = 0.0
    poll_seconds: float = 0.15
    tick_seconds: float = 0.6
    time_jitter: float = 0.08
    pre_click_jitter: float = 0.05
    spot_jitter: int = 4
    click_scale: float = 1.0
    countdown: float = 2.0
    dry_run: bool = True
    monitor: int = 1
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32


DEFAULTS = WoodcutFiremakeDefaults()
DEFAULT_CONFIG_PATH = ROOT / "config" / "woodcut_firemake.example.json"


def build_templates(
    templates_dir: Path,
    threshold: float,
    default_scales: list[float],
    config: dict[str, Any],
) -> dict[str, TemplateState]:
    return build_template_states(TEMPLATE_NAMES, templates_dir, threshold, default_scales, config)


def missing_templates(templates: dict[str, TemplateState]) -> list[Path]:
    return [templates[name].path for name in REQUIRED_TEMPLATE_NAMES if not templates[name].path.exists()]


def available_tree_templates(templates: dict[str, TemplateState]) -> list[TemplateState]:
    return [templates[name] for name in TREE_TEMPLATE_NAMES if templates[name].path.exists()]


def click_template(
    state: WoodcutFiremakeState,
    mouse,
    template: TemplateState,
    click_timeout: float,
    click_scale: float,
    spot_jitter: int,
    pre_click_jitter: float,
    dry_run: bool,
) -> bool:
    match, best_score, scale = state.find(template, click_timeout)
    if match is None:
        print(f"{template.name}: not found; best={best_score:.3f}, scale={scale:g}")
        return False

    click_matched_template(
        mouse=mouse,
        template=template,
        match=match,
        scale=scale,
        click_scale=click_scale,
        spot_jitter=spot_jitter,
        pre_click_jitter=pre_click_jitter,
        dry_run=dry_run,
    )
    return True


def click_matched_template(
    mouse,
    template: TemplateState,
    match,
    scale: float,
    click_scale: float,
    spot_jitter: int,
    pre_click_jitter: float,
    dry_run: bool,
) -> None:
    click_x, click_y = match_click_coordinates(match, click_scale, spot_jitter)
    click_x += template.click_offset[0]
    click_y += template.click_offset[1]
    pre_wait = random.uniform(0.0, pre_click_jitter) if pre_click_jitter > 0 else 0.0
    if dry_run:
        print(
            f"{template.name}: found score={match.score:.3f}, scale={scale:g}, "
            f"capture_center={match.center}, click_offset={template.click_offset}, would click=({click_x},{click_y})"
        )
        return

    if pre_wait:
        time.sleep(pre_wait)
    mouse.click(click_x, click_y)
    print(
        f"{template.name}: clicked score={match.score:.3f}, scale={scale:g}, "
        f"click_offset={template.click_offset}, at=({click_x},{click_y})"
    )


def click_first_template(
    state: WoodcutFiremakeState,
    mouse,
    candidates: list[TemplateState],
    click_timeout: float,
    fallback_threshold: float,
    click_scale: float,
    spot_jitter: int,
    pre_click_jitter: float,
    dry_run: bool,
) -> bool:
    if not candidates:
        print("tree: no tree template images available")
        return False

    best_candidate: tuple[TemplateState, float, float, object | None] | None = None
    for template in candidates:
        match, best_score, scale = state.find(template, click_timeout)
        if match is not None:
            click_matched_template(
                mouse=mouse,
                template=template,
                match=match,
                scale=scale,
                click_scale=click_scale,
                spot_jitter=spot_jitter,
                pre_click_jitter=pre_click_jitter,
                dry_run=dry_run,
            )
            return True
        if best_candidate is None or best_score > best_candidate[1]:
            best_match, _best_match_score, best_match_scale = state.best(template)
            best_candidate = (template, best_score, best_match_scale or scale, best_match)

    if best_candidate is None:
        print("tree: not found; no candidate scores available")
        return False

    template, best_score, scale, best_match = best_candidate
    if best_match is not None and best_score >= fallback_threshold:
        print(
            f"tree: using fallback best={template.name} score={best_score:.3f}, "
            f"fallback_threshold={fallback_threshold:.3f}, scale={scale:g}"
        )
        click_matched_template(
            mouse=mouse,
            template=template,
            match=best_match,
            scale=scale,
            click_scale=click_scale,
            spot_jitter=spot_jitter,
            pre_click_jitter=pre_click_jitter,
            dry_run=dry_run,
        )
        return True

    print(
        f"tree: not found; best={template.name} score={best_score:.3f}, "
        f"threshold={template.threshold:.3f}, fallback_threshold={fallback_threshold:.3f}, scale={scale:g}"
    )
    return False


def wait_ticks(label: str, ticks: float, tick_seconds: float, jitter_seconds: float, dry_run: bool) -> None:
    delay = humanized_delay(ticks * tick_seconds, jitter_seconds)
    print(f"{label}: waiting {ticks:g} tick(s), {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)


def pause_after_missing_target(label: str, seconds: float, dry_run: bool) -> None:
    delay = max(0.0, seconds)
    if delay <= 0:
        return
    print(f"{label}: pausing {delay:.2f}s before next decision")
    if not dry_run:
        time.sleep(delay)


def run_flow(
    templates_dir: Path,
    monitor: int,
    template_scales: list[float],
    threshold: float,
    status_timeout: float,
    inventory_timeout: float,
    click_timeout: float,
    tree_fallback_threshold: float,
    no_target_backoff_seconds: float,
    poll_seconds: float,
    tick_seconds: float,
    time_jitter: float,
    pre_click_jitter: float,
    spot_jitter: int,
    click_scale: float,
    countdown: float,
    loops: int,
    dry_run: bool,
    move_duration_min: float,
    move_duration_max: float,
    platform: str,
    config: dict,
) -> int:
    config, window = resolve_regions(config)
    templates_dir = platform_template_dir(templates_dir, config, resolve_platform(platform))
    templates = build_templates(templates_dir, threshold, template_scales, config)
    tree_templates = available_tree_templates(templates)
    missing = missing_templates(templates)
    if missing:
        print("Missing template image(s):")
        for path in missing:
            print(f"  {path}")
        return 1

    mode = "DRY RUN" if dry_run else "LIVE"
    loop_label = "until stopped" if loops <= 0 else f"for {loops} decision loop(s)"
    print(f"{mode}: woodcutting/firemaking {loop_label}")
    if window is not None:
        print(
            f"RuneLite window: left={window['left']}, top={window['top']}, "
            f"width={window['width']}, height={window['height']}"
        )
    print("Stop with Esc or Cmd+Shift+Q.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(max(0.0, countdown))

    stop_keys = StopKeys()
    mouse = build_mouse(
        move_duration_min=move_duration_min,
        move_duration_max=move_duration_max,
        click_pause_seconds=0.05,
        spot_jitter_pixels=spot_jitter,
    )
    keyboard = KeyboardController()
    pyautogui.FAILSAFE = False

    stop_keys.start()
    try:
        with ScreenCapture(monitor=monitor) as screen:
            state = WoodcutFiremakeState(
                screen=screen,
                monitor=monitor,
                poll_seconds=poll_seconds,
                stop_keys=stop_keys,
            )

            loop = 0
            while not stop_keys.stop_requested and (loops <= 0 or loop < loops):
                loop += 1
                print(f"Decision loop {loop}" if loops <= 0 else f"Decision loop {loop}/{loops}")

                if state.exists(templates["woodcutting_status"], status_timeout):
                    wait_ticks("woodcutting_status", 6, tick_seconds, time_jitter, dry_run)
                    continue

                inventory_status = detect_inventory_status(
                    state,
                    templates["empty_inventory_slot"],
                    inventory_timeout,
                )
                print(
                    f"empty_inventory_slot: "
                    f"{'found' if inventory_status.has_empty_slot else 'not found'} "
                    f"score={inventory_status.empty_slot_score:.3f}, "
                    f"threshold={inventory_status.empty_slot_threshold:.3f}, "
                    f"scale={inventory_status.empty_slot_scale:g}"
                )
                if inventory_status.has_empty_slot:
                    if click_first_template(
                        state,
                        mouse,
                        tree_templates,
                        click_timeout,
                        tree_fallback_threshold,
                        click_scale,
                        spot_jitter,
                        pre_click_jitter,
                        dry_run,
                    ):
                        wait_ticks("after tree click", 6, tick_seconds, time_jitter, dry_run)
                    else:
                        pause_after_missing_target("tree missing", no_target_backoff_seconds, dry_run)
                    continue

                print("inventory_full: no empty slot detected")
                if not click_template(state, mouse, templates["fire"], click_timeout, click_scale, spot_jitter, pre_click_jitter, dry_run):
                    pause_after_missing_target("fire missing", no_target_backoff_seconds, dry_run)
                    continue
                wait_ticks("after initial fire click", 6, tick_seconds, time_jitter, dry_run)

                if not click_template(state, mouse, templates["tinderbox"], click_timeout, click_scale, spot_jitter, pre_click_jitter, dry_run):
                    continue
                if not click_template(state, mouse, templates["maple_log"], click_timeout, click_scale, spot_jitter, pre_click_jitter, dry_run):
                    continue
                wait_ticks("after tinderbox + maple_log", 6, tick_seconds, time_jitter, dry_run)

                if not click_template(state, mouse, templates["maple_log"], click_timeout, click_scale, spot_jitter, pre_click_jitter, dry_run):
                    continue
                if not click_template(state, mouse, templates["fire"], click_timeout, click_scale, spot_jitter, pre_click_jitter, dry_run):
                    continue
                wait_ticks("after log + fire", 2, tick_seconds, time_jitter, dry_run)
                if dry_run:
                    print("space: would press")
                else:
                    keyboard.press("space")
                    print("space: pressed")

                while not stop_keys.stop_requested:
                    if not state.exists(templates["maple_log"], inventory_timeout):
                        print("maple_log: no longer found in inventory")
                        break
                    wait_ticks("maple_log still present", 6, tick_seconds, time_jitter, dry_run)
                    if dry_run:
                        break

                if not stop_keys.stop_requested:
                    if not click_first_template(
                        state,
                        mouse,
                        tree_templates,
                        click_timeout,
                        tree_fallback_threshold,
                        click_scale,
                        spot_jitter,
                        pre_click_jitter,
                        dry_run,
                    ):
                        pause_after_missing_target("tree missing", no_target_backoff_seconds, dry_run)
    finally:
        stop_keys.stop()

    print("Stopped." if stop_keys.stop_requested else "Flow complete.")
    return 0


def run_calibration(
    templates_dir: Path,
    monitor: int,
    template_scales: list[float],
    threshold: float,
    poll_seconds: float,
    platform: str,
    config: dict,
) -> int:
    config, window = resolve_regions(config)
    templates_dir = platform_template_dir(templates_dir, config, resolve_platform(platform))
    templates = build_templates(templates_dir, threshold, template_scales, config)
    missing = missing_templates(templates)
    if missing:
        print("Missing template image(s):")
        for path in missing:
            print(f"  {path}")
        return 1

    stop_keys = StopKeys()
    with ScreenCapture(monitor=monitor) as screen:
        frame = screen.capture()
        print(f"Monitor {monitor}: left={frame.left}, top={frame.top}, width={frame.width}, height={frame.height}")
        if window is not None:
            print(
                f"RuneLite window: left={window['left']}, top={window['top']}, "
                f"width={window['width']}, height={window['height']}"
            )
        state = WoodcutFiremakeState(
            screen=screen,
            monitor=monitor,
            poll_seconds=poll_seconds,
            stop_keys=stop_keys,
        )
        for name in TEMPLATE_NAMES:
            template = templates[name]
            if not template.path.exists():
                print(f"{name}: optional template missing at {template.path}" if name not in REQUIRED_TEMPLATE_NAMES else f"{name}: missing at {template.path}")
                continue
            match, best_score, scale = state.find(template, timeout=0.05)
            region = template.region or {"left": frame.left, "top": frame.top, "width": frame.width, "height": frame.height}
            scale_label = ",".join(f"{item:g}" for item in template.scales)
            if match is None:
                print(
                    f"{name}: NOT found best={best_score:.3f}, threshold={template.threshold:.3f}, "
                    f"best_scale={scale:g}, scales={scale_label}, region={region}"
                )
                continue
            print(
                f"{name}: found score={match.score:.3f}, threshold={template.threshold:.3f}, "
                f"scale={scale:g}, center={match.center}, rect=({match.x},{match.y},{match.width},{match.height}), "
                f"click_offset={template.click_offset}, click_center="
                f"({match.center[0] + template.click_offset[0]},{match.center[1] + template.click_offset[1]}), "
                f"scales={scale_label}, region={region}"
            )
    return 0


def main() -> int:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None,
        help="Optional JSON config path",
    )
    config_args, _remaining = config_parser.parse_known_args()

    try:
        config = load_json_config(config_args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    platform_value = resolve_platform(value_from_config(config, "platform", "auto"))
    templates_dir_default = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value)

    parser = argparse.ArgumentParser(
        description="Woodcut maple logs, burn a full inventory, then return to the tree.",
        parents=[config_parser],
    )
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir_default, help="Directory containing this script's templates")
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", DEFAULTS.monitor), help="MSS monitor index")
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales), help="Comma-separated scale(s) applied to templates")
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold), help="Default template match score")
    parser.add_argument("--status-timeout", type=float, default=value_from_config(config, "status_timeout", DEFAULTS.status_timeout), help="Seconds to check woodcutting status")
    parser.add_argument("--inventory-timeout", type=float, default=value_from_config(config, "inventory_timeout", DEFAULTS.inventory_timeout), help="Seconds to check inventory templates")
    parser.add_argument("--click-timeout", type=float, default=value_from_config(config, "click_timeout", DEFAULTS.click_timeout), help="Seconds to find clickable templates")
    parser.add_argument("--tree-fallback-threshold", type=float, default=value_from_config(config, "tree_fallback_threshold", 0.78), help="Minimum score for clicking the best tree fallback when strict tree matching misses")
    parser.add_argument("--no-target-backoff-seconds", type=float, default=value_from_config(config, "no_target_backoff_seconds", 0.25), help="Seconds to pause after an expected target is missing")
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", DEFAULTS.poll_seconds), help="Polling interval while checking templates")
    parser.add_argument("--tick-seconds", type=float, default=value_from_config(config, "tick_seconds", DEFAULTS.tick_seconds), help="Real seconds per game tick")
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter), help="Maximum random seconds added/subtracted to tick waits")
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter), help="Maximum random seconds before click")
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter), help="Maximum random pixels away from template center")
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", DEFAULTS.click_scale), help="Divide capture pixel coordinates by this before clicking")
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", DEFAULTS.countdown), help="Seconds before starting")
    parser.add_argument("--loops", type=int, default=value_from_config(config, "loops", 1), help="Decision loop count; 0 means loop until stopped")
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min), help="Minimum mouse movement duration")
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max), help="Maximum mouse movement duration")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", DEFAULTS.dry_run), help="Locate and print without moving or clicking")
    parser.add_argument("--calibrate", action="store_true", help="Print one-shot template scores, scales, centers, and configured regions")
    args = parser.parse_args()

    try:
        args.platform = resolve_platform(args.platform)
        template_scales = parse_scales(str(args.template_scales))
        if args.calibrate:
            return run_calibration(
                templates_dir=args.templates_dir,
                monitor=int(args.monitor),
                template_scales=template_scales,
                threshold=float(args.threshold),
                poll_seconds=max(0.01, float(args.poll_seconds)),
                platform=args.platform,
                config=config,
            )
        return run_flow(
            templates_dir=args.templates_dir,
            monitor=int(args.monitor),
            template_scales=template_scales,
            threshold=float(args.threshold),
            status_timeout=max(0.0, float(args.status_timeout)),
            inventory_timeout=max(0.0, float(args.inventory_timeout)),
            click_timeout=max(0.0, float(args.click_timeout)),
            tree_fallback_threshold=max(0.0, float(args.tree_fallback_threshold)),
            no_target_backoff_seconds=max(0.0, float(args.no_target_backoff_seconds)),
            poll_seconds=max(0.01, float(args.poll_seconds)),
            tick_seconds=max(0.01, float(args.tick_seconds)),
            time_jitter=max(0.0, float(args.time_jitter)),
            pre_click_jitter=max(0.0, float(args.pre_click_jitter)),
            spot_jitter=max(0, int(args.spot_jitter)),
            click_scale=float(args.click_scale),
            countdown=max(0.0, float(args.countdown)),
            loops=int(args.loops),
            dry_run=bool(args.dry_run),
            move_duration_min=max(0.0, float(args.move_duration_min)),
            move_duration_max=max(0.0, float(args.move_duration_max)),
            platform=args.platform,
            config=config,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
