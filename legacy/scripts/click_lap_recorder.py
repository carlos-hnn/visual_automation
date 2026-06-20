from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui
from pynput import keyboard, mouse

ROOT = Path(__file__).resolve().parent
while ROOT != ROOT.parent and not (ROOT / "scripts" / "click_lap_recorder.py").exists():
    ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mouse import MouseConfig, MouseController
from core.terminal import install_timestamped_print

install_timestamped_print()

DEFAULT_RECORDING = ROOT / "records" / "click_lap.json"
DEFAULT_SPOT_JITTER_PIXELS = 4
DEFAULT_TIME_JITTER_SECONDS = 0.08


@dataclass(frozen=True)
class ClickStep:
    x: int
    y: int
    button: str
    delay_seconds: float

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "click",
            "x": self.x,
            "y": self.y,
            "button": self.button,
            "delay_seconds": round(self.delay_seconds, 4),
        }


class StopKeys:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self._pressed: set[str] = set()

    def on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> bool | None:
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed.add(normalized)

        if normalized == "esc" or {"cmd", "shift", "q"}.issubset(self._pressed):
            self.stop_event.set()
            return False
        return None

    def on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
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


def recording_script_path(recording_path: Path) -> Path:
    return recording_path.with_suffix(".py")


def screen_size() -> dict[str, int]:
    width, height = pyautogui.size()
    return {"width": int(width), "height": int(height)}


def write_recording(path: Path, steps: list[ClickStep]) -> None:
    payload = {
        "schema_version": 2,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "coordinate_space": "screen_absolute",
        "screen_size": screen_size(),
        "stop_keys": ["Esc", "Cmd+Shift+Q"],
        "events": [step.to_json() for step in steps],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def script_literal_for_steps(steps: list[ClickStep]) -> str:
    return json.dumps([step.to_json() for step in steps], indent=4)


def write_replay_script(path: Path, steps: list[ClickStep], recording_path: Path) -> None:
    relative_recording = recording_path.relative_to(ROOT) if recording_path.is_relative_to(ROOT) else recording_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f'''from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while ROOT != ROOT.parent and not (ROOT / "scripts" / "click_lap_recorder.py").exists():
    ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.click_lap_recorder import ClickStep, replay_steps

# Absolute screen coordinates and seconds since the previous click.
CLICK_LAP = {script_literal_for_steps(steps)}


def main() -> int:
    steps = [
        ClickStep(
            x=int(item["x"]),
            y=int(item["y"]),
            button=str(item.get("button", "left")),
            delay_seconds=float(item.get("delay_seconds", 0.0)),
        )
        for item in CLICK_LAP
    ]
    return replay_steps(
        steps,
        laps=0,
        speed=1.0,
        countdown=3.0,
        inter_lap_delay=0.5,
        spot_jitter_pixels={DEFAULT_SPOT_JITTER_PIXELS},
        time_jitter_seconds={DEFAULT_TIME_JITTER_SECONDS},
        dry_run=False,
    )


if __name__ == "__main__":
    from core.terminal import install_timestamped_print

    install_timestamped_print()
    print("Source recording: {relative_recording}")
    raise SystemExit(main())
'''
    path.write_text(body, encoding="utf-8")


def record_clicks(path: Path, countdown: float, script_output: Path | None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    steps: list[ClickStep] = []
    lock = threading.Lock()
    state = {"last_click_at": 0.0}
    stopper = StopKeys()

    print("Run this from Terminal inside the VM/target macOS so the right game screen receives replayed clicks.")
    print("The recorder listens globally, so clicks can be anywhere in the game window or UI.")
    print(f"Recording starts in {countdown:.1f}s. Press Esc or Cmd+Shift+Q to stop.")
    time.sleep(max(0.0, countdown))
    print("Recording clicks now...")

    def on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not pressed:
            return
        now = time.monotonic()
        with lock:
            last_click_at = state["last_click_at"]
            delay_seconds = 0.0 if last_click_at == 0.0 else now - last_click_at
            state["last_click_at"] = now
            step = ClickStep(x=int(x), y=int(y), button=button.name, delay_seconds=delay_seconds)
            steps.append(step)
            print(
                f"{len(steps):03d}: {step.button} click at ({step.x}, {step.y}) "
                f"after {step.delay_seconds:.2f}s"
            )

    key_listener = keyboard.Listener(on_press=stopper.on_press, on_release=stopper.on_release)
    mouse_listener = mouse.Listener(on_click=on_click)
    key_listener.start()
    mouse_listener.start()

    try:
        while not stopper.stop_event.is_set():
            time.sleep(0.05)
    finally:
        mouse_listener.stop()
        key_listener.stop()
        mouse_listener.join(timeout=1)
        key_listener.join(timeout=1)

    write_recording(path, steps)
    script_path = script_output if script_output is not None else recording_script_path(path)
    write_replay_script(script_path, steps, path)
    print(f"Saved {len(steps)} clicks to {path}")
    print(f"Saved loop replay script to {script_path}")
    return 0


def load_steps(path: Path) -> list[ClickStep]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps: list[ClickStep] = []
    for item in payload.get("events", []):
        if item.get("type") != "click":
            continue
        delay = item.get("delay_seconds", item.get("delay", 0.0))
        steps.append(
            ClickStep(
                x=int(item["x"]),
                y=int(item["y"]),
                button=str(item.get("button", "left")),
                delay_seconds=float(delay),
            )
        )
    return steps


def humanized_delay(base_delay: float, speed: float, jitter_seconds: float) -> float:
    scaled_delay = max(0.0, base_delay / max(0.01, speed))
    jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
    return max(0.0, scaled_delay + jitter)


def build_mouse(spot_jitter_pixels: int) -> MouseController:
    return MouseController(
        MouseConfig(
            move_duration_min=0.05,
            move_duration_max=0.16,
            click_pause_seconds=0.04,
            random_offset_pixels=max(0, int(spot_jitter_pixels)),
            point_tolerance_pixels=max(0, int(spot_jitter_pixels)),
        )
    )


def replay_steps(
    steps: list[ClickStep],
    laps: int,
    speed: float,
    countdown: float,
    inter_lap_delay: float,
    spot_jitter_pixels: int,
    time_jitter_seconds: float,
    dry_run: bool,
) -> int:
    if not steps:
        print("No click steps to replay.")
        return 1

    stopper = StopKeys()
    key_listener = keyboard.Listener(on_press=stopper.on_press, on_release=stopper.on_release)
    mouse_controller = build_mouse(spot_jitter_pixels)

    mode = "DRY RUN" if dry_run else "LIVE"
    lap_label = "until stopped" if laps <= 0 else f"for {laps} lap(s)"
    print(f"{mode}: replaying {len(steps)} click(s) {lap_label}")
    print("Stop with Esc or Cmd+Shift+Q.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(max(0.0, countdown))

    key_listener.start()
    lap = 0
    try:
        while not stopper.stop_event.is_set() and (laps <= 0 or lap < laps):
            lap += 1
            print(f"Lap {lap}" if laps <= 0 else f"Lap {lap}/{laps}")
            for index, step in enumerate(steps, start=1):
                if stopper.stop_event.is_set():
                    break

                delay = humanized_delay(step.delay_seconds, speed=speed, jitter_seconds=time_jitter_seconds)
                if delay:
                    time.sleep(delay)

                target_x, target_y = mouse_controller.point_near(step.x, step.y, tolerance_pixels=spot_jitter_pixels)
                if dry_run:
                    print(
                        f"  {index:03d}: wait {delay:.2f}s, would {step.button}-click "
                        f"({target_x}, {target_y}) from base ({step.x}, {step.y})"
                    )
                    continue

                mouse_controller.click(target_x, target_y, button=step.button)

            if laps > 0 and lap >= laps:
                break
            if not stopper.stop_event.is_set() and inter_lap_delay > 0:
                time.sleep(inter_lap_delay)
    finally:
        stopper.stop_event.set()
        key_listener.stop()
        key_listener.join(timeout=1)

    print("Replay stopped." if stopper.stop_event.is_set() else "Replay complete.")
    return 0


def replay_clicks(
    path: Path,
    laps: int,
    speed: float,
    countdown: float,
    inter_lap_delay: float,
    spot_jitter_pixels: int,
    time_jitter_seconds: float,
    dry_run: bool,
) -> int:
    steps = load_steps(path)
    if not steps:
        print(f"No click events found in {path}")
        return 1
    print(f"Loaded {path}")
    return replay_steps(
        steps,
        laps=laps,
        speed=speed,
        countdown=countdown,
        inter_lap_delay=inter_lap_delay,
        spot_jitter_pixels=spot_jitter_pixels,
        time_jitter_seconds=time_jitter_seconds,
        dry_run=dry_run,
    )


def show_recording(path: Path) -> int:
    steps = load_steps(path)
    print(f"{path}: {len(steps)} click(s)")
    for index, step in enumerate(steps, start=1):
        print(f"{index:03d}: {step.button} click at ({step.x}, {step.y}), delay={step.delay_seconds:.2f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record absolute screen clicks and replay them as a humanized loop."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Record mouse clicks to JSON and a replay script")
    record_parser.add_argument("--output", type=Path, default=DEFAULT_RECORDING, help="Recording JSON path")
    record_parser.add_argument("--script-output", type=Path, default=None, help="Replay Python script path")
    record_parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before recording starts")

    replay_parser = subparsers.add_parser("replay", help="Replay a recorded click loop")
    replay_parser.add_argument("--input", type=Path, default=DEFAULT_RECORDING, help="Recording JSON path")
    replay_parser.add_argument("--laps", type=int, default=0, help="Loop count; 0 means loop until stopped")
    replay_parser.add_argument("--speed", type=float, default=1.0, help="Timing multiplier; 2.0 is twice as fast")
    replay_parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before replay starts")
    replay_parser.add_argument("--inter-lap-delay", type=float, default=0.5, help="Seconds between laps")
    replay_parser.add_argument(
        "--spot-jitter",
        type=int,
        default=DEFAULT_SPOT_JITTER_PIXELS,
        help="Maximum random pixel offset around each recorded click",
    )
    replay_parser.add_argument(
        "--time-jitter",
        type=float,
        default=DEFAULT_TIME_JITTER_SECONDS,
        help="Maximum random seconds added or removed from each click delay",
    )
    replay_parser.add_argument("--dry-run", action="store_true", help="Print clicks without moving or clicking")

    show_parser = subparsers.add_parser("show", help="Print recorded clicks")
    show_parser.add_argument("--input", type=Path, default=DEFAULT_RECORDING, help="Recording JSON path")

    args = parser.parse_args()
    if args.command == "record":
        return record_clicks(args.output, args.countdown, args.script_output)
    if args.command == "replay":
        return replay_clicks(
            path=args.input,
            laps=int(args.laps),
            speed=float(args.speed),
            countdown=float(args.countdown),
            inter_lap_delay=float(args.inter_lap_delay),
            spot_jitter_pixels=int(args.spot_jitter),
            time_jitter_seconds=max(0.0, float(args.time_jitter)),
            dry_run=bool(args.dry_run),
        )
    if args.command == "show":
        return show_recording(args.input)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
