from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi
from contracts.runtime import InventorySnapshot


# Deposit/Inventory gate terminology:
# InventoryState is an alias of the existing InventorySnapshot contract.
InventoryState = InventorySnapshot


@dataclass(frozen=True, slots=True)
class InventoryDelta:
    slot_deltas: dict[str, int]
    capacity_used_delta: int


def compute_inventory_delta(before: InventoryState, after: InventoryState) -> InventoryDelta:
    return diff_inventory(before, after)


def is_deposit_success(delta: InventoryDelta) -> bool:
    # Deposit success evidence: item count ↓ OR capacity_used ↓
    for v in delta.slot_deltas.values():
        if int(v) < 0:
            return True
    return int(delta.capacity_used_delta) < 0


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


def read_inventory(frame: Frame, roi: Roi) -> Optional[InventorySnapshot]:
    """Pure semantic inventory snapshot.

    Contract:
    - Reads a small ROI that encodes inventory counters.
    - Does not use frame digests/hashes.

    Encoding (little-endian uint16s):
    - u16[0] = magic 0xBEEF
    - u16[1] = gold_count
    - u16[2] = capacity_used
    """

    blob = _roi_bytes(frame, roi)
    if blob is None or len(blob) < 6:
        return None

    magic = int.from_bytes(blob[0:2], 'little', signed=False)
    if magic != 0xBEEF:
        return None

    gold = int.from_bytes(blob[2:4], 'little', signed=False)
    cap_used = int.from_bytes(blob[4:6], 'little', signed=False)

    return InventorySnapshot(slot_counts={'gold': int(gold)}, capacity_used=int(cap_used))


def diff_inventory(before: InventorySnapshot, after: InventorySnapshot) -> InventoryDelta:
    keys = set(before.slot_counts.keys()) | set(after.slot_counts.keys())
    deltas: dict[str, int] = {}
    for k in keys:
        b = int(before.slot_counts.get(k, 0))
        a = int(after.slot_counts.get(k, 0))
        if a != b:
            deltas[str(k)] = int(a - b)

    bcap = before.capacity_used
    acap = after.capacity_used
    cap_delta = 0
    if bcap is not None and acap is not None:
        cap_delta = int(acap) - int(bcap)

    return InventoryDelta(slot_deltas=deltas, capacity_used_delta=int(cap_delta))


def is_loot_success(delta: InventoryDelta) -> bool:
    # item count ↑ OR capacity_used ↑
    for v in delta.slot_deltas.values():
        if int(v) > 0:
            return True
    return int(delta.capacity_used_delta) > 0
