from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.debug import save_annotated_match, save_screenshot
from core.regions import RegionManager
from core.screen import ScreenCapture
from core.terminal import install_timestamped_print
from core.vision import TemplateMatch

install_timestamped_print()


def first_template() -> Path | None:
    templates_dir = ROOT / "assets" / "templates"
    for extension in ("*.png", "*.jpg", "*.jpeg"):
        templates = sorted(templates_dir.glob(extension))
        if templates:
            return templates[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one debug screenshot and annotate the first template match.")
    parser.add_argument("--template", type=Path, default=None, help="Template path. Defaults to first image in assets/templates.")
    parser.add_argument("--threshold", type=float, default=0.85, help="Template matching threshold.")
    parser.add_argument("--monitor", type=int, default=1, help="MSS monitor index.")
    parser.add_argument("--region", type=str, default=None, help="Named region from config/regions.json.")
    args = parser.parse_args()

    template = args.template or first_template()
    if template is None:
        print("No template found in assets/templates.")
        return 1

    debug_dir = ROOT / "logs" / "debug"
    region = None
    if args.region:
        region = RegionManager(ROOT / "config" / "regions.json").get_region(args.region)

    with ScreenCapture(monitor=args.monitor) as screen:
        frame = screen.capture(region)
        raw_path = save_screenshot(frame.image, debug_dir, "raw")

        template_image = cv2.imread(str(template), cv2.IMREAD_COLOR)
        if template_image is None:
            print(f"Template image not found or unreadable: {template}")
            return 1
        if template_image.shape[0] > frame.image.shape[0] or template_image.shape[1] > frame.image.shape[1]:
            print("Template is larger than the captured screen.")
            return 1

        result = cv2.matchTemplate(frame.image, template_image, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        match = TemplateMatch(
            x=frame.left + max_loc[0],
            y=frame.top + max_loc[1],
            width=template_image.shape[1],
            height=template_image.shape[0],
            score=float(max_val),
        )
        relative_top_left = (match.x - frame.left, match.y - frame.top)
        relative_bottom_right = (relative_top_left[0] + match.width, relative_top_left[1] + match.height)
        prefix = "annotated" if match.score >= args.threshold else "best_candidate"
        candidate_path = save_annotated_match(
            frame.image,
            relative_top_left,
            relative_bottom_right,
            match.score,
            debug_dir,
            prefix,
        )

        print(f"Raw screenshot: {raw_path}")
        if match.score >= args.threshold:
            print(f"Template found at center={match.center} score={match.score:.3f}")
            print(f"Annotated screenshot: {candidate_path}")
        else:
            print(f"Template NOT found. Best candidate center={match.center} score={match.score:.3f}")
            print(f"Best candidate screenshot: {candidate_path}")
            print(f"Threshold was {args.threshold:.3f}; try a lower threshold only if the candidate image is actually correct.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
