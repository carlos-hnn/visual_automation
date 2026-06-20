from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyautogui
from pynput import keyboard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logger
from core.safety import SafetyConfig, SafetyController
from core.terminal import install_timestamped_print

install_timestamped_print()


@dataclass(frozen=True)
class Hotkeys:
    right_click: str = "j"
    move_down: str = "k"
    left_click: str = "l"
    count_add: str = "="
    count_subtract: str = "-"
    count_reset: str = "0"
    suspend: str = "f12"


@dataclass(frozen=True)
class Movement:
    move_down_pixels: int = 40


def ensure_local_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def load_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "motherlode_hotkeys.json"
    ensure_local_config(config_path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_hotkeys(config: dict[str, Any]) -> Hotkeys:
    raw = config.get("hotkeys", {})
    return Hotkeys(
        right_click=str(raw.get("right_click", "j")).lower(),
        move_down=str(raw.get("move_down", "k")).lower(),
        left_click=str(raw.get("left_click", "l")).lower(),
        count_add=str(raw.get("count_add", "=")).lower(),
        count_subtract=str(raw.get("count_subtract", "-")).lower(),
        count_reset=str(raw.get("count_reset", "0")).lower(),
        suspend=str(raw.get("suspend", "f12")).lower(),
    )


def build_movement(config: dict[str, Any]) -> Movement:
    raw = config.get("movement", {})
    return Movement(move_down_pixels=int(raw.get("move_down_pixels", 40)))


class MotherlodeAssistant:
    def __init__(self, config: dict[str, Any]) -> None:
        self.logger = setup_logger()
        self.hotkeys = build_hotkeys(config)
        self.movement = build_movement(config)
        self.inventory_count = 0
        self.suspended = not bool(config.get("enabled", True))
        self._pressed: set[str] = set()
        self._lock = threading.Lock()

        safety_settings = config.get("safety", {})
        self.safety = SafetyController(
            self.logger,
            SafetyConfig(
                max_runtime_seconds=config.get("max_runtime_seconds"),
                stop_hotkey=safety_settings.get("stop_hotkey", "cmd+shift+q"),
                enable_esc_stop=bool(safety_settings.get("enable_esc_stop", True)),
                failsafe_corner_pixels=int(safety_settings.get("failsafe_corner_pixels", 5)),
            ),
        )

    def run(self) -> int:
        pyautogui.FAILSAFE = False
        self.safety.start()
        self._print_status("started")
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.start()

        try:
            while not self.safety.should_stop():
                time.sleep(0.05)
        finally:
            listener.stop()
            self.safety.stop_listeners()
            self._print_status("stopped")
        return 0

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized is None:
            return

        with self._lock:
            if normalized in self._pressed:
                return
            self._pressed.add(normalized)

        if normalized == self.hotkeys.suspend:
            self.suspended = not self.suspended
            self._print_status("suspended" if self.suspended else "resumed")
            return

        if self.suspended:
            return

        if normalized == self.hotkeys.right_click:
            pyautogui.click(button="right")
            self._print_status("right click")
            return

        if normalized == self.hotkeys.move_down:
            pyautogui.moveRel(0, self.movement.move_down_pixels, duration=0)
            self._print_status(f"move down {self.movement.move_down_pixels}px")
            return

        if normalized == self.hotkeys.left_click:
            pyautogui.click(button="left")
            self._print_status("left click")
            return

        if normalized == self.hotkeys.count_add:
            self.inventory_count += 1
            self._print_status("count add")
            return

        if normalized == self.hotkeys.count_subtract:
            self.inventory_count = max(0, self.inventory_count - 1)
            self._print_status("count subtract")
            return

        if normalized == self.hotkeys.count_reset:
            self.inventory_count = 0
            self._print_status("count reset")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized is None:
            return
        with self._lock:
            self._pressed.discard(normalized)

    def _normalize_key(self, key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        if key == keyboard.Key.f12:
            return "f12"
        if key == keyboard.Key.esc:
            self.safety.stop()
            return "esc"
        return None

    def _print_status(self, event: str) -> None:
        state = "paused" if self.suspended else "active"
        message = f"motherlode | {state} | count={self.inventory_count:02d} | {event}"
        print(message, flush=True)
        self.logger.info(message)


def main() -> int:
    config = load_config()
    return MotherlodeAssistant(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
