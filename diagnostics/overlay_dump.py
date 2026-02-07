from __future__ import annotations

import re
import time
from pathlib import Path

from contracts.capture import Frame
from contracts.evidence import Roi


def _ppm_p6_header(width: int, height: int) -> bytes:
    return f"P6\n{int(width)} {int(height)}\n255\n".encode("ascii")


def _safe_reason(reason: str) -> str:
    s = (reason or 'unknown').strip().lower()
    s = re.sub(r'[^a-z0-9._-]+', '_', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s[:80] if s else 'unknown'


def _draw_cross(rgb: bytearray, *, width: int, height: int, x: int, y: int, size: int = 4) -> None:
    if width <= 0 or height <= 0:
        return
    if x < 0 or y < 0 or x >= width or y >= height:
        return

    def set_px(px: int, py: int, r: int, g: int, b: int) -> None:
        if px < 0 or py < 0 or px >= width or py >= height:
            return
        i = (py * width + px) * 3
        if i < 0 or i + 2 >= len(rgb):
            return
        rgb[i] = int(r) & 0xFF
        rgb[i + 1] = int(g) & 0xFF
        rgb[i + 2] = int(b) & 0xFF

    # Red cross with white center.
    set_px(x, y, 255, 255, 255)
    for d in range(1, int(size) + 1):
        set_px(x - d, y, 255, 0, 0)
        set_px(x + d, y, 255, 0, 0)
        set_px(x, y - d, 255, 0, 0)
        set_px(x, y + d, 255, 0, 0)


def dump_marker_centroid_overlay(
    *,
    frames_dir: Path,
    frame: Frame,
    minimap_roi: Roi,
    centroid_x_minimap: float,
    centroid_y_minimap: float,
    reason: str,
) -> None:
    """Dump a single full-frame PPM with a cross at the detected marker centroid.

    Centroid coordinates are in minimap-crop pixel space (relative to ROI).
    """

    if not frame.rgb or int(frame.width) <= 0 or int(frame.height) <= 0:
        return

    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    try:
        abs_x = int(round(float(minimap_roi.x) + float(centroid_x_minimap)))
        abs_y = int(round(float(minimap_roi.y) + float(centroid_y_minimap)))
    except Exception:
        return

    rgb = bytearray(frame.rgb)
    _draw_cross(rgb, width=int(frame.width), height=int(frame.height), x=int(abs_x), y=int(abs_y), size=5)

    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"marker_overlay_{ts}_{_safe_reason(str(reason))}.ppm"
    try:
        p = frames_dir / name
        p.write_bytes(_ppm_p6_header(int(frame.width), int(frame.height)) + bytes(rgb))
    except Exception:
        return


def dump_click_point_overlay(*, frames_dir: Path, frame: Frame, x: int, y: int, reason: str) -> None:
    """Dump a full-frame PPM with a cross at the attempted click point (frame coords)."""

    if not frame.rgb or int(frame.width) <= 0 or int(frame.height) <= 0:
        return

    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    rgb = bytearray(frame.rgb)
    _draw_cross(rgb, width=int(frame.width), height=int(frame.height), x=int(x), y=int(y), size=6)

    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"click_overlay_{ts}_{_safe_reason(str(reason))}.ppm"
    try:
        p = frames_dir / name
        p.write_bytes(_ppm_p6_header(int(frame.width), int(frame.height)) + bytes(rgb))
    except Exception:
        return
