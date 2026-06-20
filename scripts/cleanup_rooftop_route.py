from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.terminal import install_timestamped_print

install_timestamped_print()


@dataclass(frozen=True)
class CropResult:
    box: tuple[int, int, int, int]
    method: str
    confidence: str


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def clamp_box(left: int, top: int, width: int, height: int, image_width: int, image_height: int) -> tuple[int, int, int, int]:
    left = max(0, min(left, image_width - 1))
    top = max(0, min(top, image_height - 1))
    right = max(left + 1, min(image_width, left + width))
    bottom = max(top + 1, min(image_height, top + height))
    return left, top, right - left, bottom - top


def padded_box(box: tuple[int, int, int, int], padding: int, image_width: int, image_height: int) -> tuple[int, int, int, int]:
    left, top, width, height = box
    return clamp_box(left - padding, top - padding, width + padding * 2, height + padding * 2, image_width, image_height)


def click_centered_box(click_x: int, click_y: int, size: int, image_width: int, image_height: int) -> tuple[int, int, int, int]:
    half = size // 2
    return clamp_box(click_x - half, click_y - half, size, size, image_width, image_height)


def red_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low = cv2.inRange(hsv, np.array([0, 45, 35]), np.array([16, 255, 230]))
    high = cv2.inRange(hsv, np.array([164, 45, 35]), np.array([179, 255, 230]))
    mask = cv2.bitwise_or(low, high)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def red_outline_mask(image: np.ndarray, red_min: int = 95) -> np.ndarray:
    blue, green, red = cv2.split(image)
    red_i = red.astype(np.int16)
    green_i = green.astype(np.int16)
    blue_i = blue.astype(np.int16)
    mask = (
        (red_i >= red_min)
        & (green_i <= 90)
        & (blue_i <= 90)
        & ((red_i - green_i) >= 25)
        & ((red_i - blue_i) >= 25)
    ).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def green_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 55, 45]), np.array([95, 255, 255]))
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def green_outline_mask(image: np.ndarray, green_min: int = 100) -> np.ndarray:
    blue, green, red = cv2.split(image)
    red_i = red.astype(np.int16)
    green_i = green.astype(np.int16)
    blue_i = blue.astype(np.int16)
    mask = (
        (green_i >= green_min)
        & ((green_i - red_i) >= 30)
        & ((green_i - blue_i) >= 20)
    ).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def contour_candidates(mask: np.ndarray, origin: tuple[int, int], click: tuple[int, int]) -> list[tuple[float, tuple[int, int, int, int], float]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, tuple[int, int, int, int], float]] = []
    origin_x, origin_y = origin
    click_x, click_y = click
    for contour in contours:
        area = float(cv2.contourArea(contour))
        x, y, width, height = cv2.boundingRect(contour)
        if area < 70 or width < 8 or height < 8:
            continue

        abs_box = (origin_x + x, origin_y + y, width, height)
        center_x = abs_box[0] + abs_box[2] / 2
        center_y = abs_box[1] + abs_box[3] / 2
        distance = float(np.hypot(center_x - click_x, center_y - click_y))
        shape_bonus = max(width, height) / max(1, min(width, height))
        score = distance - area * 0.001 - shape_bonus * 6
        candidates.append((score, abs_box, area))
    return sorted(candidates, key=lambda item: item[0])


def find_obstacle_crop(
    image: np.ndarray,
    click_x: int,
    click_y: int,
    search_radius: int,
    fallback_size: int,
    padding: int,
    color: str,
) -> CropResult:
    image_height, image_width = image.shape[:2]
    search_left, search_top, search_width, search_height = click_centered_box(
        click_x,
        click_y,
        search_radius * 2,
        image_width,
        image_height,
    )
    search = image[search_top : search_top + search_height, search_left : search_left + search_width]

    if color == "green":
        outline_candidates: list[tuple[float, tuple[int, int, int, int], float]] = []
        outline_method = "green_outline_near_click"
        for green_min in (120, 100, 90):
            outline_candidates = [
                candidate
                for candidate in contour_candidates(green_outline_mask(search, green_min=green_min), (search_left, search_top), (click_x, click_y))
                if candidate[2] >= 70
            ]
            if outline_candidates:
                if green_min < 120:
                    outline_method = "green_outline_soft_near_click"
                break
        fallback_mask = green_mask(search)
        fallback_method = "green_contour_near_click"
    else:
        outline_candidates = []
        outline_method = "red_outline_strict_near_click"
        for red_min in (140, 120, 95):
            outline_candidates = [
                candidate
                for candidate in contour_candidates(red_outline_mask(search, red_min=red_min), (search_left, search_top), (click_x, click_y))
                if candidate[2] >= 250
            ]
            if outline_candidates:
                if red_min < 140:
                    outline_method = "red_outline_near_click"
                break
        fallback_mask = red_mask(search)
        fallback_method = "red_contour_near_click"

    if outline_candidates:
        _, box, area = outline_candidates[0]
        confidence = "high" if area >= 250 else "medium"
        return CropResult(padded_box(box, padding, image_width, image_height), outline_method, confidence)

    broad_candidates = [
        candidate
        for candidate in contour_candidates(fallback_mask, (search_left, search_top), (click_x, click_y))
        if candidate[2] < search_width * search_height * 0.80
    ]
    if broad_candidates:
        _, box, area = broad_candidates[0]
        confidence = "medium" if area >= 250 else "review"
        return CropResult(padded_box(box, padding, image_width, image_height), fallback_method, confidence)

    return CropResult(click_centered_box(click_x, click_y, fallback_size, image_width, image_height), "click_centered_fallback", "review")


def save_crop(image: np.ndarray, box: tuple[int, int, int, int], path: Path) -> None:
    left, top, width, height = box
    crop = image[top : top + height, left : left + width]
    cv2.imwrite(str(path), crop)


def save_annotated(image: np.ndarray, box: tuple[int, int, int, int], click: tuple[int, int], path: Path) -> None:
    annotated = image.copy()
    left, top, width, height = box
    cv2.rectangle(annotated, (left, top), (left + width, top + height), (0, 255, 255), 2)
    cv2.drawMarker(annotated, click, (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=24, thickness=2)
    cv2.imwrite(str(path), annotated)


def make_contact_sheet(paths: list[Path], output: Path, thumb_width: int = 220, thumb_height: int = 160) -> None:
    if not paths:
        return
    thumbs: list[np.ndarray] = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        thumb = np.zeros((thumb_height, thumb_width, 3), dtype=np.uint8)
        scale = min(thumb_width / image.shape[1], (thumb_height - 24) / image.shape[0])
        resized = cv2.resize(image, (max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale))))
        y = 24
        x = (thumb_width - resized.shape[1]) // 2
        thumb[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        cv2.putText(thumb, path.stem, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        thumbs.append(thumb)

    cols = min(4, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = np.zeros((rows * thumb_height, cols * thumb_width, 3), dtype=np.uint8)
    for index, thumb in enumerate(thumbs):
        y = (index // cols) * thumb_height
        x = (index % cols) * thumb_width
        sheet[y : y + thumb_height, x : x + thumb_width] = thumb
    cv2.imwrite(str(output), sheet)


def cleanup_route(input_route: Path, output_dir: Path, variant: str, search_radius: int, fallback_size: int, padding: int, color: str) -> int:
    source_dir = input_route.parent
    payload = load_json(input_route)
    output_dir.mkdir(parents=True, exist_ok=True)
    route_dir = output_dir / f"{source_dir.name}_{variant}"
    if route_dir.exists():
        shutil.rmtree(route_dir)
    route_dir.mkdir(parents=True)

    templates_dir = route_dir / "obstacle_templates"
    annotated_dir = route_dir / "annotated_obstacles"
    snapshots_dir = route_dir / "snapshots"
    templates_dir.mkdir()
    annotated_dir.mkdir()
    snapshots_dir.mkdir()

    events: list[dict[str, Any]] = []
    template_paths: list[Path] = []
    review_count = 0
    for event in payload.get("events", []):
        index = int(event["index"])
        snapshot_source = source_dir / event["snapshot_path"]
        image = cv2.imread(str(snapshot_source))
        if image is None:
            raise FileNotFoundError(snapshot_source)
        click_x = int(event["capture_relative"]["x"])
        click_y = int(event["capture_relative"]["y"])

        result = find_obstacle_crop(image, click_x, click_y, search_radius, fallback_size, padding, color)
        if result.confidence == "review":
            review_count += 1

        snapshot_target = snapshots_dir / f"click_{index:03d}_region.png"
        template_target = templates_dir / f"click_{index:03d}_{variant}_obstacle.png"
        annotated_target = annotated_dir / f"click_{index:03d}_{variant}_annotated.png"
        shutil.copyfile(snapshot_source, snapshot_target)
        save_crop(image, result.box, template_target)
        save_annotated(image, result.box, (click_x, click_y), annotated_target)
        template_paths.append(template_target)

        cleaned = dict(event)
        cleaned["source_template_path"] = event.get("template_path")
        cleaned["snapshot_path"] = str(snapshot_target.relative_to(route_dir))
        cleaned["annotated_path"] = str(annotated_target.relative_to(route_dir))
        cleaned["template_path"] = str(template_target.relative_to(route_dir))
        cleaned["template_box"] = {"left": result.box[0], "top": result.box[1], "width": result.box[2], "height": result.box[3]}
        cleaned["template_cleanup"] = {
            "variant": variant,
            "color": color,
            "method": result.method,
            "confidence": result.confidence,
            "search_radius": search_radius,
            "padding": padding,
        }
        events.append(cleaned)

    cleaned_payload = dict(payload)
    cleaned_payload["kind"] = f"{payload.get('kind', 'rooftop_route')}_{variant}_cleaned"
    cleaned_payload["source_route"] = str(input_route)
    cleaned_payload["events"] = events
    cleaned_payload["cleanup"] = {
        "variant": variant,
        "color": color,
        "search_radius": search_radius,
        "fallback_size": fallback_size,
        "padding": padding,
        "review_count": review_count,
    }
    write_json(route_dir / f"route.{variant}.json", cleaned_payload)
    make_contact_sheet(template_paths, route_dir / f"{variant}_template_contact_sheet.png")
    make_contact_sheet(sorted(annotated_dir.glob("*.png")), route_dir / f"{variant}_annotated_contact_sheet.png", thumb_width=287, thumb_height=420)
    write_summary(route_dir, events, variant)
    print(f"Wrote cleaned route: {route_dir / f'route.{variant}.json'}")
    print(f"Review crops: {review_count}")
    return 0


def write_summary(route_dir: Path, events: list[dict[str, Any]], variant: str) -> None:
    lines = [
        f"# Rooftop Route Cleanup: {variant}",
        "",
        "| # | Delay | Region | Method | Confidence | Template |",
        "|---:|---:|---|---|---|---|",
    ]
    for event in events:
        cleanup = event["template_cleanup"]
        lines.append(
            f"| {event['index']} | {event['delay_seconds']:.2f}s | {event['grid_region']} | "
            f"{cleanup['method']} | {cleanup['confidence']} | {event['template_path']} |"
        )
    (route_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean rooftop route templates into narrower obstacle crops.")
    parser.add_argument("--input", type=Path, required=True, help="Input route.json")
    parser.add_argument("--output-dir", type=Path, default=Path("records/rooftop_routes_cleaned"), help="Output directory")
    parser.add_argument("--variant", default="red_fallback", help="Route variant name")
    parser.add_argument("--color", choices=("red", "green"), default="red", help="Obstacle highlight color to clean")
    parser.add_argument("--search-radius", type=int, default=210, help="Pixels around click to search for obstacle highlight")
    parser.add_argument("--fallback-size", type=int, default=56, help="Fallback square crop size when no highlight contour is found")
    parser.add_argument("--padding", type=int, default=6, help="Padding around detected obstacle")
    args = parser.parse_args()
    return cleanup_route(
        input_route=args.input,
        output_dir=args.output_dir,
        variant=args.variant,
        search_radius=max(32, args.search_radius),
        fallback_size=max(16, args.fallback_size),
        padding=max(0, args.padding),
        color=args.color,
    )


if __name__ == "__main__":
    raise SystemExit(main())
