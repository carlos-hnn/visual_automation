from __future__ import annotations

import random
import time


def humanized_delay(base_seconds: float, jitter_seconds: float) -> float:
    jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
    return max(0.0, base_seconds + jitter)


def wait_ticks(label: str, ticks: float, args, dry_run: bool) -> float:
    delay = humanized_delay(ticks * args.tick_seconds, args.time_jitter)
    print(f"{label}: waiting {ticks:g} tick(s), {delay:.2f}s")
    if not dry_run:
        time.sleep(delay)
    return delay
