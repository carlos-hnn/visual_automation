from __future__ import annotations

from pynput import keyboard


class StopKeys:
    def __init__(self) -> None:
        self.stop_requested = False
        self._pressed: set[str] = set()
        self._listener: keyboard.Listener | None = None

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener.join(timeout=1)

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
            self.stop_requested = True

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
