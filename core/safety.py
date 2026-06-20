from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from logging import Logger

import pyautogui
from pynput import keyboard


@dataclass(frozen=True)
class SafetyConfig:
    max_runtime_seconds: float | None = 300
    stop_hotkey: str = "cmd+shift+q"
    enable_esc_stop: bool = True
    failsafe_corner_pixels: int = 5


class SafetyController:
    def __init__(self, logger: Logger, config: SafetyConfig | None = None) -> None:
        self.logger = logger
        self.config = config or SafetyConfig()
        self.started_at = time.monotonic()
        self._stop_event = threading.Event()
        self._listener: keyboard.Listener | None = None
        self._pressed_keys: set[str] = set()
        self._hotkey_parts = set(self._normalized_hotkey_parts(self.config.stop_hotkey))

    def start(self) -> None:
        if self.config.enable_esc_stop or self._hotkey_parts:
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.start()

        self.logger.info("safety started: hotkey=%s esc=%s", self.config.stop_hotkey, self.config.enable_esc_stop)

    def stop(self) -> None:
        if not self._stop_event.is_set():
            self.logger.warning("stop requested")
        self._stop_event.set()

    def stop_listeners(self) -> None:
        if self._listener is not None:
            self._listener.stop()

    def should_stop(self) -> bool:
        if self._stop_event.is_set():
            return True
        if self._max_runtime_reached():
            self.logger.warning("max runtime reached")
            self.stop()
            return True
        if self._mouse_in_fail_safe_corner():
            self.logger.warning("mouse fail-safe corner reached")
            self.stop()
            return True
        return False

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized is not None:
            self._pressed_keys.add(normalized)

        if key == keyboard.Key.esc:
            self.stop()
            return

        if self._hotkey_parts and self._hotkey_parts.issubset(self._pressed_keys):
            self.stop()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized is not None:
            self._pressed_keys.discard(normalized)

    def _max_runtime_reached(self) -> bool:
        if self.config.max_runtime_seconds is None:
            return False
        return time.monotonic() - self.started_at >= self.config.max_runtime_seconds

    def _mouse_in_fail_safe_corner(self) -> bool:
        margin = max(1, self.config.failsafe_corner_pixels)
        x, y = pyautogui.position()
        return x <= margin and y <= margin

    def _normalized_hotkey_parts(self, value: str) -> list[str]:
        parts: list[str] = []
        for part in value.lower().split("+"):
            part = part.strip()
            if part in {"cmd", "command", "super"}:
                parts.append("cmd")
            elif part in {"ctrl", "control"}:
                parts.append("ctrl")
            elif part in {"shift"}:
                parts.append("shift")
            elif part in {"alt", "option"}:
                parts.append("alt")
            else:
                parts.append(part)
        return parts

    def _normalize_key(self, key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if key in {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r}:
            return "cmd"
        if key in {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}:
            return "ctrl"
        if key in {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}:
            return "shift"
        if key in {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r}:
            return "alt"
        if key == keyboard.Key.esc:
            return "esc"
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        return None
