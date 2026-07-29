from __future__ import annotations

import random
import time


def humanized_delay(base_seconds: float, jitter_seconds: float) -> float:
    jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
    return max(0.0, base_seconds + jitter)


def sleep_ticks(ticks: float, tick_seconds: float, jitter_seconds: float = 0.0) -> float:
    delay = humanized_delay(max(0.0, ticks) * max(0.0, tick_seconds), max(0.0, jitter_seconds))
    time.sleep(delay)
    return delay


def wait_ticks(label: str, ticks: float, args, dry_run: bool) -> float:
    delay = humanized_delay(ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {ticks:g} tick(s), {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)
    return delay
