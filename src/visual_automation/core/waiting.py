from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WaitPolicy:
    timeout: float = 30.0
    interval: float = 0.15


def wait_until(
    label: str,
    predicate: Callable[[], bool],
    stop_keys: Any,
    policy: WaitPolicy,
    *,
    log_waiting: bool = False,
) -> bool:
    """Poll a state predicate until it succeeds, times out, or is stopped."""
    deadline = time.monotonic() + max(0.0, policy.timeout)
    while not stop_keys.stop_requested and time.monotonic() < deadline:
        if predicate():
            print(f"{label}: confirmed")
            return True
        if log_waiting:
            print(f"{label}: waiting")
        time.sleep(max(0.01, policy.interval))
    print(f"{label}: timed out")
    return False
