from __future__ import annotations

from typing import Protocol


class SafetySupervisor(Protocol):
    @property
    def stop_requested(self) -> bool: ...

    def report_progress(self, label: str = "") -> None: ...

    def report_failure(self, label: str = "") -> None: ...


_active_supervisor: SafetySupervisor | None = None


def set_active_supervisor(supervisor: SafetySupervisor | None) -> None:
    global _active_supervisor
    _active_supervisor = supervisor


def report_progress(label: str = "") -> None:
    if _active_supervisor is not None:
        _active_supervisor.report_progress(label)


def report_failure(label: str = "") -> None:
    if _active_supervisor is not None:
        _active_supervisor.report_failure(label)


def stop_requested() -> bool:
    return bool(_active_supervisor is not None and _active_supervisor.stop_requested)
