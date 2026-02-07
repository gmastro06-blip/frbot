from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi


def crop_rgb(*, frame_rgb: bytes, frame_w: int, frame_h: int, roi: Roi) -> bytes:
    x = int(getattr(roi, 'x', 0))
    y = int(getattr(roi, 'y', 0))
    w = int(getattr(roi, 'width', 0))
    h = int(getattr(roi, 'height', 0))

    if frame_w <= 0 or frame_h <= 0:
        return b''
    if w <= 0 or h <= 0:
        return b''
    if x < 0 or y < 0:
        return b''
    if (x + w) > frame_w or (y + h) > frame_h:
        return b''

    row_stride = frame_w * 3
    out = bytearray(w * h * 3)
    out_row_stride = w * 3
    for row in range(h):
        src_start = ((y + row) * row_stride) + (x * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = frame_rgb[src_start:src_end]
    return bytes(out)


def _all_zero(buf: bytes) -> bool:
    if not buf:
        return True
    # sample to keep deterministic/fast
    step = max(1, (len(buf) // 6000))
    for i in range(0, len(buf), step):
        if buf[i] != 0:
            return False
    return True


def target_frame_stable(*, frame_a: Frame, frame_b: Frame, roi: Roi) -> bool:
    a = crop_rgb(frame_rgb=bytes(frame_a.rgb), frame_w=int(frame_a.width), frame_h=int(frame_a.height), roi=roi)
    b = crop_rgb(frame_rgb=bytes(frame_b.rgb), frame_w=int(frame_b.width), frame_h=int(frame_b.height), roi=roi)
    if not a or not b:
        return False
    if _all_zero(a) or _all_zero(b):
        return False
    return a == b


def _rgb_matches(r: int, g: int, b: int, *, target: tuple[int, int, int], tol: int) -> bool:
    tr, tg, tb = target
    return abs(int(r) - int(tr)) <= tol and abs(int(g) - int(tg)) <= tol and abs(int(b) - int(tb)) <= tol


def cooldown_active(*, frame: Frame, roi: Roi, marker_rgb: tuple[int, int, int] = (255, 255, 0), tol: int = 10) -> bool:
    crop = crop_rgb(frame_rgb=bytes(frame.rgb), frame_w=int(frame.width), frame_h=int(frame.height), roi=roi)
    if not crop:
        return False

    tol_i = int(tol)
    for i in range(0, len(crop), 3):
        if _rgb_matches(crop[i], crop[i + 1], crop[i + 2], target=marker_rgb, tol=tol_i):
            return True
    return False


def feedback_visible(*, frame: Frame, roi: Roi, marker_rgb: tuple[int, int, int] = (0, 255, 255), tol: int = 10) -> bool:
    """Semantic feedback evidence: a marker color is present within the ROI.

    This intentionally avoids hashes/deltas; it is a direct semantic marker read.
    """

    crop = crop_rgb(frame_rgb=bytes(frame.rgb), frame_w=int(frame.width), frame_h=int(frame.height), roi=roi)
    if not crop:
        return False

    tol_i = int(tol)
    for i in range(0, len(crop), 3):
        if _rgb_matches(crop[i], crop[i + 1], crop[i + 2], target=marker_rgb, tol=tol_i):
            return True
    return False


def read_target_hp_percent(*, frame: Frame, roi: Roi) -> Optional[float]:
    crop = crop_rgb(frame_rgb=bytes(frame.rgb), frame_w=int(frame.width), frame_h=int(frame.height), roi=roi)
    if not crop:
        return None

    # Robust for real clients/themes: estimate bar fill using luminance separation.
    # We compute a luminance histogram, derive a conservative threshold from the
    # 20th/80th percentiles, then measure fraction of columns above that threshold.
    total_px = len(crop) // 3
    if total_px <= 0:
        return None

    # Infer ROI width from roi metadata (crop is already validated by crop_rgb).
    w = int(getattr(roi, 'width', 0))
    h = int(getattr(roi, 'height', 0))
    if w <= 0 or h <= 0 or (w * h) != total_px:
        return None

    hist = [0] * 256
    col_sum = [0] * w
    col_cnt = [0] * w

    # ITU-R BT.709-ish integer luma (matches audit_emergency semantics).
    # y ~= 0.2126 R + 0.7152 G + 0.0722 B
    p = 0
    for yy in range(h):
        for xx in range(w):
            i = p * 3
            r = int(crop[i])
            g = int(crop[i + 1])
            b = int(crop[i + 2])
            y = int((r * 2126 + g * 7152 + b * 722) // 10000)
            if y < 0:
                y = 0
            elif y > 255:
                y = 255
            hist[y] += 1
            col_sum[xx] += y
            col_cnt[xx] += 1
            p += 1

    def _percentile(pct: float) -> int:
        if pct <= 0.0:
            return 0
        if pct >= 1.0:
            return 255
        target = int(total_px * pct)
        acc = 0
        for v in range(256):
            acc += int(hist[v])
            if acc >= target:
                return int(v)
        return 255

    p20 = _percentile(0.20)
    p80 = _percentile(0.80)

    # If the ROI doesn't have meaningful contrast, treat it as unreadable.
    if int(p80) - int(p20) < 8:
        # Fallback for mock-style solid bars (e.g., 100% HP => all red, no contrast).
        # We only accept the ROI as readable if it contains a meaningful amount of
        # red-ish pixels; otherwise keep it unreadable (avoids passing on pure black).
        red = 0
        for i in range(0, len(crop), 3):
            r = int(crop[i])
            g = int(crop[i + 1])
            b = int(crop[i + 2])
            if r > 200 and g < 80 and b < 80:
                red += 1
        ratio = float(red) / float(total_px)
        if ratio < 0.02:
            return None
        return ratio

    thr = int((int(p20) + int(p80)) // 2)

    filled_cols = 0
    for xx in range(w):
        c = int(col_cnt[xx])
        if c <= 0:
            continue
        mean = float(col_sum[xx]) / float(c)
        if mean > float(thr):
            filled_cols += 1

    if w <= 0:
        return None
    ratio = float(filled_cols) / float(w)
    return ratio


def _target_frame_visible(rgb: bytes) -> bool:
    # Visible if any non-black pixel exists (sampled across the buffer).
    if not rgb:
        return False
    step = max(1, (len(rgb) // 6000))
    for i in range(0, len(rgb), step):
        if rgb[i] != 0:
            return True
    return False


def _target_frame_hp_bar_present(rgb: bytes) -> bool:
    # HP bar present if the ROI contains meaningful luminance contrast.
    # Some clients/themes render the bar in low-saturation (gray) colors.
    if not rgb:
        return False

    step = max(1, (len(rgb) // 9000))
    lo = 255
    hi = 0
    # Sampled luma range.
    for i in range(0, len(rgb) - 2, 3 * step):
        r = int(rgb[i])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        y = int((r * 2126 + g * 7152 + b * 722) // 10000)
        if y < lo:
            lo = y
        if y > hi:
            hi = y
        if (hi - lo) >= 28:
            return True
    return (hi - lo) >= 28


def target_frame_active(*, frame: Frame, roi: Roi) -> bool:
    """Return True if target frame appears active (name/HP region lit).

    This is intentionally pixel-semantic and does not rely on OCR or hashes.
    """

    crop = crop_rgb(frame_rgb=bytes(frame.rgb), frame_w=int(frame.width), frame_h=int(frame.height), roi=roi)
    if not crop:
        return False
    return bool(_target_frame_visible(crop) and _target_frame_hp_bar_present(crop))


@dataclass(frozen=True, slots=True)
class CombatBasicEvidence:
    hp_before: Optional[float]
    hp_after: Optional[float]
    evidence_ok: bool
    evidence_kind: str
    feedback_before: bool = False
    feedback_after: bool = False
    locked_before: bool = False
    locked_after: bool = False
