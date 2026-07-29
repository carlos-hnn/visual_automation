from __future__ import annotations

import threading
import time

from pynput import keyboard

from visual_automation.core.safety import set_active_supervisor


class StopKeys:
    def __init__(self, failure_timeout_seconds: float = 30.0) -> None:
        self.stop_requested = False
        self.stop_reason = ""
        self.failure_timeout_seconds = max(0.1, float(failure_timeout_seconds))
        self._pressed: set[str] = set()
        self._listener: keyboard.Listener | None = None
        self._watchdog: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._failures: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        set_active_supervisor(self)
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        self._watchdog_stop.clear()
        self._watchdog = threading.Thread(target=self._watch_failures, name="automation-watchdog", daemon=True)
        self._watchdog.start()
        print(
            f"Safety: global Esc enabled; failure watchdog={self.failure_timeout_seconds:g}s "
            "(arms when visual targets are missing)."
        )

    def stop(self) -> None:
        self._watchdog_stop.set()
        if self._watchdog is not None and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=1)
        if self._listener is not None:
            self._listener.stop()
            self._listener.join(timeout=1)
        set_active_supervisor(None)

    def report_progress(self, label: str = "") -> None:
        with self._lock:
            self._failures.pop(self._failure_channel(label), None)

    def report_failure(self, label: str = "") -> None:
        channel = self._failure_channel(label)
        with self._lock:
            if channel not in self._failures:
                self._failures[channel] = (time.monotonic(), label)

    @staticmethod
    def _failure_channel(label: str) -> str:
        # Alternative templates are one search channel: finding any valid fallback
        # resolves that search. Other named targets remain independent.
        if label.startswith("template:"):
            return "template"
        if label.startswith("motherlode:"):
            return "motherlode-template"
        return label or "visual-target"

    def request_stop(self, reason: str) -> None:
        if self.stop_requested:
            return
        self.stop_reason = reason
        self.stop_requested = True
        print(f"Safety stop: {reason}")

    def _watch_failures(self) -> None:
        while not self._watchdog_stop.wait(0.2):
            with self._lock:
                failures = list(self._failures.values())
            if not failures:
                continue
            started_at, label = min(failures, key=lambda item: item[0])
            elapsed = time.monotonic() - started_at
            if elapsed >= self.failure_timeout_seconds:
                detail = f" Last missing target: {label}." if label else ""
                self.request_stop(
                    f"no successful visual target/progress for {self.failure_timeout_seconds:g}s.{detail}"
                )
                return

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed.add(normalized)
        if (
            normalized == "esc"
            or {"cmd", "shift", "q"}.issubset(self._pressed)
            or {"cmd", "c"}.issubset(self._pressed)
            or {"ctrl", "c"}.issubset(self._pressed)
        ):
            self.request_stop("global stop key pressed")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed.discard(normalized)

    def _normalize_key(self, key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if key in {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r}:
            return "cmd"
        if key in {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}:
            return "shift"
        if key in {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}:
            return "ctrl"
        if key == keyboard.Key.esc:
            return "esc"
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        return None
