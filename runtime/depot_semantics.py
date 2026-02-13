from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi
from contracts.runtime import DepotSnapshot


@dataclass(frozen=True, slots=True)
class DepotDelta:
    item_count_delta: int
    open_changed: bool


def _roi_bytes(frame: Frame, roi: Roi) -> Optional[bytes]:
    if not frame.rgb:
        return None
    if frame.width <= 0 or frame.height <= 0:
        return None
    if roi.width <= 0 or roi.height <= 0:
        return None
    if roi.x < 0 or roi.y < 0:
        return None
    if (roi.x + roi.width) > int(frame.width) or (roi.y + roi.height) > int(frame.height):
        return None

    row_stride = int(frame.width) * 3
    out_row_stride = int(roi.width) * 3
    out = bytearray(int(roi.height) * out_row_stride)

    for row in range(int(roi.height)):
        src_start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = frame.rgb[src_start:src_end]

    return bytes(out)


def read_depot_container(frame: Frame, roi: Roi) -> Optional[DepotSnapshot]:
    """Pure semantic depot snapshot.

    Encoding (little-endian uint16s):
    - u16[0] = magic 0xD00D
    - u16[1] = item_count
    - u16[2] = open_flag (0 or 1)
    """

    blob = _roi_bytes(frame, roi)
    if blob is None or len(blob) < 6:
        return None

    magic = int.from_bytes(blob[0:2], 'little', signed=False)
    if magic != 0xD00D:
        return None

    item_count = int.from_bytes(blob[2:4], 'little', signed=False)
    open_flag = int.from_bytes(blob[4:6], 'little', signed=False)

    return DepotSnapshot(item_count=int(item_count), open=bool(open_flag))


def find_d00d_marker_roi_within(frame: Frame, search_roi: Roi) -> Optional[Roi]:
    """Locate a 0xD00D 2x1 marker within a broader ROI.

    Returns a minimal 2x1 ROI (6 bytes) anchored at the marker pixel.
    Intended for REAL robustness when the configured ROI is a search window.
    """

    rgb = bytes(getattr(frame, 'rgb', b'') or b'')
    w = int(getattr(frame, 'width', 0) or 0)
    h = int(getattr(frame, 'height', 0) or 0)
    if not rgb or w <= 1 or h <= 0:
        return None

    x0 = max(0, int(search_roi.x))
    y0 = max(0, int(search_roi.y))
    x1 = min(int(w) - 2, int(search_roi.x) + int(search_roi.width) - 2)
    y1 = min(int(h) - 1, int(search_roi.y) + int(search_roi.height) - 1)
    if x1 < x0 or y1 < y0:
        return None

    row_stride = w * 3
    for yy in range(int(y0), int(y1) + 1):
        base = yy * row_stride
        for xx in range(int(x0), int(x1) + 1):
            i = base + (xx * 3)
            if i + 5 >= len(rgb):
                break
            blob6 = rgb[i : i + 6]
            magic = int.from_bytes(blob6[0:2], 'little', signed=False)
            if magic != 0xD00D:
                continue
            return Roi(name=str(search_roi.name), x=int(xx), y=int(yy), width=2, height=1)

    return None


def compute_depot_delta(before: DepotSnapshot, after: DepotSnapshot) -> DepotDelta:
    return DepotDelta(
        item_count_delta=int(after.item_count) - int(before.item_count),
        open_changed=bool(before.open != after.open),
    )
