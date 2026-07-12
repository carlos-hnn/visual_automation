from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import pyautogui

from core.debug import save_annotated_match
from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from v2.actions import StopKeys, build_mouse, humanized_delay, match_click_coordinates
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT, TemplateSequenceDefaults
from v2.game_states import TemplateStep, fallback_candidates, load_template_steps, rotate_steps, wait_for_template_match
from v2.game_states.template_matching import best_template_match, parse_scales
from v2.game_states.template_sequence import parse_order
from v2.platforming import add_platform_argument, platform_template_dir, resolve_platform

install_timestamped_print()

DEFAULTS = TemplateSequenceDefaults()
MAX_UNSUCCESSFUL_ROUNDS = 2


def save_debug_match(frame: Frame, match: TemplateMatch, debug_dir: Path, prefix: str) -> Path:
    relative_top_left = (match.x - frame.left, match.y - frame.top)
    relative_bottom_right = (relative_top_left[0] + match.width, relative_top_left[1] + match.height)
    return save_annotated_match(frame.image, relative_top_left, relative_bottom_right, match.score, debug_dir, prefix)


def run_sequence(
    steps: list[TemplateStep],
    monitor: int,
    template_scales: list[float],
    threshold: float,
    timeout: float,
    poll_seconds: float,
    click_scale: float,
    countdown: float,
    loops: int,
    dry_run: bool,
    debug: bool,
    fallback_timeout: float,
    spot_jitter_pixels: int,
    time_jitter_seconds: float,
    pre_click_jitter_seconds: float,
    move_duration_min: float,
    move_duration_max: float,
) -> int:
    if not steps:
        print("No steps to run.")
        return 1

    mode = "DRY RUN" if dry_run else "LIVE"
    loop_label = "until stopped" if loops <= 0 else f"for {loops} loop(s)"
    print(f"{mode}: {len(steps)} template click step(s) {loop_label}")
    print("Stop with Esc or Cmd+Shift+Q.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(max(0.0, countdown))

    debug_dir = ROOT / "logs" / "debug"
    stop_keys = StopKeys()
    mouse = build_mouse(
        move_duration_min=move_duration_min,
        move_duration_max=move_duration_max,
        click_pause_seconds=0.05,
        spot_jitter_pixels=spot_jitter_pixels,
    )
    pyautogui.FAILSAFE = False

    stop_keys.start()
    try:
        with ScreenCapture(monitor=monitor) as screen:
            step_scales_map: dict[str, list[float]] = {step.name: list(template_scales) for step in steps}

            loop = 0
            unsuccessful_rounds = 0
            while not stop_keys.stop_requested and (loops <= 0 or loop < loops):
                loop += 1
                print(f"Loop {loop}" if loops <= 0 else f"Loop {loop}/{loops}")

                step_index = 0
                matched_in_round = False
                while step_index < len(steps):
                    if stop_keys.stop_requested:
                        break

                    step = steps[step_index]
                    step_template_scales = step_scales_map[step.name]
                    result = wait_for_template_match(
                        screen=screen,
                        template_path=step.template_path,
                        monitor=monitor,
                        template_scales=step_template_scales,
                        threshold=threshold,
                        timeout=timeout,
                        poll_seconds=poll_seconds,
                        stop_keys=stop_keys,
                    )
                    match = result.match
                    scale = result.scale
                    if match is None:
                        print(
                            f"  {step.index:03d}: NOT found {step.template_path.name}; "
                            f"best={result.best_seen.score:.3f}, scale={scale:g}; trying next obstacles"
                        )
                        fallback_found = False
                        for candidate_index, candidate_step in fallback_candidates(steps, step_index):
                            candidate_template_scales = step_scales_map[candidate_step.name]
                            candidate_result = wait_for_template_match(
                                screen=screen,
                                template_path=candidate_step.template_path,
                                monitor=monitor,
                                template_scales=candidate_template_scales,
                                threshold=threshold,
                                timeout=fallback_timeout,
                                poll_seconds=poll_seconds,
                                stop_keys=stop_keys,
                            )
                            if candidate_result.match is None:
                                print(
                                    f"  {candidate_step.index:03d}: fallback not found "
                                    f"best={candidate_result.best_seen.score:.3f}, scale={candidate_result.scale:g}"
                                )
                                continue

                            print(f"  recovered at {candidate_step.template_path.name}; continuing from there")
                            step = candidate_step
                            step_index = candidate_index
                            match = candidate_result.match
                            scale = candidate_result.scale
                            fallback_found = True
                            break

                        if not fallback_found:
                            print(f"  {step.index:03d}: lost {step.template_path.name}; continuing to next template")
                            step_index += 1
                            continue

                    matched_in_round = True
                    click_x, click_y = match_click_coordinates(match, click_scale, spot_jitter_pixels)
                    wait_seconds = humanized_delay(step.wait_seconds, time_jitter_seconds)
                    pre_click_delay = random.uniform(0.0, pre_click_jitter_seconds) if pre_click_jitter_seconds > 0 else 0.0
                    if debug:
                        _latest_match, frame, _latest_scale = best_template_match(
                            screen,
                            step.template_path,
                            monitor,
                            [scale],
                        )
                        annotated_path = save_debug_match(frame, match, debug_dir, "template_sequence_match")
                        print(f"  debug match: {annotated_path}")

                    if dry_run:
                        print(
                            f"  {step.index:03d}: found {step.template_path.name} score={match.score:.3f} "
                            f"scale={scale:g} capture_center={match.center} click=({click_x},{click_y}); "
                            f"would pre-wait {pre_click_delay:.2f}s, then wait {wait_seconds:.2f}s "
                            f"(base {step.wait_seconds:.2f}s)"
                        )
                    else:
                        if pre_click_delay:
                            time.sleep(pre_click_delay)
                        mouse.click(click_x, click_y)
                        print(
                            f"  {step.index:03d}: clicked {step.template_path.name} score={match.score:.3f} "
                            f"scale={scale:g} at=({click_x},{click_y}); waiting {wait_seconds:.2f}s "
                            f"(base {step.wait_seconds:.2f}s)"
                        )
                        time.sleep(wait_seconds)
                    step_index += 1

                if stop_keys.stop_requested:
                    break
                if matched_in_round:
                    unsuccessful_rounds = 0
                    continue

                unsuccessful_rounds += 1
                print(
                    f"No templates matched in full round {unsuccessful_rounds}/"
                    f"{MAX_UNSUCCESSFUL_ROUNDS}."
                )
                if unsuccessful_rounds >= MAX_UNSUCCESSFUL_ROUNDS:
                    print("No templates matched for 2 full rounds; ending sequence.")
                    break
    finally:
        stop_keys.stop()

    print("Stopped." if stop_keys.stop_requested else "Sequence complete.")
    return 0


def main() -> int:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", type=Path, default=None, help="Optional JSON config path")
    config_args, _remaining = config_parser.parse_known_args()
    config = load_json_config(config_args.config)

    order_default = value_from_config(config, "order", list(DEFAULTS.order))
    if isinstance(order_default, str):
        order_default = parse_order(order_default)
    else:
        order_default = [str(item) for item in order_default]

    platform_value = resolve_platform(value_from_config(config, "platform", "auto"))
    templates_dir_default = platform_template_dir(value_from_config(config, "templates_dir", DEFAULTS.templates_dir), config, platform_value)

    parser = argparse.ArgumentParser(
        description="Find numbered game templates on screen, click each one, and wait between clicks.",
        parents=[config_parser],
    )
    add_platform_argument(parser, config)
    parser.add_argument("--templates-dir", type=Path, default=templates_dir_default, help="Directory containing route templates")
    parser.add_argument("--order", type=parse_order, default=order_default, help="Comma-separated template order")
    parser.add_argument("--waits", default=value_from_config(config, "waits", DEFAULTS.waits), help="One wait value for all steps, or comma-separated waits")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N templates")
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", 1), help="MSS monitor index")
    parser.add_argument("--threshold", type=float, default=value_from_config(config, "threshold", DEFAULTS.threshold), help="Minimum template match score")
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", DEFAULTS.template_scales), help="Comma-separated scale(s) applied to templates")
    parser.add_argument("--timeout", type=float, default=value_from_config(config, "timeout", 6.0), help="Seconds to wait for each template")
    parser.add_argument("--poll-seconds", type=float, default=value_from_config(config, "poll_seconds", 0.20), help="Polling interval while waiting")
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", 1.0), help="Divide capture pixel coordinates by this before clicking")
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", 2.0), help="Seconds before starting")
    parser.add_argument("--loops", type=int, default=None, help="Loop count; 0 means loop until stopped")
    parser.add_argument("--start-at", default=None, help="Start at a specific obstacle/template, e.g. 3")
    parser.add_argument("--fallback-timeout", type=float, default=value_from_config(config, "fallback_timeout", 1.2), help="Seconds to try each next obstacle after expected one is missing")
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", DEFAULTS.spot_jitter_pixels), help="Maximum random pixels away from template center")
    parser.add_argument("--time-jitter", type=float, default=value_from_config(config, "time_jitter", DEFAULTS.time_jitter_seconds), help="Maximum random seconds added/subtracted")
    parser.add_argument("--pre-click-jitter", type=float, default=value_from_config(config, "pre_click_jitter", DEFAULTS.pre_click_jitter_seconds), help="Maximum random seconds before click")
    parser.add_argument("--move-duration-min", type=float, default=value_from_config(config, "move_duration_min", DEFAULTS.move_duration_min), help="Minimum mouse movement duration")
    parser.add_argument("--move-duration-max", type=float, default=value_from_config(config, "move_duration_max", DEFAULTS.move_duration_max), help="Maximum mouse movement duration")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", False), help="Locate and print without moving or clicking")
    parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=value_from_config(config, "debug", False), help="Save raw and annotated screenshots to logs/debug")
    args = parser.parse_args()

    try:
        args.platform = resolve_platform(args.platform)
        args.templates_dir = platform_template_dir(args.templates_dir, config, args.platform)
        steps = load_template_steps(
            templates_dir=args.templates_dir,
            order=list(args.order),
            waits_value=str(args.waits),
            limit=args.limit,
        )
        steps = rotate_steps(steps, args.start_at)
        template_scales = parse_scales(str(args.template_scales))
        loops = 1 if args.loops is None and args.dry_run else args.loops
        loops = 0 if loops is None else int(loops)
        return run_sequence(
            steps=steps,
            monitor=int(args.monitor),
            template_scales=template_scales,
            threshold=float(args.threshold),
            timeout=max(0.1, float(args.timeout)),
            poll_seconds=max(0.01, float(args.poll_seconds)),
            click_scale=float(args.click_scale),
            countdown=max(0.0, float(args.countdown)),
            loops=loops,
            dry_run=bool(args.dry_run),
            debug=bool(args.debug),
            fallback_timeout=max(0.1, float(args.fallback_timeout)),
            spot_jitter_pixels=max(0, int(args.spot_jitter)),
            time_jitter_seconds=max(0.0, float(args.time_jitter)),
            pre_click_jitter_seconds=max(0.0, float(args.pre_click_jitter)),
            move_duration_min=max(0.0, float(args.move_duration_min)),
            move_duration_max=max(0.0, float(args.move_duration_max)),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
