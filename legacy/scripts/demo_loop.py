from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.debug import save_screenshot
from core.logger import setup_logger
from core.mouse import MouseConfig, MouseController
from core.regions import RegionManager
from core.safety import SafetyConfig, SafetyController
from core.screen import ScreenCapture
from core.state_machine import AutomationState, StateMachine
from core.vision import Vision


def load_settings(path: Path) -> dict:
    ensure_local_config(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return

    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def first_template() -> Path | None:
    templates_dir = ROOT / "assets" / "templates"
    for extension in ("*.png", "*.jpg", "*.jpeg"):
        templates = sorted(templates_dir.glob(extension))
        if templates:
            return templates[0]
    return None


def main() -> int:
    ensure_local_config(ROOT / "config" / "regions.json")
    settings = load_settings(ROOT / "config" / "settings.json")
    logger = setup_logger(level=logging.DEBUG if settings.get("debug") else logging.INFO)
    state = StateMachine(logger)

    configured_template = settings.get("template_path")
    template_path = ROOT / configured_template if configured_template else first_template()
    if template_path is None:
        logger.warning("No template found in assets/templates. Add a PNG/JPG and run the demo again.")
        state.transition_to(AutomationState.STOPPED, "missing template")
        return 0

    monitor = int(settings.get("monitor", 1))
    threshold = float(settings.get("template_threshold", 0.85))
    loop_delay = float(settings.get("loop_delay_seconds", 0.5))
    dry_run = bool(settings.get("dry_run", True))
    dry_run_move_mouse = bool(settings.get("dry_run_move_mouse_to_match", False))
    debug_enabled = bool(settings.get("debug", False))

    safety_settings = settings.get("safety", {})
    safety = SafetyController(
        logger,
        SafetyConfig(
            max_runtime_seconds=settings.get("max_runtime_seconds", 300),
            stop_hotkey=safety_settings.get("stop_hotkey", "cmd+shift+q"),
            enable_esc_stop=bool(safety_settings.get("enable_esc_stop", True)),
            failsafe_corner_pixels=int(safety_settings.get("failsafe_corner_pixels", 5)),
        ),
    )

    mouse_settings = settings.get("mouse", {})
    mouse = MouseController(
        MouseConfig(
            move_duration_min=float(mouse_settings.get("move_duration_min", 0.08)),
            move_duration_max=float(mouse_settings.get("move_duration_max", 0.25)),
            click_pause_seconds=float(mouse_settings.get("click_pause_seconds", 0.08)),
            random_offset_pixels=int(mouse_settings.get("random_offset_pixels", 4)),
        )
    )

    regions = RegionManager(ROOT / "config" / "regions.json")
    target_region = settings.get("target_region")
    region = regions.get_region(target_region) if target_region else None
    if target_region:
        logger.info("using target region: %s", target_region)

    with ScreenCapture(monitor=monitor) as screen:
        vision = Vision(
            screen=screen,
            debug_enabled=debug_enabled,
            debug_dir=ROOT / settings.get("debug_dir", "logs/debug"),
        )
        safety.start()
        state.transition_to(AutomationState.RUNNING, "demo started")

        try:
            while not safety.should_stop():
                match = vision.find_template(template_path, region=region, threshold=threshold)
                if match is not None:
                    if dry_run:
                        if dry_run_move_mouse:
                            mouse.move_to(*match.center)
                        logger.info(
                            "dry-run match template=%s center=%s score=%.3f",
                            template_path.name,
                            match.center,
                            match.score,
                        )
                    else:
                        clicked = mouse.click_match(match)
                        logger.info("clicked template=%s at=%s score=%.3f", template_path.name, clicked, match.score)
                else:
                    logger.debug("template not found: %s", template_path.name)
                time.sleep(loop_delay)
        except Exception:
            state.transition_to(AutomationState.ERROR, "unhandled exception")
            frame = screen.capture(region)
            output = save_screenshot(frame.image, ROOT / settings.get("error_screenshot_dir", "logs/errors"), "error")
            logger.exception("demo failed; screenshot saved at %s", output)
            return 1
        finally:
            safety.stop_listeners()
            if state.state != AutomationState.ERROR:
                state.transition_to(AutomationState.STOPPED, "demo stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
