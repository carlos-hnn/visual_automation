from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from pynput import keyboard, mouse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.regions import Region, RegionManager
from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from scripts.falador_rooftop_loop import active_capture_region, grid_region_for_name

install_timestamped_print()

DEFAULT_CONFIG = ROOT / "config" / "falador_rooftop.json"
DEFAULT_OUTPUT_DIR = ROOT / "records" / "rooftop_routes"
GRID_NAMES = (
    "north-west",
    "north",
    "north-east",
    "west",
    "center",
    "east",
    "south-west",
    "south",
    "south-east",
)


@dataclass(frozen=True)
class RecordedClick:
    index: int
    button: str
    delay: float
    screen_x: int
    screen_y: int
    region_x: int
    region_y: int
    grid_region: str
    capture_region: Region
    template_box: Region
    snapshot_path: Path
    annotated_path: Path
    template_path: Path

    def to_json(self, base_dir: Path) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": "click",
            "button": self.button,
            "delay_seconds": round(self.delay, 4),
            "screen": {"x": self.screen_x, "y": self.screen_y},
            "capture_relative": {"x": self.region_x, "y": self.region_y},
            "grid_region": self.grid_region,
            "capture_region": self.capture_region.to_mss(),
            "template_box": self.template_box.to_mss(),
            "snapshot_path": str(self.snapshot_path.relative_to(base_dir)),
            "annotated_path": str(self.annotated_path.relative_to(base_dir)),
            "template_path": str(self.template_path.relative_to(base_dir)),
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


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def point_inside_region(x: int, y: int, region: Region) -> bool:
    return region.left <= x < region.left + region.width and region.top <= y < region.top + region.height


def grid_name_for_point(x: int, y: int, region: Region) -> str:
    rel_x = max(0, min(region.width - 1, x - region.left))
    rel_y = max(0, min(region.height - 1, y - region.top))
    col = min(2, int(rel_x / max(1, region.width / 3)))
    row = min(2, int(rel_y / max(1, region.height / 3)))
    for name in GRID_NAMES:
        if grid_region_for_name(name, region) is None:
            continue
        if (col, row) == {
            "north-west": (0, 0),
            "north": (1, 0),
            "north-east": (2, 0),
            "west": (0, 1),
            "center": (1, 1),
            "east": (2, 1),
            "south-west": (0, 2),
            "south": (1, 2),
            "south-east": (2, 2),
        }[name]:
            return name
    return "center"


def template_box_for_click(frame: Frame, screen_x: int, screen_y: int, template_size: int) -> Region:
    half = max(4, template_size // 2)
    rel_x = screen_x - frame.left
    rel_y = screen_y - frame.top
    left = max(0, min(frame.width - 1, rel_x - half))
    top = max(0, min(frame.height - 1, rel_y - half))
    right = min(frame.width, left + template_size)
    bottom = min(frame.height, top + template_size)
    left = max(0, right - template_size)
    top = max(0, bottom - template_size)
    return Region(left=int(left), top=int(top), width=int(right - left), height=int(bottom - top))


def save_click_artifacts(
    frame: Frame,
    click_index: int,
    screen_x: int,
    screen_y: int,
    template_size: int,
    route_dir: Path,
) -> tuple[Path, Path, Path, Region]:
    snapshots_dir = route_dir / "snapshots"
    annotated_dir = route_dir / "annotated"
    templates_dir = route_dir / "templates"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshots_dir / f"click_{click_index:03d}_region.png"
    annotated_path = annotated_dir / f"click_{click_index:03d}_annotated.png"
    template_path = templates_dir / f"click_{click_index:03d}_template.png"
    template_box = template_box_for_click(frame, screen_x, screen_y, template_size)

    rel_x = screen_x - frame.left
    rel_y = screen_y - frame.top
    annotated = frame.image.copy()
    cv2.drawMarker(annotated, (rel_x, rel_y), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=24, thickness=2)
    cv2.rectangle(
        annotated,
        (template_box.left, template_box.top),
        (template_box.left + template_box.width, template_box.top + template_box.height),
        (0, 255, 255),
        2,
    )

    template = frame.image[
        template_box.top : template_box.top + template_box.height,
        template_box.left : template_box.left + template_box.width,
    ]
    cv2.imwrite(str(snapshot_path), frame.image)
    cv2.imwrite(str(annotated_path), annotated)
    cv2.imwrite(str(template_path), template)
    return snapshot_path, annotated_path, template_path, template_box


def build_route_payload(
    config_path: Path,
    route_dir: Path,
    config: dict[str, Any],
    events: list[RecordedClick],
    template_size: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "rooftop_region_timed_template_route",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_config": relative_path(config_path),
        "stop_keys": ["Esc", "Cmd+Shift+Q"],
        "app_window": config.get("app_window", {}),
        "template_size": template_size,
        "grid_regions": list(GRID_NAMES),
        "events": [event.to_json(route_dir) for event in events],
    }


def write_route_summary(route_dir: Path, events: list[RecordedClick]) -> None:
    lines = [
        "# Rooftop Route",
        "",
        "| # | Delay | Region | Click | Template |",
        "|---:|---:|---|---|---|",
    ]
    for event in events:
        lines.append(
            f"| {event.index} | {event.delay:.2f}s | {event.grid_region} | "
            f"({event.region_x}, {event.region_y}) | {event.template_path.relative_to(route_dir)} |"
        )
    (route_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_route(config_path: Path, output_dir: Path, route_name: str | None, countdown: float, template_size: int) -> int:
    config = load_config(config_path)
    route_dir = output_dir / (route_name or f"rooftop_route_{timestamp()}")
    route_dir.mkdir(parents=True, exist_ok=True)

    regions = RegionManager(ROOT / "config" / "regions.json")
    fallback_region = regions.get_region(config["regions"]["game_view"])
    stopper = StopKeys()
    events: list[RecordedClick] = []
    lock = threading.Lock()
    state = {"last_click_at": 0.0}

    print(f"Recording rooftop route into {route_dir}")
    print(f"Recording starts in {countdown:.1f}s. Press Esc or Cmd+Shift+Q to stop.")
    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        capture_region = active_capture_region(config, fallback_region, __import__("logging").getLogger("rooftop_route_recorder"))
        print(f"RuneLite capture region: {capture_region}")
        time.sleep(max(0.0, countdown))
        print("Recording clicks now...")

        def on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> None:
            if not pressed:
                return
            now = time.monotonic()
            current_region = active_capture_region(config, fallback_region, __import__("logging").getLogger("rooftop_route_recorder"))
            if not point_inside_region(int(x), int(y), current_region):
                print(f"ignored click outside capture region: ({int(x)}, {int(y)})")
                return

            frame = screen.capture(current_region)
            with lock:
                index = len(events) + 1
                last_click_at = state["last_click_at"]
                delay = 0.0 if last_click_at == 0.0 else now - last_click_at
                state["last_click_at"] = now

                snapshot_path, annotated_path, template_path, template_box = save_click_artifacts(
                    frame=frame,
                    click_index=index,
                    screen_x=int(x),
                    screen_y=int(y),
                    template_size=template_size,
                    route_dir=route_dir,
                )
                event = RecordedClick(
                    index=index,
                    button=button.name,
                    delay=delay,
                    screen_x=int(x),
                    screen_y=int(y),
                    region_x=int(x) - current_region.left,
                    region_y=int(y) - current_region.top,
                    grid_region=grid_name_for_point(int(x), int(y), current_region),
                    capture_region=current_region,
                    template_box=template_box,
                    snapshot_path=snapshot_path,
                    annotated_path=annotated_path,
                    template_path=template_path,
                )
                events.append(event)
                print(
                    f"{index:03d}: {event.button} click grid={event.grid_region} "
                    f"rel=({event.region_x}, {event.region_y}) delay={delay:.2f}s template={template_path.name}"
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

    payload = build_route_payload(config_path, route_dir, config, events, template_size)
    route_path = route_dir / "route.json"
    route_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_route_summary(route_dir, events)
    print(f"Saved {len(events)} click(s) to {route_path}")
    return 0


def show_route(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    print(f"{path}: {len(events)} click(s)")
    for event in events:
        print(
            f"{int(event['index']):03d}: delay={float(event['delay_seconds']):.2f}s "
            f"grid={event['grid_region']} rel=({event['capture_relative']['x']}, {event['capture_relative']['y']}) "
            f"template={event['template_path']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a rooftop route as timed clicks, 3x3 regions, and click templates.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Record a new rooftop route")
    record_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Rooftop config path")
    record_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory where route folders are saved")
    record_parser.add_argument("--route-name", default=None, help="Optional route folder name")
    record_parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before recording starts")
    record_parser.add_argument("--template-size", type=int, default=96, help="Square pixels to crop around each click")

    show_parser = subparsers.add_parser("show", help="Print route click summary")
    show_parser.add_argument("--input", type=Path, required=True, help="route.json path")

    args = parser.parse_args()
    if args.command == "record":
        return record_route(
            config_path=args.config,
            output_dir=args.output_dir,
            route_name=args.route_name,
            countdown=float(args.countdown),
            template_size=max(16, int(args.template_size)),
        )
    if args.command == "show":
        return show_route(args.input)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
