from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi


@dataclass(frozen=True, slots=True)
class PercentRead:
    value: float
    source: str


def _crop_rgb(frame: Frame, roi: Roi) -> bytes:
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


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return float(v)


def read_bar_percent(frame: Frame, roi: Roi, *, channel: str) -> Optional[PercentRead]:
    """Read fill percent from a mock-style bar.

    This is semantic only if the bar uses a dedicated channel:
    - channel='r' for HP
    - channel='b' for MP

    The reader counts pixels with high channel intensity and low others.
    """

    rgb = _crop_rgb(frame, roi)
    if not rgb:
        return None

    total_px = int(roi.width) * int(roi.height)
    if total_px <= 0:
        return None

    if channel not in {'r', 'g', 'b'}:
        return None

    filled = 0
    for i in range(0, len(rgb) - 2, 3):
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        if channel == 'r':
            if r > 200 and g < 80 and b < 80:
                filled += 1
        elif channel == 'g':
            if g > 200 and r < 80 and b < 80:
                filled += 1
        else:
            if b > 200 and r < 80 and g < 80:
                filled += 1

    return PercentRead(value=_clamp01(filled / float(total_px)), source='bar')


def read_text_percent(frame: Frame, roi: Roi) -> Optional[PercentRead]:
    """Read percent from a mock-encoded numeric ROI.

    Encoding: first 4 bytes of ROI RGB buffer are uint16 current, uint16 max (little endian).
    This requires roi.height == 1.
    """

    if int(roi.height) != 1:
        return None

    rgb = _crop_rgb(frame, roi)
    if len(rgb) < 4:
        return None

    cur = int.from_bytes(rgb[0:2], 'little', signed=False)
    mx = int.from_bytes(rgb[2:4], 'little', signed=False)
    if mx <= 0:
        return None
    if cur < 0 or cur > mx * 10:
        return None

    return PercentRead(value=_clamp01(cur / float(mx)), source='text')


def read_hp_mp_text_pair(frame: Frame, roi: Roi) -> Optional[tuple[PercentRead, PercentRead]]:
    """Read (hp, mp) percents from a combined mock-encoded ROI.

    Encoding: 8 bytes at ROI start: hp_cur u16, hp_max u16, mp_cur u16, mp_max u16.
    Requires roi.height == 1.
    """

    if int(roi.height) != 1:
        return None

    rgb = _crop_rgb(frame, roi)
    if len(rgb) < 8:
        return None

    hp_cur = int.from_bytes(rgb[0:2], 'little', signed=False)
    hp_max = int.from_bytes(rgb[2:4], 'little', signed=False)
    mp_cur = int.from_bytes(rgb[4:6], 'little', signed=False)
    mp_max = int.from_bytes(rgb[6:8], 'little', signed=False)

    if hp_max <= 0 or mp_max <= 0:
        return None

    hp = PercentRead(value=_clamp01(hp_cur / float(hp_max)), source='hp_mp')
    mp = PercentRead(value=_clamp01(mp_cur / float(mp_max)), source='hp_mp')
    return hp, mp


def read_percent_with_consistency(
    *,
    bar: Optional[PercentRead],
    text: Optional[PercentRead],
    tol: float,
) -> Optional[PercentRead]:
    if bar is None and text is None:
        return None
    if bar is None:
        return text
    if text is None:
        return bar

    if abs(float(bar.value) - float(text.value)) > float(tol):
        return None

    # Both consistent: prefer combined.
    return PercentRead(value=(float(bar.value) + float(text.value)) / 2.0, source='bar+text')


def detect_cooldown_marker(frame: Frame, roi: Roi, *, marker_rgb: tuple[int, int, int], tol: int) -> Optional[bool]:
    """Cooldown is observable only via a configured marker color in ROI.

    Returns:
    - True if marker present
    - False if marker absent
    - None if ROI cannot be read
    """

    rgb = _crop_rgb(frame, roi)
    if not rgb:
        return None

    mr, mg, mb = (int(marker_rgb[0]), int(marker_rgb[1]), int(marker_rgb[2]))
    t = int(max(0, min(255, tol)))

    for i in range(0, len(rgb) - 2, 3):
        r = int(rgb[i])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        if abs(r - mr) <= t and abs(g - mg) <= t and abs(b - mb) <= t:
            return True
    return False


def parse_rgb_triplet(raw: str, *, default: tuple[int, int, int]) -> tuple[int, int, int]:
    s = (raw or '').strip()
    if not s:
        return default
    parts = [p.strip() for p in s.split(',')]
    if len(parts) != 3:
        return default
    try:
        r = int(parts[0])
        g = int(parts[1])
        b = int(parts[2])
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    except Exception:
        return default
