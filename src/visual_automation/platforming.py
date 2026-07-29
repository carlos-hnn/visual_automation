from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from visual_automation.config import value_from_config
from visual_automation.definitions import ROOT

PlatformName = Literal["mac", "windows"]
PlatformOption = Literal["auto", "mac", "windows"]


def detect_platform() -> PlatformName:
    if sys.platform.startswith("win"):
        return "windows"
    return "mac"


def resolve_platform(value: object = "auto") -> PlatformName:
    requested = str(value or "auto").strip().lower()
    if requested in {"auto", ""}:
        return detect_platform()
    if requested in {"mac", "darwin", "osx", "macos"}:
        return "mac"
    if requested in {"windows", "win", "win32"}:
        return "windows"
    raise ValueError(f"Unknown platform: {value}; expected auto, mac, or windows")


def add_platform_argument(parser, config: dict[str, Any]) -> None:
    parser.add_argument(
        "--platform",
        choices=("auto", "mac", "windows"),
        default=str(value_from_config(config, "platform", "auto")).lower(),
        help="Template/platform profile to use; auto detects the current OS",
    )


def resolve_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def platform_template_dir(base_dir: str | Path, config: dict[str, Any], platform: PlatformName) -> Path:
    dirs_by_platform = value_from_config(config, "template_dirs_by_platform", {})
    if isinstance(dirs_by_platform, dict):
        raw_platform_dir = dirs_by_platform.get(platform)
        if raw_platform_dir:
            platform_dir = resolve_path(raw_platform_dir)
            if platform_dir.exists():
                return platform_dir

    base = resolve_path(base_dir)
    platform_child = base / platform
    if platform_child.exists():
        return platform_child
    return base
