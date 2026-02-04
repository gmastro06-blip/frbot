from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.runtime import MinimapMarker, Waypoint


@dataclass(frozen=True, slots=True)
class MarkerCandidate:
    candidate_id: int
    x_px: int
    y_px: int
    pixel_count: int
    mean_rgb: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class MarkerSelection:
    marker: Optional[MinimapMarker]
    candidates: tuple[MarkerCandidate, ...]
    selected_candidate_id: Optional[int]
    confidence: float
    abort_reason: Optional[str]
    details: dict[str, object]


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


def _sample_luma_std(rgb: bytes, *, width: int, height: int, max_pixels: int = 20000) -> float:
    if int(width) <= 0 or int(height) <= 0:
        return 0.0
    expected = int(width) * int(height) * 3
    if expected <= 0 or len(rgb) != expected:
        return 0.0

    px = int(width) * int(height)
    step = max(1, px // int(max_pixels))

    mean = 0.0
    m2 = 0.0
    n = 0

    idx = 0
    for _ in range(0, px, step):
        r = rgb[idx]
        g = rgb[idx + 1]
        b = rgb[idx + 2]
        y = (float(r) + float(g) + float(b)) / 3.0
        n += 1
        delta = y - mean
        mean += delta / float(n)
        delta2 = y - mean
        m2 += delta * delta2
        idx += 3 * step
        if idx + 2 >= len(rgb):
            break

    if n <= 1:
        return 0.0
    var = m2 / float(n - 1)
    return float(var ** 0.5)


def _rgb_delta(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return float(((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2 + (float(a[2]) - float(b[2])) ** 2) ** 0.5)


def _component_candidates(
    frame: Frame,
    *,
    marker_rgb: tuple[int, int, int],
    tol: int,
    min_pixels: int,
    max_pixels: int,
) -> list[MarkerCandidate]:
    if not frame.minimap_detected:
        return []
    w = int(frame.minimap_width)
    h = int(frame.minimap_height)
    rgb = frame.minimap_rgb
    if w <= 0 or h <= 0:
        return []
    if not rgb or len(rgb) != (w * h * 3):
        return []

    tol_i = int(tol)
    visited = bytearray(w * h)

    def matches(pix: int) -> bool:
        i = pix * 3
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        return _rgb_matches(r, g, b, target=marker_rgb, tol=tol_i)

    out: list[MarkerCandidate] = []
    cid = 0

    for start in range(w * h):
        if visited[start]:
            continue
        visited[start] = 1
        if not matches(start):
            continue

        stack = [start]
        count = 0
        sum_x = 0
        sum_y = 0
        sum_r = 0
        sum_g = 0
        sum_b = 0

        while stack:
            pix = stack.pop()
            x = pix % w
            y = pix // w

            i = pix * 3
            r = int(rgb[i])
            g = int(rgb[i + 1])
            b = int(rgb[i + 2])

            count += 1
            sum_x += int(x)
            sum_y += int(y)
            sum_r += int(r)
            sum_g += int(g)
            sum_b += int(b)

            # 4-neighborhood
            if x > 0:
                n = pix - 1
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
                else:
                    visited[n] = visited[n] or 0
            if x + 1 < w:
                n = pix + 1
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
                else:
                    visited[n] = visited[n] or 0
            if y > 0:
                n = pix - w
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
                else:
                    visited[n] = visited[n] or 0
            if y + 1 < h:
                n = pix + w
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
                else:
                    visited[n] = visited[n] or 0

        if count < int(min_pixels):
            continue
        if int(max_pixels) > 0 and count > int(max_pixels):
            continue

        cx = int(round(float(sum_x) / float(count)))
        cy = int(round(float(sum_y) / float(count)))
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            continue

        mr = int(round(float(sum_r) / float(count)))
        mg = int(round(float(sum_g) / float(count)))
        mb = int(round(float(sum_b) / float(count)))

        out.append(
            MarkerCandidate(
                candidate_id=int(cid),
                x_px=int(cx),
                y_px=int(cy),
                pixel_count=int(count),
                mean_rgb=(int(mr), int(mg), int(mb)),
            )
        )
        cid += 1

    return out


def select_player_marker(
    frame: Frame,
    *,
    marker_rgb: tuple[int, int, int] = (255, 0, 255),
    tol: int = 30,
    min_pixels: int = 5,
    max_pixels: int = 0,
    prev_marker: Optional[MinimapMarker] = None,
) -> MarkerSelection:
    """Select a single stable marker from potentially multiple candidate blobs.

    Contract:
    - No sleeps, no IO.
    - If multiple candidates exist, select deterministically using:
      (a) proximity to previous tick marker
      (b) minimal RGB delta to configured marker_rgb
      (c) stable area within ±10% relative to previous marker area
    - If no candidate satisfies stability constraints => abort_reason=cavebot_marker_ambiguous.
    - Anti-noise guard: if full-frame is high-variance but minimap ROI is low-variance => abort_reason=cavebot_marker_roi_black.
    """

    details: dict[str, object] = {}

    # Anti-noise guard (best-effort; skip if frame buffers are missing).
    try:
        if bool(getattr(frame, 'rgb', b'')) and int(getattr(frame, 'width', 0)) > 0 and int(getattr(frame, 'height', 0)) > 0:
            full_std = _sample_luma_std(bytes(frame.rgb), width=int(frame.width), height=int(frame.height), max_pixels=15000)
            details['full_std_luma'] = float(full_std)

            if bool(getattr(frame, 'minimap_rgb', b'')) and int(getattr(frame, 'minimap_width', 0)) > 0 and int(getattr(frame, 'minimap_height', 0)) > 0:
                roi_std = _sample_luma_std(bytes(frame.minimap_rgb), width=int(frame.minimap_width), height=int(frame.minimap_height), max_pixels=8000)
                details['roi_std_luma'] = float(roi_std)

                # Guard thresholding: full frame is clearly not black, but the ROI is near-uniform.
                if float(full_std) > 10.0 and float(roi_std) < 1.5:
                    return MarkerSelection(
                        marker=None,
                        candidates=(),
                        selected_candidate_id=None,
                        confidence=0.0,
                        abort_reason='cavebot_marker_roi_black',
                        details={
                            **details,
                            'reason': 'cavebot_marker_roi_black',
                        },
                    )
    except Exception:
        pass

    candidates = _component_candidates(
        frame,
        marker_rgb=marker_rgb,
        tol=int(tol),
        min_pixels=int(min_pixels),
        max_pixels=int(max_pixels),
    )

    # Candidate inventory for auditing.
    cand_details: list[dict[str, object]] = []
    for c in candidates:
        cand_details.append(
            {
                'id': int(c.candidate_id),
                'x_px': int(c.x_px),
                'y_px': int(c.y_px),
                'pixel_count': int(c.pixel_count),
                'mean_rgb': [int(c.mean_rgb[0]), int(c.mean_rgb[1]), int(c.mean_rgb[2])],
                'rgb_delta': float(_rgb_delta(c.mean_rgb, marker_rgb)),
            }
        )
    details['marker_candidates'] = cand_details

    if not candidates:
        return MarkerSelection(marker=None, candidates=(), selected_candidate_id=None, confidence=0.0, abort_reason=None, details=details)

    if len(candidates) == 1:
        c0 = candidates[0]
        return MarkerSelection(
            marker=MinimapMarker(x_px=int(c0.x_px), y_px=int(c0.y_px), pixel_count=int(c0.pixel_count)),
            candidates=tuple(candidates),
            selected_candidate_id=int(c0.candidate_id),
            confidence=1.0,
            abort_reason=None,
            details={**details, 'selected_marker': int(c0.candidate_id)},
        )

    # Multiple candidates: stabilization layer.
    prev = prev_marker
    prev_pos = (int(prev.x_px), int(prev.y_px)) if prev is not None else None
    prev_area = int(prev.pixel_count) if prev is not None else None

    def dist_to_prev(c: MarkerCandidate) -> float:
        if prev_pos is None:
            return 0.0
        dx = float(int(c.x_px) - int(prev_pos[0]))
        dy = float(int(c.y_px) - int(prev_pos[1]))
        return float((dx * dx + dy * dy) ** 0.5)

    def area_ratio(c: MarkerCandidate) -> float:
        if prev_area is None or prev_area <= 0:
            return 0.0
        return float(abs(int(c.pixel_count) - int(prev_area)) / float(prev_area))

    eligible: list[MarkerCandidate] = []
    for c in candidates:
        if prev_area is not None and prev_area > 0:
            if float(area_ratio(c)) > 0.10:
                continue
        eligible.append(c)

    if not eligible:
        return MarkerSelection(
            marker=None,
            candidates=tuple(candidates),
            selected_candidate_id=None,
            confidence=0.0,
            abort_reason='cavebot_marker_ambiguous',
            details={
                **details,
                'reason': 'cavebot_marker_ambiguous',
                'selected_marker': None,
                'prev_marker': {'x_px': int(prev.x_px), 'y_px': int(prev.y_px), 'pixel_count': int(prev.pixel_count)} if prev is not None else None,
            },
        )

    # Rank by: distance to prev, rgb delta to target, area stability ratio.
    eligible_sorted = sorted(
        eligible,
        key=lambda c: (
            float(dist_to_prev(c)),
            float(_rgb_delta(c.mean_rgb, marker_rgb)),
            float(area_ratio(c)),
            int(c.y_px),
            int(c.x_px),
            -int(c.pixel_count),
        ),
    )

    best = eligible_sorted[0]

    # Confidence: inverse of a normalized composite score.
    max_dim = float(max(1, int(getattr(frame, 'minimap_width', 1)), int(getattr(frame, 'minimap_height', 1))))
    d_norm = float(dist_to_prev(best)) / max_dim if prev_pos is not None else 0.0
    rgb_norm = float(_rgb_delta(best.mean_rgb, marker_rgb)) / (255.0 * (3.0 ** 0.5))
    a_norm = float(area_ratio(best))
    score = 0.60 * d_norm + 0.30 * rgb_norm + 0.10 * a_norm
    conf = float(max(0.0, min(1.0, 1.0 - score)))

    return MarkerSelection(
        marker=MinimapMarker(x_px=int(best.x_px), y_px=int(best.y_px), pixel_count=int(best.pixel_count)),
        candidates=tuple(candidates),
        selected_candidate_id=int(best.candidate_id),
        confidence=float(conf),
        abort_reason=None,
        details={
            **details,
            'selected_marker': int(best.candidate_id),
            'selected_marker_confidence': float(conf),
        },
    )


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

    sel = select_player_marker(
        frame,
        marker_rgb=marker_rgb,
        tol=int(tol),
        min_pixels=int(min_pixels),
        max_pixels=int(max_pixels),
        prev_marker=None,
    )
    if sel.abort_reason is not None:
        return None
    return sel.marker


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
