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


def compute_depot_delta(before: DepotSnapshot, after: DepotSnapshot) -> DepotDelta:
    return DepotDelta(
        item_count_delta=int(after.item_count) - int(before.item_count),
        open_changed=bool(before.open != after.open),
    )
