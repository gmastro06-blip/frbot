from __future__ import annotations

from dataclasses import dataclass

from contracts.capture import Frame
from contracts.evidence import Roi
from contracts.runtime import InventorySnapshot, NpcIdentity


@dataclass(frozen=True, slots=True)
class TradeDelta:
    gold_delta: int
    item_delta: int
    capacity_used_delta: int


def _crop_roi_bytes(frame: Frame, roi: Roi) -> bytes | None:
    if not frame.rgb:
        return None
    w = int(frame.width)
    h = int(frame.height)
    if w <= 0 or h <= 0:
        return None
    if int(roi.x) < 0 or int(roi.y) < 0:
        return None
    if (int(roi.x) + int(roi.width)) > w or (int(roi.y) + int(roi.height)) > h:
        return None

    row_stride = w * 3
    out = bytearray(int(roi.width) * int(roi.height) * 3)
    out_row_stride = int(roi.width) * 3
    for row in range(int(roi.height)):
        src_start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = frame.rgb[src_start:src_end]
    return bytes(out)


def detect_npc_window(frame: Frame, roi: Roi) -> NpcIdentity | None:
    """Detect an NPC trade window with semantic identity.

    Encoding (mock-friendly, deterministic):
      u16 magic 0xFACE
      u16 npc_id
      u16 open_flag (0/1)
    """

    raw = _crop_roi_bytes(frame, roi)
    if raw is None or len(raw) < 6:
        return None

    magic = int.from_bytes(raw[0:2], 'little', signed=False)
    if magic != 0xFACE:
        return None

    npc_id = int.from_bytes(raw[2:4], 'little', signed=False)
    open_flag = int.from_bytes(raw[4:6], 'little', signed=False)
    if int(npc_id) <= 0:
        return None

    return NpcIdentity(npc_id=int(npc_id), open=bool(open_flag))


def read_trade_inventory(frame: Frame, roi: Roi) -> InventorySnapshot | None:
    """Read trade inventory snapshot.

    Encoding (mock-friendly, deterministic):
      u16 magic 0xB00B
      u16 gold
      u16 item_count
      u16 capacity_used

    ROI must provide at least 8 bytes.
    """

    raw = _crop_roi_bytes(frame, roi)
    if raw is None or len(raw) < 8:
        return None

    magic = int.from_bytes(raw[0:2], 'little', signed=False)
    if magic != 0xB00B:
        return None

    gold = int.from_bytes(raw[2:4], 'little', signed=False)
    item_count = int.from_bytes(raw[4:6], 'little', signed=False)
    cap_used = int.from_bytes(raw[6:8], 'little', signed=False)

    return InventorySnapshot(slot_counts={'gold': int(gold), 'item': int(item_count)}, capacity_used=int(cap_used))


def compute_trade_delta(before: InventorySnapshot, after: InventorySnapshot) -> TradeDelta:
    b_gold = int(before.slot_counts.get('gold', 0))
    a_gold = int(after.slot_counts.get('gold', 0))
    b_item = int(before.slot_counts.get('item', 0))
    a_item = int(after.slot_counts.get('item', 0))

    b_cap = int(before.capacity_used or 0)
    a_cap = int(after.capacity_used or 0)

    return TradeDelta(
        gold_delta=int(a_gold - b_gold),
        item_delta=int(a_item - b_item),
        capacity_used_delta=int(a_cap - b_cap),
    )


def is_trade_success(delta: TradeDelta, intent_type: str) -> bool:
    it = (intent_type or '').strip().lower()

    # Buy: gold decreases and item count increases.
    if it == 'buy':
        return int(delta.gold_delta) < 0 and int(delta.item_delta) > 0

    # Sell: gold increases and item count decreases.
    if it == 'sell':
        return int(delta.gold_delta) > 0 and int(delta.item_delta) < 0

    # Deposit via NPC: gold decreases OR capacity_used decreases.
    if it == 'deposit':
        return int(delta.gold_delta) < 0 or int(delta.capacity_used_delta) < 0

    return False
