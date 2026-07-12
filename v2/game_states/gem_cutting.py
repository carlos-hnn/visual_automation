from __future__ import annotations

from v2.game_states.template_state import TemplateState
from v2.game_states.woodcut_firemake import WoodcutFiremakeState


class GemCuttingState(WoodcutFiremakeState):
    """Template-backed observations used by the gem-cutting flow."""

    def first_present(
        self,
        templates: list[TemplateState],
        timeout: float = 0.0,
    ) -> tuple[TemplateState | None, object | None, float, float]:
        best: tuple[TemplateState | None, object | None, float, float] = (None, None, -1.0, 1.0)
        for template in templates:
            match, score, scale = self.find(template, timeout)
            if score > best[2]:
                best = (template, match, score, scale)
            if match is not None:
                return template, match, score, scale
        return best
