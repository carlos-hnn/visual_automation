from __future__ import annotations

import random


def humanized_delay(base_seconds: float, jitter_seconds: float) -> float:
    jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
    return max(0.0, base_seconds + jitter)

