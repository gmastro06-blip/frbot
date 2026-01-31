from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.runtime import BattleListEntry, Rect
from contracts.evidence import Roi


@dataclass(frozen=True, slots=True)
class BattleListObservation:
    container_bbox: Rect
    entries: tuple[BattleListEntry, ...]


def crop_roi_rgb(frame: Frame, roi: Roi) -> bytes:
    if not frame.rgb:
        return b''
    if frame.width <= 0 or frame.height <= 0:
        return b''
    if roi.width <= 0 or roi.height <= 0:
        return b''
    if roi.x < 0 or roi.y < 0:
        return b''
    if (roi.x + roi.width) > frame.width or (roi.y + roi.height) > frame.height:
        return b''

    row_stride = frame.width * 3
    out = bytearray(roi.width * roi.height * 3)
    out_row_stride = roi.width * 3
    src = frame.rgb
    for row in range(roi.height):
        src_start = ((roi.y + row) * row_stride) + (roi.x * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = src[src_start:src_end]
    return bytes(out)


@dataclass(frozen=True, slots=True)
class MockBattleListLayout:
    row_height: int = 16
    header_height: int = 0
    max_rows: int = 8


def _rgb_at(rgb: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    if x < 0 or y < 0 or x >= width:
        return (0, 0, 0)
    idx = (y * width + x) * 3
    if idx < 0 or idx + 2 >= len(rgb):
        return (0, 0, 0)
    return (rgb[idx], rgb[idx + 1], rgb[idx + 2])


def _decode_name_from_row(rgb: bytes, width: int, row_y: int) -> str:
    # Mock "OCR": first 12 pixels of the row encode up to 12 ASCII bytes in the red channel.
    # A value 0 terminates.
    out: list[int] = []
    for i in range(12):
        r, g, b = _rgb_at(rgb, width, x=2 + i, y=row_y + 2)
        if g != 0 or b != 0:
            # Not a valid encoded pixel.
            return ''
        if r == 0:
            break
        out.append(int(r))
    try:
        return bytes(out).decode('ascii', errors='ignore').strip()
    except Exception:
        return ''


def _row_highlighted(rgb: bytes, width: int, row_y: int, row_h: int) -> bool:
    # Sample mid pixel; highlighted rows have a distinctive blue background in mock.
    r, g, b = _rgb_at(rgb, width, x=width // 2, y=row_y + (row_h // 2))
    return b > 200 and r < 80 and g < 80


def _row_attackable(rgb: bytes, width: int, row_y: int) -> bool:
    # Attackable marker: bright green pixel at fixed offset.
    r, g, b = _rgb_at(rgb, width, x=1, y=row_y + 1)
    return g > 200 and r < 50 and b < 50


def _row_hp_bar_visible(rgb: bytes, width: int, row_y: int) -> bool:
    # HP bar: any bright red pixel within a small bar region.
    bar_x0 = max(0, width - 20)
    bar_x1 = max(0, width - 2)
    y = row_y + 1
    for x in range(bar_x0, bar_x1):
        r, g, b = _rgb_at(rgb, width, x=x, y=y)
        if r > 180 and g < 60 and b < 60:
            return True
    return False


def detect_battle_list(frame: Frame, roi: Roi, *, layout: Optional[MockBattleListLayout] = None) -> Optional[BattleListObservation]:
    """Semantic Battle List detection.

    This implementation is intentionally strict and deterministic:
    - Interprets the ROI as the Battle List container.
    - Extracts rows by fixed height (mock layout).
    - Requires at least one row with non-empty OCR name.

    If the runtime cannot extract entries with OCR, it must treat the Battle List as invalid.
    """

    layout = layout or MockBattleListLayout()

    rgb = crop_roi_rgb(frame, roi)
    if not rgb:
        return None

    w = int(roi.width)
    h = int(roi.height)
    if w <= 0 or h <= 0:
        return None

    header_h = int(layout.header_height)
    row_h = int(layout.row_height)
    if row_h <= 0:
        return None

    max_rows = int(layout.max_rows)
    if max_rows <= 0:
        return None

    entries: list[BattleListEntry] = []
    ocr_ok_any = False

    # Determine how many full rows fit.
    usable_h = max(0, h - header_h)
    rows_fit = min(max_rows, usable_h // row_h)
    if rows_fit <= 0:
        return None

    for row_index in range(rows_fit):
        row_y = header_h + row_index * row_h
        name = _decode_name_from_row(rgb, w, row_y)
        if name:
            ocr_ok_any = True

        highlighted = _row_highlighted(rgb, w, row_y, row_h)
        is_attackable = _row_attackable(rgb, w, row_y)
        hp_bar_visible = _row_hp_bar_visible(rgb, w, row_y)

        entry_bbox = Rect(x=int(roi.x), y=int(roi.y + row_y), width=int(roi.width), height=row_h)
        entries.append(
            BattleListEntry(
                name=name,
                hp_bar_visible=bool(hp_bar_visible),
                is_attackable=bool(is_attackable),
                screen_bbox=entry_bbox,
                row_index=int(row_index),
                highlighted=bool(highlighted),
            )
        )

    # Invalid if no rows or OCR fails for all rows.
    if not entries:
        return None
    if not ocr_ok_any:
        return None

    return BattleListObservation(
        container_bbox=Rect(x=int(roi.x), y=int(roi.y), width=int(roi.width), height=int(roi.height)),
        entries=tuple(entries),
    )
