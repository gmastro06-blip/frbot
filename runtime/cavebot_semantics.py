from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.runtime import MinimapMarker, Waypoint


@dataclass(frozen=True, slots=True)
class ProgressResult:
    delta_x_px: int
    delta_y_px: int
    delta_mag_px: int
    distance_before_px: float
    distance_after_px: float
    moved_toward_waypoint: bool
    in_expected_direction: bool


def _rgb_matches(r: int, g: int, b: int, *, target: tuple[int, int, int], tol: int) -> bool:
    tr, tg, tb = target
    return abs(int(r) - int(tr)) <= tol and abs(int(g) - int(tg)) <= tol and abs(int(b) - int(tb)) <= tol


def detect_player_marker(
    frame: Frame,
    *,
    marker_rgb: tuple[int, int, int] = (255, 0, 255),
    tol: int = 30,
    min_pixels: int = 5,
    max_pixels: int = 0,
) -> Optional[MinimapMarker]:
    """Detect player marker in the minimap crop.

    Pure function. No IO, no sleeps.

    Returns a marker centroid (x_px, y_px) in minimap pixel coordinates.
    """

    if not frame.minimap_detected:
        return None
    w = int(frame.minimap_width)
    h = int(frame.minimap_height)
    rgb = frame.minimap_rgb
    if w <= 0 or h <= 0:
        return None
    if not rgb or len(rgb) != (w * h * 3):
        return None

    total = 0
    sum_x = 0
    sum_y = 0

    # Scan all pixels deterministically.
    idx = 0
    for y in range(h):
        for x in range(w):
            r = rgb[idx]
            g = rgb[idx + 1]
            b = rgb[idx + 2]
            if _rgb_matches(r, g, b, target=marker_rgb, tol=int(tol)):
                total += 1
                sum_x += x
                sum_y += y
            idx += 3

    if total < int(min_pixels):
        return None
    if int(max_pixels) > 0 and total > int(max_pixels):
        return None

    cx = int(round(float(sum_x) / float(total)))
    cy = int(round(float(sum_y) / float(total)))
    if cx < 0 or cy < 0 or cx >= w or cy >= h:
        return None

    return MinimapMarker(x_px=cx, y_px=cy, pixel_count=int(total))


def compute_progress(before: MinimapMarker, after: MinimapMarker, waypoint: Waypoint) -> ProgressResult:
    """Compute objective movement evidence from marker BEFORE/AFTER.

    All coordinates are in minimap pixels.
    """

    dx = int(after.x_px) - int(before.x_px)
    dy = int(after.y_px) - int(before.y_px)
    mag = abs(dx) + abs(dy)

    tx = int(waypoint.x)
    ty = int(waypoint.y)

    # L2 distance is used only for the "reduces distance" check.
    dbx = float(int(before.x_px) - tx)
    dby = float(int(before.y_px) - ty)
    dax = float(int(after.x_px) - tx)
    day = float(int(after.y_px) - ty)
    dist_before = (dbx * dbx + dby * dby) ** 0.5
    dist_after = (dax * dax + day * day) ** 0.5

    moved_toward = dist_after < dist_before

    expected = str(waypoint.expected_direction).strip().upper()
    in_dir = False
    if expected == 'E':
        in_dir = dx > 0
    elif expected == 'W':
        in_dir = dx < 0
    elif expected == 'S':
        in_dir = dy > 0
    elif expected == 'N':
        in_dir = dy < 0

    return ProgressResult(
        delta_x_px=dx,
        delta_y_px=dy,
        delta_mag_px=int(mag),
        distance_before_px=float(dist_before),
        distance_after_px=float(dist_after),
        moved_toward_waypoint=bool(moved_toward),
        in_expected_direction=bool(in_dir),
    )


def is_progress_valid(progress: ProgressResult, waypoint: Waypoint) -> bool:
    """Return True only if movement is semantically valid progress."""

    if int(progress.delta_mag_px) < int(waypoint.min_pixel_delta):
        return False
    if not bool(progress.in_expected_direction):
        return False
    if not bool(progress.moved_toward_waypoint):
        return False
    return True
