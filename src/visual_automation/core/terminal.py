from __future__ import annotations

import builtins
import sys
import time
from typing import Any

_ORIGINAL_PRINT = builtins.print
_INSTALLED = False


def timestamped_print(*args: Any, **kwargs: Any) -> None:
    output = kwargs.get("file", sys.stdout)
    if output not in (sys.stdout, sys.stderr):
        _ORIGINAL_PRINT(*args, **kwargs)
        return

    _ORIGINAL_PRINT(f"[{time.strftime('%H:%M')}]", *args, **kwargs)


def install_timestamped_print() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    builtins.print = timestamped_print
    _INSTALLED = True
