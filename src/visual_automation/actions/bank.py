from __future__ import annotations

from dataclasses import dataclass

from visual_automation.actions.templates import TemplateActions
from visual_automation.game_states.bank import BankStatus, detect_bank_status
from visual_automation.game_states.template_state import TemplateState


@dataclass
class BankActions:
    templates: dict[str, TemplateState]
    clicks: TemplateActions

    def status(self, timeout: float = 0.0) -> BankStatus:
        return detect_bank_status(self.clicks.state, self.templates["deposit_all"], timeout)

    def open(self, template_name: str = "bank") -> bool:
        if self.status().is_open:
            print("bank: already open")
            return True
        return self.clicks.find_and_click(self.templates[template_name])

    def deposit_all(self) -> bool:
        return self.clicks.find_and_click(self.templates["deposit_all"])

    def withdraw(self, template_name: str) -> bool:
        return self.clicks.find_and_click(self.templates[template_name])

    def close(self) -> bool:
        return self.clicks.find_and_click(self.templates["bank_close"])
