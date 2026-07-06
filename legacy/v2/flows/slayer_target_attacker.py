from __future__ import annotations

import argparse
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from v2.actions import StopKeys, build_mouse, match_click_coordinates
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.flows.health_fish_monitor import best_fish_match, fish_templates, health_percent
from v2.flows.woodcut_firemake import find_window_bounds
from v2.game_states.template_matching import parse_scales

install_timestamped_print()

DEFAULT_CONFIG_PATH = ROOT / "config" / "slayer_target_attacker.example.json"


@dataclass(frozen=True)
class RedTarget:
    x: int
    y: int
    width: int
    height: int
    red_pixels: int
    distance: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def parse_region(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain left, top, width, height")
    required = ("left", "top", "width", "height")
    if any(key not in value for key in required):
        raise ValueError(f"{label} must contain left, top, width, height")
    region = {key: int(value[key]) for key in required}
    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError(f"{label} width and height must be positive")
    return region


def absolute_region(window: dict[str, int], relative: dict[str, int]) -> dict[str, int]:
    return {
        "left": window["left"] + relative["left"],
        "top": window["top"] + relative["top"],
        "width": min(relative["width"], window["width"] - relative["left"]),
        "height": min(relative["height"], window["height"] - relative["top"]),
    }


def red_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, np.array((0, 170, 130), np.uint8), np.array((10, 255, 255), np.uint8))
    high_red = cv2.inRange(hsv, np.array((170, 170, 130), np.uint8), np.array((179, 255, 255), np.uint8))
    return cv2.bitwise_or(low_red, high_red)


def combat_green_fraction(frame: Frame) -> float:
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array((35, 110, 70), np.uint8), np.array((90, 255, 255), np.uint8))
    return float(np.count_nonzero(green)) / max(1, green.size)


def mask_ignored_regions(
    mask: np.ndarray,
    search_region: dict[str, int],
    ignored_regions: list[dict[str, int]],
) -> None:
    for ignored in ignored_regions:
        left = max(0, ignored["left"] - search_region["left"])
        top = max(0, ignored["top"] - search_region["top"])
        right = min(mask.shape[1], ignored["left"] + ignored["width"] - search_region["left"])
        bottom = min(mask.shape[0], ignored["top"] + ignored["height"] - search_region["top"])
        if right > left and bottom > top:
            mask[top:bottom, left:right] = 0


def detect_red_targets(
    frame: Frame,
    search_region_relative: dict[str, int],
    ignored_regions_relative: list[dict[str, int]],
    anchor_absolute: tuple[int, int],
    min_red_pixels: int,
    min_dimension: int,
    max_dimension: int,
    grouping_pixels: int,
) -> tuple[list[RedTarget], np.ndarray]:
    raw_mask = red_mask(frame.image)
    mask_ignored_regions(raw_mask, search_region_relative, ignored_regions_relative)
    kernel_size = max(1, grouping_pixels * 2 + 1)
    grouped = cv2.dilate(
        raw_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
        iterations=1,
    )
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(grouped)
    targets: list[RedTarget] = []
    for label in range(1, count):
        x, y, width, height, _area = (int(item) for item in stats[label])
        red_pixels = int(np.count_nonzero(raw_mask[labels == label]))
        if red_pixels < min_red_pixels:
            continue
        if min(width, height) < min_dimension or max(width, height) > max_dimension:
            continue
        absolute_x = frame.left + x
        absolute_y = frame.top + y
        center_x = absolute_x + width // 2
        center_y = absolute_y + height // 2
        distance = math.hypot(center_x - anchor_absolute[0], center_y - anchor_absolute[1])
        targets.append(RedTarget(absolute_x, absolute_y, width, height, red_pixels, distance))
    targets.sort(key=lambda target: target.distance)
    return targets, raw_mask


def save_debug_view(
    frame: Frame,
    targets: list[RedTarget],
    anchor: tuple[int, int],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    image = frame.image.copy()
    for index, target in enumerate(targets, start=1):
        x = target.x - frame.left
        y = target.y - frame.top
        color = (0, 255, 0) if index == 1 else (0, 215, 255)
        cv2.rectangle(image, (x, y), (x + target.width, y + target.height), color, 2)
        cv2.putText(image, str(index), (x, max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    anchor_local = (anchor[0] - frame.left, anchor[1] - frame.top)
    cv2.drawMarker(image, anchor_local, (255, 255, 0), cv2.MARKER_CROSS, 20, 2)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"slayer_targets_{timestamp}.png"
    cv2.imwrite(str(path), image)
    return path


def run(
    *, monitor: int, window_title: str, combat_region_relative: dict[str, int],
    target_region_relative: dict[str, int], ignored_regions_relative: list[dict[str, int]],
    health_region_relative: dict[str, int], inventory_region_relative: dict[str, int],
    fish_templates_dir: Path, health_threshold_percent: float, health_check_seconds: float,
    required_low_health_readings: int, eat_cooldown_seconds: float,
    fish_match_threshold: float, fish_template_scales: list[float],
    anchor_relative: tuple[int, int], combat_green_threshold: float,
    required_out_of_combat_readings: int, check_seconds: float,
    post_combat_wait_seconds: float,
    attack_confirm_timeout: float, min_red_pixels: int, min_target_dimension: int,
    max_target_dimension: int, grouping_pixels: int, countdown: float,
    spot_jitter: int, dry_run: bool, calibrate: bool,
) -> int:
    window = find_window_bounds(window_title)
    if window is None:
        print(f"RuneLite window not found for title/owner containing: {window_title}")
        return 1
    combat_region = absolute_region(window, combat_region_relative)
    target_region = absolute_region(window, target_region_relative)
    health_region = absolute_region(window, health_region_relative)
    inventory_region = absolute_region(window, inventory_region_relative)
    anchor = (window["left"] + anchor_relative[0], window["top"] + anchor_relative[1])
    fish_paths = fish_templates(fish_templates_dir)
    print(f"RuneLite window={window}; character_anchor={anchor}")

    stop_keys = StopKeys()
    mouse = build_mouse(0.16, 0.32, spot_jitter_pixels=spot_jitter)
    out_of_combat_readings = 0
    attack_pending_until = 0.0
    low_health_readings = 0
    last_eat = float("-inf")
    next_health_check = 0.0
    combat_was_active = False
    print(
        f"{'DRY RUN' if dry_run else 'LIVE'}: attacking the nearest red Slayer target when out of combat; "
        f"checking health every {health_check_seconds:g}s"
    )
    print("Stop with Esc or Cmd+Shift+Q.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(countdown)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=monitor) as screen:
            while not stop_keys.stop_requested:
                now = time.monotonic()
                ate_this_tick = False
                if now >= next_health_check:
                    percent = health_percent(screen.capture(health_region))
                    low_health_readings = low_health_readings + 1 if percent < health_threshold_percent else 0
                    print(
                        f"health={percent:.1f}% "
                        f"low={low_health_readings}/{required_low_health_readings}"
                    )
                    if (
                        low_health_readings >= required_low_health_readings
                        and now - last_eat >= eat_cooldown_seconds
                    ):
                        template, match, scale = best_fish_match(
                            screen, fish_paths, monitor, fish_template_scales, inventory_region
                        )
                        if match.score < fish_match_threshold:
                            print(f"no fish found; best={template.name} score={match.score:.3f}")
                        else:
                            fish_x, fish_y = match_click_coordinates(match, 1.0, spot_jitter)
                            if dry_run:
                                print(
                                    f"would eat {template.name} score={match.score:.3f} "
                                    f"scale={scale:g} at=({fish_x},{fish_y})"
                                )
                            else:
                                mouse.click(fish_x, fish_y)
                                park_x = max(window["left"], inventory_region["left"] - 80)
                                park_y = inventory_region["top"] + inventory_region["height"] // 2
                                mouse.move_to(park_x, park_y)
                                print(
                                    f"ate {template.name} score={match.score:.3f} "
                                    f"scale={scale:g} at=({fish_x},{fish_y})"
                                )
                            last_eat = now
                            low_health_readings = 0
                            ate_this_tick = True
                            attack_pending_until = max(attack_pending_until, now + 1.2)
                    next_health_check = now + health_check_seconds

                green_fraction = combat_green_fraction(screen.capture(combat_region))
                in_combat = green_fraction >= combat_green_threshold
                if in_combat:
                    combat_was_active = True
                    out_of_combat_readings = 0
                    attack_pending_until = 0.0
                    print(f"combat=yes green={green_fraction:.3f}")
                else:
                    if combat_was_active:
                        print(
                            f"combat ended; waiting {post_combat_wait_seconds:g}s "
                            "for the defeated target to disappear"
                        )
                        deadline = time.monotonic() + post_combat_wait_seconds
                        while time.monotonic() < deadline and not stop_keys.stop_requested:
                            time.sleep(min(0.10, deadline - time.monotonic()))
                        if stop_keys.stop_requested:
                            break
                        green_fraction = combat_green_fraction(screen.capture(combat_region))
                        in_combat = green_fraction >= combat_green_threshold
                        if in_combat:
                            print(f"combat resumed during wait green={green_fraction:.3f}")
                            out_of_combat_readings = 0
                            attack_pending_until = 0.0
                            continue
                        combat_was_active = False
                    out_of_combat_readings += 1
                    now = time.monotonic()
                    pending = now < attack_pending_until
                    print(
                        f"combat=no green={green_fraction:.3f} "
                        f"clear={out_of_combat_readings}/{required_out_of_combat_readings} pending={pending}"
                    )
                    if out_of_combat_readings >= required_out_of_combat_readings and not pending and not ate_this_tick:
                        frame = screen.capture(target_region)
                        targets, _mask = detect_red_targets(
                            frame, target_region_relative, ignored_regions_relative, anchor,
                            min_red_pixels, min_target_dimension, max_target_dimension, grouping_pixels,
                        )
                        if not targets:
                            print("no red Slayer target found")
                        else:
                            target = targets[0]
                            click_x, click_y = target.center
                            click_x += random.randint(-spot_jitter, spot_jitter) if spot_jitter else 0
                            click_y += random.randint(-spot_jitter, spot_jitter) if spot_jitter else 0
                            if dry_run:
                                print(
                                    f"would attack nearest target center={target.center} click=({click_x},{click_y}) "
                                    f"distance={target.distance:.1f} red_pixels={target.red_pixels} candidates={len(targets)}"
                                )
                            else:
                                mouse.click(click_x, click_y)
                                print(
                                    f"attacked nearest target at=({click_x},{click_y}) "
                                    f"distance={target.distance:.1f} candidates={len(targets)}"
                                )
                            attack_pending_until = time.monotonic() + attack_confirm_timeout
                            out_of_combat_readings = 0

                        if calibrate:
                            path = save_debug_view(frame, targets, anchor, ROOT / "logs" / "debug")
                            print(f"debug={path}")
                            return 0

                if calibrate:
                    frame = screen.capture(target_region)
                    targets, _mask = detect_red_targets(
                        frame, target_region_relative, ignored_regions_relative, anchor,
                        min_red_pixels, min_target_dimension, max_target_dimension, grouping_pixels,
                    )
                    path = save_debug_view(frame, targets, anchor, ROOT / "logs" / "debug")
                    print(f"targets={len(targets)} debug={path}")
                    return 0

                deadline = time.monotonic() + check_seconds
                while time.monotonic() < deadline and not stop_keys.stop_requested:
                    time.sleep(min(0.10, deadline - time.monotonic()))
    finally:
        stop_keys.stop()
    print("Stopped.")
    return 0


def main() -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    known, _remaining = pre_parser.parse_known_args()
    config = load_json_config(known.config)
    parser = argparse.ArgumentParser(
        description="Attack the nearest red Slayer target whenever the combat bar is empty.",
        parents=[pre_parser],
    )
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", True))
    parser.add_argument("--calibrate", action="store_true", help="Inspect one frame, save target annotations, and exit")
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", 2.0))
    args = parser.parse_args()

    try:
        combat_region = parse_region(value_from_config(config, "combat_region", {}), "combat_region")
        target_region = parse_region(value_from_config(config, "target_region", {}), "target_region")
        health_region = parse_region(value_from_config(config, "health_region", {}), "health_region")
        inventory_region = parse_region(value_from_config(config, "inventory_region", {}), "inventory_region")
        raw_ignored = value_from_config(config, "ignored_regions", [])
        if not isinstance(raw_ignored, list):
            raise ValueError("ignored_regions must be a list")
        ignored_regions = [parse_region(item, "ignored_regions item") for item in raw_ignored]
        anchor_value = value_from_config(config, "character_anchor", {})
        if not isinstance(anchor_value, dict) or "x" not in anchor_value or "y" not in anchor_value:
            raise ValueError("character_anchor must contain x and y")
        templates_dir_value = Path(value_from_config(config, "fish_templates_dir", "templates/health_fish"))
        templates_dir = templates_dir_value if templates_dir_value.is_absolute() else ROOT / templates_dir_value
        return run(
            monitor=int(value_from_config(config, "monitor", 1)),
            window_title=str(value_from_config(config, "window_title", "RuneLite")),
            combat_region_relative=combat_region,
            target_region_relative=target_region,
            ignored_regions_relative=ignored_regions,
            health_region_relative=health_region,
            inventory_region_relative=inventory_region,
            fish_templates_dir=templates_dir,
            health_threshold_percent=max(0.0, min(100.0, float(value_from_config(config, "health_threshold_percent", 70.0)))),
            health_check_seconds=max(0.1, float(value_from_config(config, "health_check_seconds", 5.0))),
            required_low_health_readings=max(1, int(value_from_config(config, "required_low_health_readings", 2))),
            eat_cooldown_seconds=max(0.0, float(value_from_config(config, "eat_cooldown_seconds", 3.0))),
            fish_match_threshold=max(0.0, min(1.0, float(value_from_config(config, "fish_match_threshold", 0.78)))),
            fish_template_scales=parse_scales(str(value_from_config(config, "fish_template_scales", "0.5"))),
            anchor_relative=(int(anchor_value["x"]), int(anchor_value["y"])),
            combat_green_threshold=max(0.0, min(1.0, float(value_from_config(config, "combat_green_threshold", 0.02)))),
            required_out_of_combat_readings=max(1, int(value_from_config(config, "required_out_of_combat_readings", 2))),
            check_seconds=max(0.1, float(value_from_config(config, "check_seconds", 5.0))),
            post_combat_wait_seconds=max(0.0, float(value_from_config(config, "post_combat_wait_seconds", 1.0))),
            attack_confirm_timeout=max(0.1, float(value_from_config(config, "attack_confirm_timeout", 5.0))),
            min_red_pixels=max(1, int(value_from_config(config, "min_red_pixels", 80))),
            min_target_dimension=max(1, int(value_from_config(config, "min_target_dimension", 25))),
            max_target_dimension=max(1, int(value_from_config(config, "max_target_dimension", 140))),
            grouping_pixels=max(0, int(value_from_config(config, "grouping_pixels", 8))),
            countdown=max(0.0, args.countdown),
            spot_jitter=max(0, int(value_from_config(config, "spot_jitter", 3))),
            dry_run=bool(args.dry_run), calibrate=bool(args.calibrate),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
