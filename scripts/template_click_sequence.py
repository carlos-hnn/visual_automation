from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pyautogui
from pynput import keyboard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.debug import save_annotated_match
from core.mouse import MouseConfig, MouseController
from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch

install_timestamped_print()

DEFAULT_TEMPLATES_DIR = Path("assets/templates")
DEFAULT_ORDER = ("1", "2", "3", "4", "5", "6")
DEFAULT_WAITS = "4,21,12,11,3,9"
DEFAULT_TEMPLATE_SCALES = "0.35,0.4,0.45,0.5,0.55,0.6,0.65"
DEFAULT_THRESHOLD = 0.60
DEFAULT_SPOT_JITTER_PIXELS = 4
DEFAULT_TIME_JITTER_SECONDS = 0.1
DEFAULT_PRE_CLICK_JITTER_SECONDS = 0.05
DEFAULT_MOVE_DURATION_MIN = 0.05
DEFAULT_MOVE_DURATION_MAX = 0.15


@dataclass(frozen=True)
class Step:
    index: int
    name: str
    template_path: Path
    wait_seconds: float


class StopKeys:
    def __init__(self) -> None:
        self.stop_requested = False
        self._pressed: set[str] = set()
        self._listener: keyboard.Listener | None = None

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener.join(timeout=1)

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed.add(normalized)
        if normalized == "esc" or {"cmd", "shift", "q"}.issubset(self._pressed):
            self.stop_requested = True

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed.discard(normalized)

    def _normalize_key(self, key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if key in {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r}:
            return "cmd"
        if key in {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}:
            return "shift"
        if key == keyboard.Key.esc:
            return "esc"
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        return None


def parse_order(value: str) -> list[str]:
    order = [item.strip() for item in value.split(",") if item.strip()]
    if not order:
        raise argparse.ArgumentTypeError("order must contain at least one template name")
    return order


def parse_waits(value: str, step_count: int) -> list[float]:
    waits = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(waits) == 1:
        return waits * step_count
    if len(waits) < step_count:
        raise ValueError(f"--waits must be one value or at least {step_count} values")
    return waits[:step_count]


def parse_scales(value: str) -> list[float]:
    scales = [float(item.strip()) for item in value.split(",") if item.strip()]
    scales = [scale for scale in scales if scale > 0]
    if not scales:
        raise ValueError("--template-scales must contain at least one positive value")
    return scales


def template_path_for_name(templates_dir: Path, name: str) -> Path:
    path = Path(name)
    if path.suffix:
        return path if path.is_absolute() else templates_dir / path
    return templates_dir / f"{name}.png"


def load_steps(templates_dir: Path, order: list[str], waits_value: str, limit: int | None) -> list[Step]:
    waits = parse_waits(waits_value, len(order))
    selected_order = order if limit is None else order[: max(0, limit)]
    selected_waits = waits if limit is None else waits[: max(0, limit)]
    steps = [
        Step(index=index, name=name, template_path=template_path_for_name(templates_dir, name), wait_seconds=wait)
        for index, (name, wait) in enumerate(zip(selected_order, selected_waits), start=1)
    ]
    missing = [str(step.template_path) for step in steps if not step.template_path.exists()]
    if missing:
        raise FileNotFoundError("Missing template(s): " + ", ".join(missing))
    return steps


def rotate_steps(steps: list[Step], start_at: str | None) -> list[Step]:
    if start_at is None:
        return steps
    start = start_at.strip()
    if not start:
        return steps

    for index, step in enumerate(steps):
        if step.name == start or step.template_path.stem == start or str(step.index) == start:
            return steps[index:] + steps[:index]
    valid = ", ".join(step.name for step in steps)
    raise ValueError(f"--start-at must be one of: {valid}")


def scaled_template(template_path: Path, scale: float) -> np.ndarray:
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"Template image not found or unreadable: {template_path}")
    if scale == 1.0:
        return template
    width = max(1, round(template.shape[1] * scale))
    height = max(1, round(template.shape[0] * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(template, (width, height), interpolation=interpolation)


def best_template_match(
    screen: ScreenCapture,
    template_path: Path,
    monitor: int,
    template_scales: list[float],
    min_template_dimension: int = 0,
) -> tuple[TemplateMatch, Frame, float]:
    frame = screen.capture()
    best: TemplateMatch | None = None
    best_scale = template_scales[0]

    valid_scales = []
    for scale in template_scales:
        template = scaled_template(template_path, scale)
        if template.shape[0] > frame.image.shape[0] or template.shape[1] > frame.image.shape[1]:
            raise ValueError(f"Template is larger than monitor {monitor} at scale {scale:g}: {template_path}")
        if min_template_dimension > 0 and (
            template.shape[0] < min_template_dimension or template.shape[1] < min_template_dimension
        ):
            continue
        valid_scales.append(scale)

    scales_to_check = valid_scales if valid_scales else template_scales
    for scale in scales_to_check:
        template = scaled_template(template_path, scale)
        result = cv2.matchTemplate(frame.image, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        match = TemplateMatch(
            x=frame.left + int(max_loc[0]),
            y=frame.top + int(max_loc[1]),
            width=int(template.shape[1]),
            height=int(template.shape[0]),
            score=float(max_val),
        )
        if best is None or match.score > best.score:
            best = match
            best_scale = scale

    if best is None:
        raise RuntimeError("No template scales were available")
    return best, frame, best_scale


def wait_for_match(
    screen: ScreenCapture,
    template_path: Path,
    monitor: int,
    template_scales: list[float],
    threshold: float,
    timeout: float,
    poll_seconds: float,
    stop_keys: StopKeys,
) -> tuple[TemplateMatch | None, TemplateMatch, float]:
    deadline = time.monotonic() + timeout
    best_seen: TemplateMatch | None = None
    best_seen_scale = template_scales[0]
    while time.monotonic() < deadline and not stop_keys.stop_requested:
        match, _frame, scale = best_template_match(screen, template_path, monitor, template_scales)
        if best_seen is None or match.score > best_seen.score:
            best_seen = match
            best_seen_scale = scale
        if match.score >= threshold:
            return match, match, scale
        time.sleep(max(0.01, poll_seconds))
    if best_seen is None:
        best_seen, _frame, best_seen_scale = best_template_match(screen, template_path, monitor, template_scales)
    return None, best_seen, best_seen_scale


def match_step(
    screen: ScreenCapture,
    step: Step,
    monitor: int,
    template_scales: list[float],
    threshold: float,
    timeout: float,
    poll_seconds: float,
    stop_keys: StopKeys,
) -> tuple[TemplateMatch | None, TemplateMatch, float]:
    return wait_for_match(
        screen=screen,
        template_path=step.template_path,
        monitor=monitor,
        template_scales=template_scales,
        threshold=threshold,
        timeout=timeout,
        poll_seconds=poll_seconds,
        stop_keys=stop_keys,
    )


def fallback_candidates(steps: list[Step], start_index: int) -> list[tuple[int, Step]]:
    if len(steps) <= 1 or start_index >= len(steps) - 1:
        return []
    return [(index, step) for index, step in enumerate(steps[start_index + 1 :], start=start_index + 1)]


def save_debug_match(frame: Frame, match: TemplateMatch, debug_dir: Path, prefix: str) -> Path:
    relative_top_left = (match.x - frame.left, match.y - frame.top)
    relative_bottom_right = (relative_top_left[0] + match.width, relative_top_left[1] + match.height)
    return save_annotated_match(frame.image, relative_top_left, relative_bottom_right, match.score, debug_dir, prefix)


def click_coordinates(match: TemplateMatch, click_scale: float, spot_jitter_pixels: int) -> tuple[int, int]:
    center_x, center_y = match.center
    jitter = max(0, int(spot_jitter_pixels))
    max_x_offset = min(jitter, max(0, (match.width - 1) // 2))
    max_y_offset = min(jitter, max(0, (match.height - 1) // 2))
    center_x += random.randint(-max_x_offset, max_x_offset) if max_x_offset else 0
    center_y += random.randint(-max_y_offset, max_y_offset) if max_y_offset else 0
    scale = max(0.01, click_scale)
    return round(center_x / scale), round(center_y / scale)


def humanized_delay(base_seconds: float, jitter_seconds: float) -> float:
    jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
    return max(0.0, base_seconds + jitter)


def run_sequence(
    steps: list[Step],
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
    mouse = MouseController(
        MouseConfig(
            move_duration_min=move_duration_min,
            move_duration_max=max(move_duration_min, move_duration_max),
            click_pause_seconds=0.05,
            random_offset_pixels=max(0, spot_jitter_pixels),
            point_tolerance_pixels=max(0, spot_jitter_pixels),
        )
    )
    pyautogui.FAILSAFE = False

    stop_keys.start()
    try:
        with ScreenCapture(monitor=monitor) as screen:
            step_scales_map: dict[str, list[float]] = {step.name: list(template_scales) for step in steps}

            loop = 0
            while not stop_keys.stop_requested and (loops <= 0 or loop < loops):
                loop += 1
                print(f"Loop {loop}" if loops <= 0 else f"Loop {loop}/{loops}")

                step_index = 0
                while step_index < len(steps):
                    if stop_keys.stop_requested:
                        break

                    step = steps[step_index]
                    step_template_scales = step_scales_map[step.name]
                    match, best, scale = match_step(
                        screen=screen,
                        step=step,
                        monitor=monitor,
                        template_scales=step_template_scales,
                        threshold=threshold,
                        timeout=timeout,
                        poll_seconds=poll_seconds,
                        stop_keys=stop_keys,
                    )
                    if match is None:
                        print(
                            f"  {step.index:03d}: NOT found {step.template_path.name}; "
                            f"best={best.score:.3f}, scale={scale:g}; trying next obstacles"
                        )
                        fallback_found = False
                        for candidate_index, candidate_step in fallback_candidates(steps, step_index):
                            candidate_template_scales = step_scales_map[candidate_step.name]
                            candidate_match, candidate_best, candidate_scale = match_step(
                                screen=screen,
                                step=candidate_step,
                                monitor=monitor,
                                template_scales=candidate_template_scales,
                                threshold=threshold,
                                timeout=fallback_timeout,
                                poll_seconds=poll_seconds,
                                stop_keys=stop_keys,
                            )
                            if candidate_match is None:
                                print(
                                    f"  {candidate_step.index:03d}: fallback not found "
                                    f"best={candidate_best.score:.3f}, scale={candidate_scale:g}"
                                )
                                continue

                            print(f"  recovered at {candidate_step.template_path.name}; continuing from there")
                            step = candidate_step
                            step_index = candidate_index
                            match = candidate_match
                            scale = candidate_scale
                            fallback_found = True
                            break

                        if not fallback_found:
                            print(f"  {step.index:03d}: lost {step.template_path.name}; continuing to next template")
                            step_index += 1
                            continue

                    assert match is not None
                    click_x, click_y = click_coordinates(match, click_scale, spot_jitter_pixels)
                    wait_seconds = humanized_delay(step.wait_seconds, time_jitter_seconds)
                    pre_click_delay = random.uniform(0.0, pre_click_jitter_seconds) if pre_click_jitter_seconds > 0 else 0.0
                    if debug:
                        _latest_match, frame, _latest_scale = best_template_match(screen, step.template_path, monitor, [scale])
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
    finally:
        stop_keys.stop()

    print("Stopped." if stop_keys.stop_requested else "Sequence complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find numbered game templates on screen, click each one, and wait between clicks."
    )
    parser.add_argument("--templates-dir", type=Path, default=DEFAULT_TEMPLATES_DIR, help="Directory containing 1.png ... 6.png")
    parser.add_argument("--order", type=parse_order, default=list(DEFAULT_ORDER), help="Comma-separated template order")
    parser.add_argument("--waits", default=DEFAULT_WAITS, help="One wait value for all steps, or six comma-separated waits")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N templates")
    parser.add_argument("--monitor", type=int, default=1, help="MSS monitor index")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Minimum template match score; lower if templates are dim or small",
    )
    parser.add_argument("--template-scales", default=DEFAULT_TEMPLATE_SCALES, help="Comma-separated scale(s) applied to templates before matching")
    parser.add_argument("--timeout", type=float, default=6.0, help="Seconds to wait for each template")
    parser.add_argument("--poll-seconds", type=float, default=0.20, help="Polling interval while waiting")
    parser.add_argument("--click-scale", type=float, default=1.0, help="Divide capture pixel coordinates by this before clicking")
    parser.add_argument("--countdown", type=float, default=2.0, help="Seconds before starting")
    parser.add_argument("--loops", type=int, default=None, help="Loop count; 0 means loop until stopped")
    parser.add_argument("--start-at", default=None, help="Start at a specific obstacle/template, e.g. 3")
    parser.add_argument("--fallback-timeout", type=float, default=1.2, help="Seconds to try each next obstacle after the expected one is missing")
    parser.add_argument("--spot-jitter", type=int, default=DEFAULT_SPOT_JITTER_PIXELS, help="Maximum random pixels away from the template center")
    parser.add_argument("--time-jitter", type=float, default=DEFAULT_TIME_JITTER_SECONDS, help="Maximum random seconds added/subtracted from each post-click wait")
    parser.add_argument("--pre-click-jitter", type=float, default=DEFAULT_PRE_CLICK_JITTER_SECONDS, help="Maximum random seconds to pause before each click")
    parser.add_argument("--move-duration-min", type=float, default=DEFAULT_MOVE_DURATION_MIN, help="Minimum mouse movement duration in seconds")
    parser.add_argument("--move-duration-max", type=float, default=DEFAULT_MOVE_DURATION_MAX, help="Maximum mouse movement duration in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Locate and print without moving or clicking")
    parser.add_argument("--debug", action="store_true", help="Save raw and annotated screenshots to logs/debug")
    args = parser.parse_args()

    try:
        steps = load_steps(
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
