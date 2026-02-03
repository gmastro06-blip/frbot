from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.runtime import MinimapMarker, Waypoint


@dataclass(frozen=True, slots=True)
class ProgressResult:
    distance_before_px: float
    distance_after_px: float
    angle_deg: float
    moved_toward_waypoint: bool


def distance_to_waypoint(marker: MinimapMarker, waypoint: Waypoint) -> float:
    dx = float(int(marker.x_px) - int(waypoint.x))
    dy = float(int(marker.y_px) - int(waypoint.y))
    return float((dx * dx + dy * dy) ** 0.5)


def _angle_deg_between(ax: float, ay: float, bx: float, by: float) -> float:
    # Return angle in degrees between vectors a and b.
    # If either is zero-length, treat as 0 degrees (cannot assert wrong direction from no movement).
    amag = float((ax * ax + ay * ay) ** 0.5)
    bmag = float((bx * bx + by * by) ** 0.5)
    if amag <= 1e-9 or bmag <= 1e-9:
        return 0.0
    dot = (ax * bx + ay * by) / (amag * bmag)
    # Clamp for numeric stability.
    dot = max(-1.0, min(1.0, float(dot)))
    return float(math.degrees(math.acos(dot)))


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

    Rules:
    - progress is ONLY distance reduction (distance_after < distance_before)
    - wrong direction is angle(expected_vector, real_vector) > 90 degrees
    """

    dist_before = distance_to_waypoint(before, waypoint)
    dist_after = distance_to_waypoint(after, waypoint)

    exp_x = float(int(waypoint.x) - int(before.x_px))
    exp_y = float(int(waypoint.y) - int(before.y_px))
    real_x = float(int(after.x_px) - int(before.x_px))
    real_y = float(int(after.y_px) - int(before.y_px))
    angle = _angle_deg_between(exp_x, exp_y, real_x, real_y)

    moved_toward = dist_after < dist_before
    return ProgressResult(
        distance_before_px=float(dist_before),
        distance_after_px=float(dist_after),
        angle_deg=float(angle),
        moved_toward_waypoint=bool(moved_toward),
    )


def is_progress_valid(progress: ProgressResult, waypoint: Waypoint) -> bool:
    """Return True only if movement is semantically valid progress."""

    # ONLY distance reduction is progress.
    if not bool(progress.moved_toward_waypoint):
        return False

    # Direction correctness is enforced separately by angle.
    if float(progress.angle_deg) > 90.0:
        return False

    return True
