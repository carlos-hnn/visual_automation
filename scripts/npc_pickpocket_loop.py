from __future__ import annotations

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

from core.debug import save_annotated_match, save_screenshot
from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import Region, RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import ScreenCapture
from core.state_machine import AutomationState, StateMachine


@dataclass(frozen=True)
class CyanConfig:
    hue_min: int = 80
    hue_max: int = 100
    saturation_min: int = 80
    value_min: int = 100
    min_area: int = 80
    morph_open: int = 2
    dilate: int = 3


@dataclass(frozen=True)
class CyanTarget:
    x: int
    y: int
    width: int
    height: int
    area: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "npc_pickpocket.json"
    ensure_local_config(config_path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_cyan_config(config: dict[str, Any]) -> CyanConfig:
    raw = config.get("cyan_detection", {})
    return CyanConfig(
        hue_min=int(raw.get("hue_min", 80)),
        hue_max=int(raw.get("hue_max", 100)),
        saturation_min=int(raw.get("saturation_min", 80)),
        value_min=int(raw.get("value_min", 100)),
        min_area=int(raw.get("min_area", 80)),
        morph_open=int(raw.get("morph_open", 2)),
        dilate=int(raw.get("dilate", 3)),
    )


def runtime_seconds_from_config(config: dict[str, Any]) -> float | None:
    hours = config.get("max_runtime_hours")
    if hours is not None:
        return float(hours) * 60 * 60
    seconds = config.get("max_runtime_seconds")
    if seconds is None:
        return None
    return float(seconds)


def find_cyan_targets(frame_image: np.ndarray, frame_left: int, frame_top: int, cyan: CyanConfig) -> tuple[list[CyanTarget], np.ndarray]:
    hsv = cv2.cvtColor(frame_image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        (cyan.hue_min, cyan.saturation_min, cyan.value_min),
        (cyan.hue_max, 255, 255),
    )

    if cyan.morph_open > 0:
        kernel = np.ones((cyan.morph_open, cyan.morph_open), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    if cyan.dilate > 0:
        kernel = np.ones((cyan.dilate, cyan.dilate), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    targets: list[CyanTarget] = []
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if int(area) < cyan.min_area:
            continue
        targets.append(
            CyanTarget(
                x=frame_left + int(x),
                y=frame_top + int(y),
                width=int(width),
                height=int(height),
                area=int(area),
            )
        )
    return targets, mask


def choose_target(targets: list[CyanTarget], region: Region, mode: str) -> CyanTarget | None:
    if not targets:
        return None
    if mode == "nearest_to_center":
        center = (region.left + region.width // 2, region.top + region.height // 2)
        return min(targets, key=lambda item: (item.center[0] - center[0]) ** 2 + (item.center[1] - center[1]) ** 2)
    return max(targets, key=lambda item: item.area)


def click_target_or_log(
    target: CyanTarget,
    mouse: MouseController,
    dry_run: bool,
    logger: logging.Logger,
    offset: dict[str, Any],
) -> tuple[int, int]:
    x = target.center[0] + int(offset.get("x", 0))
    y = target.center[1] + int(offset.get("y", 0))
    if dry_run:
        click_point = mouse.point_near(x, y)
        mouse.move_to(*click_point)
        logger.info("dry-run would click cyan npc center=%s target=%s area=%s", target.center, click_point, target.area)
        return click_point
    click_point = mouse.click_point(x, y)
    logger.info("clicked cyan npc center=%s target=%s area=%s", target.center, click_point, target.area)
    return click_point


def main() -> int:
    config = load_config()
    logger = setup_logger(level=logging.DEBUG if config.get("debug") else logging.INFO)
    state = StateMachine(logger)

    dry_run = bool(config.get("dry_run", True))
    loop_delay = float(config.get("loop_delay_seconds", 1.0))
    cyan = build_cyan_config(config)
    debug_dir = ROOT / config.get("debug_dir", "logs/debug")
    target_selection = str(config.get("target_selection", "largest"))
    click_offset = config.get("click_offset", {})

    regions = RegionManager(ROOT / "config" / "regions.json")
    game_region = regions.get_region(str(config.get("region", "utm_runelite_game_view")))

    mouse_settings = config.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.18)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.38)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.12)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 4)),
            point_tolerance_pixels=int(mouse_settings.get("point_tolerance_pixels", 5)),
        )
    )

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

    with ScreenCapture(monitor=int(config.get("monitor", 1))) as screen:
        safety.start()
        state.transition_to(AutomationState.RUNNING, "npc pickpocket workflow started")
        try:
            while not safety.should_stop():
                frame = screen.capture(game_region)
                targets, mask = find_cyan_targets(frame.image, frame.left, frame.top, cyan)
                target = choose_target(targets, game_region, target_selection)
                logger.info("cyan targets=%s selection=%s", len(targets), None if target is None else target.center)

                if config.get("debug"):
                    save_screenshot(mask, debug_dir, "npc_pickpocket_cyan_mask")
                    if target is not None:
                        relative_top_left = (target.x - frame.left, target.y - frame.top)
                        relative_bottom_right = (relative_top_left[0] + target.width, relative_top_left[1] + target.height)
                        save_annotated_match(frame.image, relative_top_left, relative_bottom_right, float(target.area), debug_dir, "npc_pickpocket_target")

                if target is not None:
                    click_target_or_log(target, mouse, dry_run, logger, click_offset)

                time.sleep(loop_delay)

        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            logger.exception("npc pickpocket workflow failed")
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "npc pickpocket workflow stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
