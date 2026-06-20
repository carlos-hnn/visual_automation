from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import Region, RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from scripts.falador_rooftop_loop import active_capture_region, grid_region_for_name

install_timestamped_print()

DEFAULT_CONFIG = ROOT / "config" / "falador_rooftop.json"
DEFAULT_ROUTE = ROOT / "records" / "rooftop_routes_cleaned" / "falador_test_02_green_normal" / "route.green_normal.json"


@dataclass(frozen=True)
class RouteEvent:
    index: int
    button: str
    delay_seconds: float
    grid_region: str
    template_path: Path
    template_box: Region
    click_offset: tuple[int, int]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def path_from_route(route_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else route_dir / path


def load_route(path: Path) -> tuple[dict[str, Any], list[RouteEvent]]:
    payload = load_json(path)
    route_dir = path.parent
    events: list[RouteEvent] = []
    for raw in payload.get("events", []):
        template_box = Region.from_mapping(raw["template_box"])
        click_relative = raw["capture_relative"]
        click_offset = (
            int(click_relative["x"]) - template_box.left,
            int(click_relative["y"]) - template_box.top,
        )
        events.append(
            RouteEvent(
                index=int(raw["index"]),
                button=str(raw.get("button", "left")),
                delay_seconds=float(raw.get("delay_seconds", 0.0)),
                grid_region=str(raw["grid_region"]),
                template_path=path_from_route(route_dir, str(raw["template_path"])),
                template_box=template_box,
                click_offset=click_offset,
            )
        )
    return payload, events


def runtime_seconds_from_config(config: dict[str, Any]) -> float | None:
    hours = config.get("max_runtime_hours")
    if hours is not None:
        return float(hours) * 60 * 60
    seconds = config.get("max_runtime_seconds", 900)
    if seconds is None:
        return None
    return float(seconds)


def expanded_region(region: Region, padding: int, bounds: Region) -> Region:
    left = max(bounds.left, region.left - padding)
    top = max(bounds.top, region.top - padding)
    right = min(bounds.left + bounds.width, region.left + region.width + padding)
    bottom = min(bounds.top + bounds.height, region.top + region.height + padding)
    return Region(left=left, top=top, width=max(1, right - left), height=max(1, bottom - top))


def route_search_region(event: RouteEvent, capture_region: Region, grid_padding: int) -> Region:
    cell = grid_region_for_name(event.grid_region, capture_region)
    if cell is None:
        return capture_region
    absolute_cell = Region(
        left=capture_region.left + cell.left,
        top=capture_region.top + cell.top,
        width=cell.width,
        height=cell.height,
    )
    return expanded_region(absolute_cell, grid_padding, capture_region)


def find_template_in_region(
    screen: ScreenCapture,
    template_path: Path,
    region: Region,
    threshold: float,
) -> tuple[TemplateMatch | None, float]:
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"Template image not found: {template_path}")

    frame = screen.capture(region)
    if template.shape[0] > frame.image.shape[0] or template.shape[1] > frame.image.shape[1]:
        return None, 0.0

    result = cv2.matchTemplate(frame.image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    score = float(max_val)
    if score < threshold:
        return None, score

    return (
        TemplateMatch(
            x=frame.left + int(max_loc[0]),
            y=frame.top + int(max_loc[1]),
            width=int(template.shape[1]),
            height=int(template.shape[0]),
            score=score,
        ),
        score,
    )


def click_point_for_match(match: TemplateMatch, event: RouteEvent) -> tuple[int, int]:
    offset_x = max(0, min(match.width - 1, event.click_offset[0]))
    offset_y = max(0, min(match.height - 1, event.click_offset[1]))
    return match.x + offset_x, match.y + offset_y


def build_mouse(config: dict[str, Any]) -> MouseController:
    mouse_settings = config.get("mouse", {})
    return MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.04)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.10)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.04)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 3)),
            point_tolerance_pixels=int(mouse_settings.get("point_tolerance_pixels", 3)),
        )
    )


def replay_route(
    route_path: Path,
    config_path: Path,
    laps: int,
    threshold: float,
    timeout: float,
    poll_seconds: float,
    grid_padding: int,
    speed: float,
    countdown: float,
    dry_run: bool,
) -> int:
    route_payload, events = load_route(route_path)
    if not events:
        print(f"No events found in {route_path}")
        return 1

    config = load_json(config_path)
    if isinstance(route_payload.get("app_window"), dict):
        config["app_window"] = route_payload["app_window"]

    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    regions = RegionManager(ROOT / "config" / "regions.json")
    fallback_region = regions.get_region(config["regions"]["game_view"])
    mouse = build_mouse(config)

    safety_settings = config.get("safety", {})
    safety = SafetyController(
        logger,
        SafetyConfig(
            max_runtime_seconds=runtime_seconds_from_config(config),
            stop_hotkey=safety_settings.get("stop_hotkey", "cmd+shift+q"),
            enable_esc_stop=bool(safety_settings.get("enable_esc_stop", True)),
            failsafe_corner_pixels=int(safety_settings.get("failsafe_corner_pixels", 5)),
        ),
    )

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"{mode}: replaying {route_path} for {laps} lap(s)")
    print("Stop with Esc, Cmd+Shift+Q, or mouse in the top-left fail-safe corner.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(max(0.0, countdown))

    delay_scale = 1.0 / max(0.01, speed)
    safety.start()
    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        try:
            for lap in range(1, laps + 1):
                if safety.should_stop():
                    break
                logger.info("rooftop route lap %s/%s", lap, laps)
                print(f"Lap {lap}/{laps}")

                for event in events:
                    if safety.should_stop():
                        break
                    delay = max(0.0, event.delay_seconds * delay_scale)
                    if delay:
                        time.sleep(delay)

                    match = None
                    best_score = 0.0
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline and not safety.should_stop():
                        capture_region = active_capture_region(config, fallback_region, logger)
                        search_region = route_search_region(event, capture_region, grid_padding)
                        match, best_score = find_template_in_region(screen, event.template_path, search_region, threshold)
                        if match is not None:
                            break
                        time.sleep(poll_seconds)

                    if match is None:
                        logger.warning(
                            "event %s template not found grid=%s best_score=%.3f template=%s",
                            event.index,
                            event.grid_region,
                            best_score,
                            event.template_path,
                        )
                        print(f"  {event.index:03d}: not found grid={event.grid_region} best={best_score:.3f}")
                        return 2

                    click_x, click_y = click_point_for_match(match, event)
                    if dry_run:
                        mouse.move_to(click_x, click_y)
                        logger.info(
                            "dry-run event=%s grid=%s match=(%s,%s) click=(%s,%s) score=%.3f",
                            event.index,
                            event.grid_region,
                            match.x,
                            match.y,
                            click_x,
                            click_y,
                            match.score,
                        )
                        print(f"  {event.index:03d}: would click {event.grid_region} score={match.score:.3f} at=({click_x},{click_y})")
                    else:
                        mouse.click_point(click_x, click_y, button=event.button)
                        logger.info(
                            "clicked event=%s grid=%s click=(%s,%s) score=%.3f",
                            event.index,
                            event.grid_region,
                            click_x,
                            click_y,
                            match.score,
                        )
                        print(f"  {event.index:03d}: clicked {event.grid_region} score={match.score:.3f}")
        finally:
            safety.stop_listeners()

    print("Replay complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a cleaned rooftop route using grid-scoped obstacle templates.")
    parser.add_argument("--route", type=Path, default=DEFAULT_ROUTE, help="Cleaned route JSON path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Rooftop config path")
    parser.add_argument("--laps", type=int, default=1, help="How many route laps to replay")
    parser.add_argument("--threshold", type=float, default=0.72, help="Template match threshold")
    parser.add_argument("--timeout", type=float, default=6.0, help="Seconds to wait for each event template")
    parser.add_argument("--poll-seconds", type=float, default=0.20, help="Polling interval while waiting for a template")
    parser.add_argument("--grid-padding", type=int, default=45, help="Pixels to expand each recorded grid region")
    parser.add_argument("--speed", type=float, default=1.0, help="Timing multiplier; 2.0 is twice as fast")
    parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before replay starts")
    parser.add_argument("--dry-run", action="store_true", help="Move to clicks without clicking")
    args = parser.parse_args()
    return replay_route(
        route_path=args.route,
        config_path=args.config,
        laps=max(1, int(args.laps)),
        threshold=float(args.threshold),
        timeout=float(args.timeout),
        poll_seconds=float(args.poll_seconds),
        grid_padding=max(0, int(args.grid_padding)),
        speed=float(args.speed),
        countdown=float(args.countdown),
        dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    raise SystemExit(main())
