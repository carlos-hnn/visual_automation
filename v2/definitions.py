from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path = ROOT / "config"
    logs_dir: Path = ROOT / "logs"
    debug_dir: Path = ROOT / "logs" / "debug"
    templates_dir: Path = ROOT / "templates"


@dataclass(frozen=True)
class TemplateSequenceDefaults:
    templates_dir: Path = ROOT / "templates" / "template_click_sequence"
    order: tuple[str, ...] = ("1", "2", "3", "4", "5", "6")
    waits: str = "5,21,13,12,4,10"
    template_scales: str = "0.35,0.4,0.45,0.5,0.55,0.6,0.65"
    threshold: float = 0.60
    spot_jitter_pixels: int = 4
    time_jitter_seconds: float = 0.1
    pre_click_jitter_seconds: float = 0.05
    move_duration_min: float = 0.16
    move_duration_max: float = 0.32

