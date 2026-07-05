from __future__ import annotations

import argparse
import random
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.screen import Frame, ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch
from v2.actions import StopKeys, build_mouse, match_click_coordinates
from v2.config import load_json_config, value_from_config
from v2.definitions import ROOT
from v2.game_states.template_matching import best_template_match, parse_scales

install_timestamped_print()


def parse_region(value: str | list[int] | tuple[int, ...] | dict[str, Any]) -> dict[str, int]:
    if isinstance(value, dict):
        region = {key: int(value[key]) for key in ("left", "top", "width", "height")}
    else:
        parts = list(value) if isinstance(value, (list, tuple)) else [item.strip() for item in str(value).split(",")]
        if len(parts) != 4:
            raise ValueError("Region must be left,top,width,height")
        region = dict(zip(("left", "top", "width", "height"), (int(item) for item in parts)))
    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError("Region width and height must be positive")
    return region


def prayer_percent(frame: Frame, minimum_row_coverage: float = 0.18) -> float:
    """Estimate fill percentage from the cyan/green pixels in a cropped prayer bar."""
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    # Broad cyan/green range, intentionally excluding the grey/black bar surround.
    mask = cv2.inRange(hsv, np.array((45, 100, 80), dtype=np.uint8), np.array((100, 255, 255), dtype=np.uint8))
    row_coverage = np.count_nonzero(mask, axis=1) / max(1, mask.shape[1])
    filled_rows = np.flatnonzero(row_coverage >= minimum_row_coverage)
    if filled_rows.size == 0:
        return 0.0
    fill_top = int(filled_rows.min())
    return max(0.0, min(100.0, (mask.shape[0] - fill_top) * 100.0 / mask.shape[0]))


def potion_templates(directory: Path) -> list[Path]:
    paths = sorted(path for path in directory.glob("*.png") if path.is_file())
    if not paths:
        raise FileNotFoundError(f"No potion PNG templates found in: {directory}")
    return paths


def best_potion_match(
    screen: ScreenCapture,
    templates: list[Path],
    monitor: int,
    scales: list[float],
    inventory_region: dict[str, int],
) -> tuple[Path, TemplateMatch, float]:
    candidates = []
    for template in templates:
        match, _frame, scale = best_template_match(
            screen, template, monitor, scales, region=inventory_region
        )
        candidates.append((template, match, scale))
    return max(candidates, key=lambda item: item[1].score)


def run(
    *, monitor: int, bar_region: dict[str, int], inventory_region: dict[str, int],
    templates_dir: Path, threshold_percent: float, check_seconds: float,
    required_low_readings: int, cooldown_seconds: float, match_threshold: float,
    template_scales: list[float], countdown: float, click_scale: float,
    spot_jitter: int, dry_run: bool,
) -> int:
    templates = potion_templates(templates_dir)
    stop_keys = StopKeys()
    mouse = build_mouse(0.16, 0.32, spot_jitter_pixels=spot_jitter)
    low_readings = 0
    last_click = float("-inf")

    print(f"{'DRY RUN' if dry_run else 'LIVE'}: checking prayer every {check_seconds:g}s; use below {threshold_percent:g}%")
    print("Stop with Esc or Cmd+Shift+Q.")
    print(f"Starting in {countdown:.1f}s...")
    time.sleep(countdown)
    stop_keys.start()
    try:
        with ScreenCapture(monitor=monitor) as screen:
            while not stop_keys.stop_requested:
                percent = prayer_percent(screen.capture(bar_region))
                low_readings = low_readings + 1 if percent < threshold_percent else 0
                print(f"prayer={percent:.1f}% low_readings={low_readings}/{required_low_readings}")

                cooled_down = time.monotonic() - last_click >= cooldown_seconds
                if low_readings >= required_low_readings and cooled_down:
                    template, match, scale = best_potion_match(
                        screen, templates, monitor, template_scales, inventory_region
                    )
                    if match.score < match_threshold:
                        print(f"no potion found; best={template.name} score={match.score:.3f}")
                    else:
                        x, y = match_click_coordinates(match, click_scale, spot_jitter)
                        if dry_run:
                            print(f"would click {template.name} score={match.score:.3f} scale={scale:g} at=({x},{y})")
                        else:
                            time.sleep(random.uniform(0.05, 0.20))
                            mouse.click(x, y)
                            # RuneLite previews the potion restore while the cursor
                            # remains over it. Move into the game viewport so the
                            # next bar reading only sees the real prayer amount.
                            park_x = max(0, inventory_region["left"] - 80)
                            park_y = inventory_region["top"] + inventory_region["height"] // 2
                            mouse.move_to(park_x, park_y)
                            print(f"clicked {template.name} score={match.score:.3f} scale={scale:g} at=({x},{y})")
                        last_click = time.monotonic()
                        low_readings = 0

                deadline = time.monotonic() + check_seconds
                while time.monotonic() < deadline and not stop_keys.stop_requested:
                    time.sleep(min(0.10, deadline - time.monotonic()))
    finally:
        stop_keys.stop()
    print("Stopped.")
    return 0


def main() -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=ROOT / "config" / "prayer_potion.example.json")
    known, _ = pre_parser.parse_known_args()
    config = load_json_config(known.config)

    parser = argparse.ArgumentParser(description="Monitor the prayer bar and drink a potion below a threshold.", parents=[pre_parser])
    parser.add_argument("--monitor", type=int, default=value_from_config(config, "monitor", 1))
    parser.add_argument("--bar-region", default=value_from_config(config, "bar_region", "0,0,50,350"))
    parser.add_argument("--inventory-region", default=value_from_config(config, "inventory_region", "0,0,400,600"))
    parser.add_argument("--templates-dir", type=Path, default=value_from_config(config, "templates_dir", "templates/prayer_potions"))
    parser.add_argument("--threshold-percent", type=float, default=value_from_config(config, "threshold_percent", 50.0))
    parser.add_argument("--check-seconds", type=float, default=value_from_config(config, "check_seconds", 5.0))
    parser.add_argument("--required-low-readings", type=int, default=value_from_config(config, "required_low_readings", 1))
    parser.add_argument("--cooldown-seconds", type=float, default=value_from_config(config, "cooldown_seconds", 8.0))
    parser.add_argument("--match-threshold", type=float, default=value_from_config(config, "match_threshold", 0.78))
    parser.add_argument("--template-scales", default=value_from_config(config, "template_scales", "1.0"))
    parser.add_argument("--countdown", type=float, default=value_from_config(config, "countdown", 2.0))
    parser.add_argument("--click-scale", type=float, default=value_from_config(config, "click_scale", 1.0))
    parser.add_argument("--spot-jitter", type=int, default=value_from_config(config, "spot_jitter", 3))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=value_from_config(config, "dry_run", True))
    args = parser.parse_args()

    templates_dir = args.templates_dir if args.templates_dir.is_absolute() else ROOT / args.templates_dir
    try:
        return run(
            monitor=args.monitor, bar_region=parse_region(args.bar_region),
            inventory_region=parse_region(args.inventory_region), templates_dir=templates_dir,
            threshold_percent=max(0.0, min(100.0, args.threshold_percent)),
            check_seconds=max(0.1, args.check_seconds), required_low_readings=max(1, args.required_low_readings),
            cooldown_seconds=max(0.0, args.cooldown_seconds), match_threshold=max(0.0, min(1.0, args.match_threshold)),
            template_scales=parse_scales(str(args.template_scales)), countdown=max(0.0, args.countdown),
            click_scale=max(0.01, args.click_scale), spot_jitter=max(0, args.spot_jitter), dry_run=args.dry_run,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
