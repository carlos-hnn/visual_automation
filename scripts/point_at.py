from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mouse import MouseController
from core.terminal import install_timestamped_print

install_timestamped_print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Move the mouse to an absolute screen coordinate without clicking.")
    parser.add_argument("x", type=int, help="Absolute screen X coordinate")
    parser.add_argument("y", type=int, help="Absolute screen Y coordinate")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait before moving")
    args = parser.parse_args()

    print(f"Moving mouse to ({args.x}, {args.y}) in {args.delay:.1f}s. No click will be performed.")
    time.sleep(args.delay)
    MouseController().move_to(args.x, args.y)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
