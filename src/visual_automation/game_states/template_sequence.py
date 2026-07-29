from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TemplateStep:
    index: int
    name: str
    template_path: Path
    wait_seconds: float


def parse_order(value: str) -> list[str]:
    order = [item.strip() for item in value.split(",") if item.strip()]
    if not order:
        raise ValueError("order must contain at least one template name")
    return order


def parse_waits(value: str, step_count: int) -> list[float]:
    waits = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(waits) == 1:
        return waits * step_count
    if len(waits) < step_count:
        raise ValueError(f"--waits must be one value or at least {step_count} values")
    return waits[:step_count]


def template_path_for_name(templates_dir: Path, name: str) -> Path:
    path = Path(name)
    if path.suffix:
        return path if path.is_absolute() else templates_dir / path
    return templates_dir / f"{name}.png"


def load_template_steps(templates_dir: Path, order: list[str], waits_value: str, limit: int | None) -> list[TemplateStep]:
    waits = parse_waits(waits_value, len(order))
    selected_order = order if limit is None else order[: max(0, limit)]
    selected_waits = waits if limit is None else waits[: max(0, limit)]
    steps = [
        TemplateStep(index=index, name=name, template_path=template_path_for_name(templates_dir, name), wait_seconds=wait)
        for index, (name, wait) in enumerate(zip(selected_order, selected_waits), start=1)
    ]
    missing = [str(step.template_path) for step in steps if not step.template_path.exists()]
    if missing:
        raise FileNotFoundError("Missing template(s): " + ", ".join(missing))
    return steps


def rotate_steps(steps: list[TemplateStep], start_at: str | None) -> list[TemplateStep]:
    if start_at is None:
        return steps
    start = start_at.strip()
    if not start:
        return steps

    for index, step in enumerate(steps):
        if step.name == start or step.template_path.stem == start or str(step.index) == start:
            return steps[index:] + steps[:index]
    valid = ", ".join(step.name for step in steps)
    raise ValueError(f"--start-at must be one of: {valid}")


def fallback_candidates(steps: list[TemplateStep], start_index: int) -> list[tuple[int, TemplateStep]]:
    if len(steps) <= 1 or start_index >= len(steps) - 1:
        return []
    return [(index, step) for index, step in enumerate(steps[start_index + 1 :], start=start_index + 1)]

