from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def recognized_text(output: Any) -> str:
    """Normalize RapidOCR 1.x tuple and 3.x object results to plain text."""
    result = output[0] if isinstance(output, tuple) else output
    texts = getattr(result, "txts", None)
    if texts is not None:
        return " | ".join(str(text) for text in texts if text)
    if not isinstance(result, Iterable):
        return ""
    rows: list[str] = []
    for row in result or []:
        if isinstance(row, (list, tuple)) and len(row) > 1 and row[1]:
            rows.append(str(row[1]))
    return " | ".join(rows)
