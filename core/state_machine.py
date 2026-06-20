from __future__ import annotations

from enum import Enum
from logging import Logger


class AutomationState(str, Enum):
    INIT = "INIT"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    RECOVERY = "RECOVERY"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class StateMachine:
    _allowed: dict[AutomationState, set[AutomationState]] = {
        AutomationState.INIT: {AutomationState.IDLE, AutomationState.RUNNING, AutomationState.ERROR, AutomationState.STOPPED},
        AutomationState.IDLE: {AutomationState.RUNNING, AutomationState.STOPPED, AutomationState.ERROR},
        AutomationState.RUNNING: {AutomationState.IDLE, AutomationState.RECOVERY, AutomationState.ERROR, AutomationState.STOPPED},
        AutomationState.RECOVERY: {AutomationState.IDLE, AutomationState.RUNNING, AutomationState.ERROR, AutomationState.STOPPED},
        AutomationState.ERROR: {AutomationState.RECOVERY, AutomationState.STOPPED},
        AutomationState.STOPPED: set(),
    }

    def __init__(self, logger: Logger, initial: AutomationState = AutomationState.INIT) -> None:
        self.logger = logger
        self.state = initial
        self.logger.info("state initialized: %s", self.state.value)

    def transition_to(self, next_state: AutomationState, reason: str | None = None) -> None:
        if next_state not in self._allowed[self.state]:
            raise ValueError(f"Invalid transition: {self.state.value} -> {next_state.value}")

        previous = self.state
        self.state = next_state
        if reason:
            self.logger.info("state transition: %s -> %s | %s", previous.value, next_state.value, reason)
        else:
            self.logger.info("state transition: %s -> %s", previous.value, next_state.value)
