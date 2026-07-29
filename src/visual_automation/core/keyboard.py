from __future__ import annotations

import random
import time
from dataclasses import dataclass

import pyautogui


@dataclass(frozen=True)
class KeyboardConfig:
    press_pause_min: float = 0.05
    press_pause_max: float = 0.12


class KeyboardController:
    def __init__(self, config: KeyboardConfig | None = None) -> None:
        self.config = config or KeyboardConfig()
        pyautogui.FAILSAFE = False

    def press(self, key: str) -> None:
        pyautogui.press(key)
        time.sleep(random.uniform(self.config.press_pause_min, self.config.press_pause_max))
