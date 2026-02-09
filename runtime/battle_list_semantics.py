from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os

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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _mode() -> str:
    return str(os.environ.get('FRBOT_MODE') or '').strip().lower()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _row_dark_fraction(
    rgb: bytes,
    *,
    width: int,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    dark_luma: float,
) -> float:
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = max(int(x0) + 1, int(x1))
    y1 = max(int(y0) + 1, int(y1))

    dark = 0
    total = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            idx = (y * width + x) * 3
            if idx + 2 >= len(rgb):
                continue
            r = rgb[idx]
            g = rgb[idx + 1]
            b = rgb[idx + 2]
            lum = (float(r) + float(g) + float(b)) / 3.0
            if lum < float(dark_luma):
                dark += 1
            total += 1
    return (float(dark) / float(total)) if total > 0 else 0.0


def _infer_header_height(rgb: bytes, *, width: int, header_h: int, row_h: int, rows_fit: int) -> int:
    """Best-effort header detection for real Battle List ROIs.

    Some real ROIs include a header/tab row; our mock layout uses header_height=0.
    When OCR is unavailable, clicking the header row produces unstable evidence.
    This heuristic attempts to detect a header row as a strong outlier.
    """

    if header_h != 0:
        return int(header_h)
    if rows_fit <= 2 or row_h <= 0:
        return int(header_h)

    # Analyze a central stripe (avoid scroll bars / borders).
    x0 = max(0, int(width * 0.10))
    x1 = max(x0 + 1, int(width * 0.70))
    dark_luma = _env_float('FRBOT_BATTLE_LIST_TEXT_DARK_LUMA', 50.0)
    min_dark_frac = _env_float('FRBOT_BATTLE_LIST_TEXT_DARK_FRAC', 0.002)

    # First try a simple, robust signal: headers often have less "text-like" dark pixels
    # than the first actual entry row.
    # This helps avoid the brittle SD outlier test when many rows are empty.
    try:
        row0_dark = _row_dark_fraction(
            rgb,
            width=width,
            x0=x0,
            x1=x1,
            y0=int(header_h + 0 * row_h),
            y1=int(header_h + 1 * row_h),
            dark_luma=dark_luma,
        )
        row1_dark = _row_dark_fraction(
            rgb,
            width=width,
            x0=x0,
            x1=x1,
            y0=int(header_h + 1 * row_h),
            y1=int(header_h + 2 * row_h),
            dark_luma=dark_luma,
        )
        ratio_max = _env_float('FRBOT_BATTLE_LIST_HEADER_DARK_RATIO_MAX', 0.60)
        # If row1 clearly looks like an entry row and row0 is substantially "less texty",
        # treat row0 as a header.
        if (row1_dark >= float(min_dark_frac)) and (row0_dark <= (float(row1_dark) * float(ratio_max))):
            return int(row_h)
    except Exception:
        # Fall back to the SD heuristic below.
        pass

    means: list[float] = []
    sds: list[float] = []
    dark_fracs: list[float] = []
    for row_index in range(int(rows_fit)):
        y0 = int(header_h + row_index * row_h)
        y1 = int(y0 + row_h)
        means.append(_row_mean_luma(rgb, width=width, x0=x0, x1=x1, y0=y0, y1=y1))

        # A cheap variability proxy: mean absolute deviation from the mean.
        m = means[-1]
        total_dev = 0.0
        n = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                idx = (y * width + x) * 3
                if idx + 2 >= len(rgb):
                    continue
                r = rgb[idx]
                g = rgb[idx + 1]
                b = rgb[idx + 2]
                lum = (float(r) + float(g) + float(b)) / 3.0
                total_dev += abs(lum - m)
                n += 1
        sds.append((total_dev / float(n)) if n > 0 else 0.0)
        dark_fracs.append(_row_dark_fraction(rgb, width=width, x0=x0, x1=x1, y0=y0, y1=y1, dark_luma=dark_luma))

    # Compare first row vs median of remaining rows.
    rest_means = means[1:]
    rest_sds = sds[1:]
    rest_dark = dark_fracs[1:]
    if not rest_means or not rest_sds:
        return int(header_h)

    rest_means_sorted = sorted(rest_means)
    rest_sds_sorted = sorted(rest_sds)
    rest_dark_sorted = sorted(rest_dark)
    med_mean = rest_means_sorted[len(rest_means_sorted) // 2]
    med_sd = rest_sds_sorted[len(rest_sds_sorted) // 2]
    med_dark = rest_dark_sorted[len(rest_dark_sorted) // 2]

    mean0 = means[0]
    sd0 = sds[0]
    dark0 = dark_fracs[0]

    # Header row tends to have a different background/border and more contrast.
    if (sd0 > max(10.0, 3.0 * float(med_sd))) and (
        abs(mean0 - float(med_mean)) > 4.0 or (dark0 > (float(med_dark) + 0.01))
    ):
        return int(row_h)

    return int(header_h)


def _row_mean_luma(rgb: bytes, *, width: int, x0: int, x1: int, y0: int, y1: int) -> float:
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = max(int(x0) + 1, int(x1))
    y1 = max(int(y0) + 1, int(y1))

    total = 0.0
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            idx = (y * width + x) * 3
            if idx + 2 >= len(rgb):
                continue
            r = rgb[idx]
            g = rgb[idx + 1]
            b = rgb[idx + 2]
            total += (float(r) + float(g) + float(b)) / 3.0
            n += 1
    return (total / float(n)) if n > 0 else 0.0


def _infer_highlighted_row(rgb: bytes, *, width: int, header_h: int, row_h: int, rows_fit: int) -> int | None:
    if rows_fit <= 0 or row_h <= 0:
        return None

    # Sample a central stripe, avoiding the far-right HP bar area.
    x0 = max(0, int(width * 0.10))
    x1 = max(x0 + 1, int(width * 0.70))

    means: list[float] = []
    for row_index in range(int(rows_fit)):
        y0 = int(header_h + row_index * row_h)
        y1 = int(y0 + row_h)
        means.append(_row_mean_luma(rgb, width=width, x0=x0, x1=x1, y0=y0, y1=y1))

    if not means:
        return None

    sorted_means = sorted(means)
    med = sorted_means[len(sorted_means) // 2]
    # Highlight may be brighter OR darker depending on client/theme.
    dev = [abs(float(m) - float(med)) for m in means]
    mx = max(dev)
    idx = int(dev.index(mx))

    # Require a clear separation vs median to avoid false positives.
    if mx >= float(os.environ.get('FRBOT_BATTLE_LIST_HIGHLIGHT_LUMA_DELTA', '8') or '8'):
        return idx
    return None


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

    # Guardrail: the mock OCR encoding is only valid for synthetic frames.
    # In REAL frames, attempting to decode names from arbitrary pixels can yield
    # rare false positives that break downstream invariants (e.g., highlight detection).
    mock_rows_env_present = os.environ.get('FRBOT_MOCK_BATTLE_LIST_ROWS') is not None
    allow_mock_ocr = (_mode() == 'mock') or bool(mock_rows_env_present)

    # Determine how many full rows fit.
    usable_h = max(0, h - header_h)
    rows_fit = min(max_rows, usable_h // row_h)
    if rows_fit <= 0:
        return None

    for row_index in range(rows_fit):
        row_y = header_h + row_index * row_h
        name = _decode_name_from_row(rgb, w, row_y) if allow_mock_ocr else ''
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

    # Invalid if no rows.
    if not entries:
        return None

    # Strict mode requires OCR to succeed for at least one row.
    if not ocr_ok_any:
        # Never enable no-OCR fallback during mock runs/tests.
        # Tests control mock layout via FRBOT_MOCK_BATTLE_LIST_ROWS.
        allow_no_ocr = (
            _env_bool('FRBOT_BATTLE_LIST_ALLOW_NO_OCR', False)
            and (_mode() != 'mock')
            and (not mock_rows_env_present)
        )
        if not allow_no_ocr:
            return None

        # Fallback: treat the ROI as a real battle list container without name OCR.
        # IMPORTANT: avoid clicking headers / empty rows.
        header_h2 = _infer_header_height(rgb, width=w, header_h=header_h, row_h=row_h, rows_fit=int(rows_fit))
        usable_h2 = max(0, h - int(header_h2))
        rows_fit2 = min(max_rows, usable_h2 // row_h)
        if rows_fit2 <= 0:
            return None

        highlighted_idx = _infer_highlighted_row(rgb, width=w, header_h=int(header_h2), row_h=row_h, rows_fit=int(rows_fit2))

        # Heuristic “row has content”: presence of dark text-like pixels in the name area.
        dark_luma = _env_float('FRBOT_BATTLE_LIST_TEXT_DARK_LUMA', 50.0)
        min_dark_frac = _env_float('FRBOT_BATTLE_LIST_TEXT_DARK_FRAC', 0.002)
        x0 = max(0, int(w * 0.10))
        x1 = max(x0 + 1, int(w * 0.70))

        entries2: list[BattleListEntry] = []
        for row_index in range(int(rows_fit2)):
            row_y = int(header_h2 + row_index * row_h)
            y0 = int(row_y)
            y1 = int(row_y + row_h)
            dark_frac = _row_dark_fraction(rgb, width=w, x0=x0, x1=x1, y0=y0, y1=y1, dark_luma=dark_luma)
            has_text = bool(dark_frac >= float(min_dark_frac))

            entry_bbox = Rect(x=int(roi.x), y=int(roi.y + row_y), width=int(roi.width), height=row_h)
            entries2.append(
                BattleListEntry(
                    # Deterministic dummy name (stable across frames).
                    # Selectability is controlled via is_attackable/hp_bar_visible.
                    name=f'row_{int(row_index)}',
                    # Without OCR/icons, we conservatively allow selecting only “populated” rows.
                    hp_bar_visible=bool(has_text),
                    is_attackable=bool(has_text),
                    screen_bbox=entry_bbox,
                    row_index=int(row_index),
                    highlighted=(highlighted_idx is not None and int(row_index) == int(highlighted_idx)),
                )
            )

        return BattleListObservation(
            container_bbox=Rect(x=int(roi.x), y=int(roi.y), width=int(roi.width), height=int(roi.height)),
            entries=tuple(entries2),
        )

    return BattleListObservation(
        container_bbox=Rect(x=int(roi.x), y=int(roi.y), width=int(roi.width), height=int(roi.height)),
        entries=tuple(entries),
    )
