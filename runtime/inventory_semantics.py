from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, cast

import json
import os
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class BeefCandidate:
    x: int
    y: int
    gold: int
    cap_used: int
    raw6_hex: str


def _pairings4(items: list[int]) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    if len(items) != 4:
        return []
    a, b, c, d = items
    return [
        ((a, b), (c, d)),
        ((a, c), (b, d)),
        ((a, d), (b, c)),
    ]


def _decode_beef_from_blob(
    blob6: bytes,
    *,
    cap_max: int | None,
    gold_max: int | None,
) -> tuple[int, int] | None:
    """Decode (gold, cap_used) from a 2x1 ROI payload.

    Contract: ROI is exactly 2x1 pixels, producing 6 bytes in RGB order:
    [R0,G0,B0,R1,G1,B1]. The 0xBEEF magic + payload may be stored in different
    channel orders depending on the capture pipeline.
    """

    if not blob6 or len(blob6) < 6:
        return None

    b = blob6[:6]
    all_idx = [0, 1, 2, 3, 4, 5]

    # Directed candidate pairs within each pixel (RG, GB, BR) for pixel0 and pixel1.
    magic_pairs: list[tuple[int, int]] = [
        (0, 1),
        (1, 0),
        (1, 2),
        (2, 1),
        (2, 0),
        (0, 2),
        (3, 4),
        (4, 3),
        (4, 5),
        (5, 4),
        (5, 3),
        (3, 5),
    ]

    for m0, m1 in magic_pairs:
        if b[m0] != 0xEF or b[m1] != 0xBE:
            continue

        remaining = [i for i in all_idx if i not in (m0, m1)]
        for (p1, p2) in _pairings4(remaining):
            for gold_pair, cap_pair in ((p1, p2), (p2, p1)):
                for g0, g1 in (gold_pair, (gold_pair[1], gold_pair[0])):
                    gold = int(b[g0]) | (int(b[g1]) << 8)
                    if gold_max is not None and int(gold) > int(gold_max):
                        continue

                    for c0, c1 in (cap_pair, (cap_pair[1], cap_pair[0])):
                        cap_used = int(b[c0]) | (int(b[c1]) << 8)
                        if cap_max is not None and int(cap_used) > int(cap_max):
                            continue
                        return int(gold), int(cap_used)

    return None


def _beef_candidate_u16(raw6_hex: str) -> dict[str, int]:
    """Decode the 6-byte candidate payload into uint16s (little-endian).

    Tries the same decoder used by prod_emergency inventory reads (handles
    channel-order variations). Falls back to raw sequential decoding.
    """

    try:
        b = bytes.fromhex(str(raw6_hex or ''))
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        b = b''
    if len(b) < 6:
        return {'magic': 0, 'gold': 0, 'cap_used': 0}

    decoded = _decode_beef_from_blob(b[:6], cap_max=None, gold_max=None)
    if decoded is not None:
        gold, cap_used = decoded
        return {'magic': 0xBEEF, 'gold': int(gold), 'cap_used': int(cap_used)}

    # Fallback: sequential bytes (legacy evidence).
    return {
        'magic': int.from_bytes(b[0:2], 'little', signed=False),
        'gold': int.from_bytes(b[2:4], 'little', signed=False),
        'cap_used': int.from_bytes(b[4:6], 'little', signed=False),
    }


def beef_candidate_u16(raw6_hex: str) -> dict[str, int]:
    """Public wrapper for decoding a 6-byte BEEF candidate payload.

    Intended for prod_emergency evidence dumps.
    """

    return _beef_candidate_u16(raw6_hex)


def scan_beef_candidates_all_in_frame(
    frame: Frame,
    *,
    cap_max: int | None = None,
    gold_max: int | None = None,
) -> list[BeefCandidate]:
    """Scan the full frame for ALL pixel-aligned 0xBEEF candidates.

    This is intended for prod_emergency evidence dumps (assisted calibration).
    """

    rgb = bytes(getattr(frame, 'rgb', b'') or b'')
    w = int(getattr(frame, 'width', 0) or 0)
    h = int(getattr(frame, 'height', 0) or 0)
    if not rgb or w <= 0 or h <= 0:
        return []

    out: list[BeefCandidate] = []
    row_stride = w * 3
    for yy in range(h):
        row_base = yy * row_stride
        # Need two pixels (2x1 ROI) => stop at w-1.
        for xx in range(max(0, w - 1)):
            i = row_base + (xx * 3)
            if i + 5 >= len(rgb):
                break
            blob6 = rgb[i : i + 6]
            decoded = _decode_beef_from_blob(blob6, cap_max=cap_max, gold_max=gold_max)
            if decoded is None:
                continue
            gold, cap_used = decoded
            out.append(
                BeefCandidate(
                    x=int(xx),
                    y=int(yy),
                    gold=int(gold),
                    cap_used=int(cap_used),
                    raw6_hex=blob6.hex(),
                )
            )
    return out


def find_beef_marker_roi_within(
    frame: Frame,
    search_roi: Roi,
    *,
    cap_max: int | None = None,
    gold_max: int | None = None,
) -> Optional[Roi]:
    """Locate a 0xBEEF 2x1 marker within a broader ROI.

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
            if _decode_beef_from_blob(blob6, cap_max=cap_max, gold_max=gold_max) is None:
                continue
            return Roi(name=str(search_roi.name), x=int(xx), y=int(yy), width=2, height=1)

    return None


def rank_beef_candidates_by_temporal_stability(
    *,
    before: Frame,
    after_frames: list[Frame],
    cap_max: int | None,
    gold_max: int | None,
    top_n: int = 50,
) -> list[dict[str, object]]:
    """Return top-N candidate coordinates ordered by stability across frames.

    Stability definition (no guessing): count how many frames contain a valid
    0xBEEF candidate at the same (x,y) pixel coordinate.

    Also filters by coherence: candidate must exist in BEFORE and at least one AFTER.
    """

    top_n = max(1, min(int(top_n), 500))

    b_hits = scan_beef_candidates_all_in_frame(before, cap_max=cap_max, gold_max=gold_max)
    if not b_hits or not after_frames:
        return []

    after_hits_by_frame: list[dict[tuple[int, int], BeefCandidate]] = []
    for af in after_frames:
        hits = scan_beef_candidates_all_in_frame(af, cap_max=cap_max, gold_max=gold_max)
        after_hits_by_frame.append({(int(h.x), int(h.y)): h for h in hits})

    stable: list[dict[str, object]] = []
    for b in b_hits:
        key = (int(b.x), int(b.y))
        count = 0
        last_after: BeefCandidate | None = None
        for m in after_hits_by_frame:
            h = m.get(key)
            if h is None:
                continue
            count += 1
            last_after = h
        if count <= 0:
            continue

        stable.append(
            {
                'x': int(b.x),
                'y': int(b.y),
                'w': 2,
                'h': 1,
                'stability': int(count),
                'before': {
                    'raw6_hex': str(b.raw6_hex),
                    'u16': _beef_candidate_u16(str(b.raw6_hex)),
                },
                'after': None
                if last_after is None
                else {
                    'raw6_hex': str(last_after.raw6_hex),
                    'u16': _beef_candidate_u16(str(last_after.raw6_hex)),
                },
            }
        )

    def _k(o: dict[str, object]) -> tuple[int, int, int]:
        try:
            s = int(cast(Any, o.get('stability') or 0))
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
            s = 0
        try:
            xx = int(cast(Any, o.get('x') or 0))
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
            xx = 0
        try:
            yy = int(cast(Any, o.get('y') or 0))
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
            yy = 0
        return (-int(s), int(xx), int(yy))

    stable.sort(key=_k)
    return stable[: int(top_n)]


def rank_beef_candidates_by_temporal_stability_fast(
    *,
    before: Frame,
    after_frames: list[Frame],
    cap_max: int | None,
    gold_max: int | None,
    top_n: int = 50,
    scan_limit: int = 200,
) -> list[dict[str, object]]:
    """Cheaper stability ranking for prod_emergency evidence dumps.

    Uses `scan_beef_candidates_in_frame` (limited hits) instead of scanning ALL
    candidates. This keeps evidence generation lightweight for repeated runs.

    Contract matches `rank_beef_candidates_by_temporal_stability` output shape.
    """

    top_n = max(1, min(int(top_n), 500))
    scan_limit = max(1, min(int(scan_limit), 1000))

    b_hits = scan_beef_candidates_in_frame(before, limit=int(scan_limit), cap_max=cap_max, gold_max=gold_max)
    if not b_hits or not after_frames:
        return []

    after_hits_by_frame: list[dict[tuple[int, int], BeefCandidate]] = []
    for af in after_frames:
        hits = scan_beef_candidates_in_frame(af, limit=int(scan_limit), cap_max=cap_max, gold_max=gold_max)
        after_hits_by_frame.append({(int(h.x), int(h.y)): h for h in hits})

    stable: list[dict[str, object]] = []
    for b in b_hits:
        key = (int(b.x), int(b.y))
        count = 0
        last_after: BeefCandidate | None = None
        for m in after_hits_by_frame:
            h = m.get(key)
            if h is None:
                continue
            count += 1
            last_after = h
        if count <= 0:
            continue

        stable.append(
            {
                'x': int(b.x),
                'y': int(b.y),
                'w': 2,
                'h': 1,
                'stability': int(count),
                'before': {
                    'raw6_hex': str(b.raw6_hex),
                    'u16': _beef_candidate_u16(str(b.raw6_hex)),
                },
                'after': None
                if last_after is None
                else {
                    'raw6_hex': str(last_after.raw6_hex),
                    'u16': _beef_candidate_u16(str(last_after.raw6_hex)),
                },
            }
        )

    def _k(o: dict[str, object]) -> tuple[int, int, int]:
        try:
            s = int(cast(Any, o.get('stability') or 0))
        except Exception:
            s = 0
        try:
            xx = int(cast(Any, o.get('x') or 0))
        except Exception:
            xx = 0
        try:
            yy = int(cast(Any, o.get('y') or 0))
        except Exception:
            yy = 0
        return (-int(s), int(xx), int(yy))

    stable.sort(key=_k)
    return stable[: int(top_n)]


def scan_beef_candidates_in_frame(
    frame: Frame,
    *,
    limit: int = 50,
    cap_max: int | None = None,
    gold_max: int | None = None,
) -> list[BeefCandidate]:
    """Scan a full frame for pixel-aligned 0xBEEF candidates.

    This is intended for calibration/debug in REAL mode. It is not used for
    semantic decisions except to generate evidence when binary inventory is
    unreadable.
    """

    rgb = bytes(getattr(frame, 'rgb', b'') or b'')
    w = int(getattr(frame, 'width', 0) or 0)
    h = int(getattr(frame, 'height', 0) or 0)
    if not rgb or w <= 0 or h <= 0:
        return []

    out: list[BeefCandidate] = []
    # Scan by pixels (step 3 bytes) but decode the full 2x1 ROI blob (6 bytes) with
    # layout tolerance.
    row_stride = w * 3
    for yy in range(h):
        row_base = yy * row_stride
        for xx in range(max(0, w - 1)):
            i = row_base + (xx * 3)
            if i + 5 >= len(rgb):
                break
            blob6 = rgb[i : i + 6]
            decoded = _decode_beef_from_blob(blob6, cap_max=cap_max, gold_max=gold_max)
            if decoded is None:
                continue
            gold, cap_used = decoded
            out.append(
                BeefCandidate(
                    x=int(xx),
                    y=int(yy),
                    gold=int(gold),
                    cap_used=int(cap_used),
                    raw6_hex=blob6.hex(),
                )
            )
            if len(out) >= int(limit):
                return out
    return out


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


def _read_inventory_binary(frame: Frame, roi: Roi) -> Optional[InventorySnapshot]:
    """Decode the prod-emergency binary inventory encoding (magic 0xBEEF)."""

    blob = _roi_bytes(frame, roi)
    if blob is None or len(blob) < 6:
        return None

    # Defensive guard: in REAL captures, the two magic bytes can occur naturally in
    # RGB pixels (false positives). Reject implausible values to avoid treating
    # random UI pixels as an inventory snapshot.
    # Default is intentionally conservative but must cover real accounts.
    # Override with FRBOT_INVENTORY_BINARY_CAP_MAX.
    default_cap_max = 50000
    try:
        cap_max = int(
            (os.environ.get('FRBOT_INVENTORY_BINARY_CAP_MAX', str(default_cap_max)) or str(default_cap_max)).strip()
            or str(default_cap_max)
        )
    except Exception:
        cap_max = int(default_cap_max)
    cap_max = max(1, min(int(cap_max), 65535))

    try:
        raw_gold_max = os.environ.get('FRBOT_INVENTORY_BINARY_GOLD_MAX', '').strip()
        gold_max = int(raw_gold_max, 10) if raw_gold_max else None
    except Exception:
        gold_max = None
    if gold_max is not None:
        gold_max = max(0, min(int(gold_max), 65535))

    decoded = _decode_beef_from_blob(blob[:6], cap_max=int(cap_max), gold_max=gold_max)
    if decoded is None:
        return None

    gold, cap_used = decoded
    return InventorySnapshot(slot_counts={'gold': int(gold)}, capacity_used=int(cap_used))


def read_inventory_binary(frame: Frame, roi: Roi) -> Optional[InventorySnapshot]:
    """Read only the binary inventory encoding (magic 0xBEEF).

    Returns None if the binary encoding is missing/unreadable.
    """

    return _read_inventory_binary(frame, roi)


def _try_import_cv2_np() -> tuple[Any | None, Any | None]:
    try:
        import cv2
        import numpy as np

        return cv2, np
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return None, None


def _load_digit_templates(path: Path) -> dict[str, list[list[int]]]:
    """Load digit templates.

    Format:
    {
      "0": [[...flattened 0/1 pixels...], ...],
      ...
    }
    """

    try:
        raw = path.read_text(encoding='utf-8')
        obj = json.loads(raw)
        out: dict[str, list[list[int]]] = {}
        if not isinstance(obj, dict):
            return {}
        for k, v in obj.items():
            if str(k) not in {'0', '1', '2', '3', '4', '5', '6', '7', '8', '9'}:
                continue
            if not isinstance(v, list):
                continue
            templates: list[list[int]] = []
            for t in v:
                if not isinstance(t, list):
                    continue
                try:
                    templates.append([int(x) for x in t])
                except Exception:
                    continue
            if templates:
                out[str(k)] = templates
        return out
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return {}


def _visual_templates_path() -> str:
    p = (os.environ.get('FRBOT_INVENTORY_VISUAL_TEMPLATES', '') or '').strip()
    if p:
        return p
    # Match tools/calibrate_inventory_visual_templates.py default and docs.
    return 'diagnostics/inventory_digit_templates.json'


def _binarize_roi_rgb(rgb: bytes, *, w: int, h: int) -> Optional[tuple[bytes, int, int]]:
    cv2, np = _try_import_cv2_np()
    if cv2 is None or np is None:
        return None

    if not rgb or w <= 0 or h <= 0:
        return None

    try:
        img = np.frombuffer(rgb, dtype=np.uint8).reshape((int(h), int(w), 3))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Heuristic: digits are bright on dark background, but a fixed threshold is brittle
        # across capture sources/themes. Choose a threshold by scoring digit-like blobs.
        gray = cv2.medianBlur(gray, 3)

        mean = float(gray.mean())
        std = float(gray.std())
        base_thr = int(mean + 1.0 * std)

        def clamp_thr(v: int) -> int:
            if v < 60:
                return 60
            if v > 200:
                return 200
            return int(v)

        candidates = [
            clamp_thr(base_thr),
            clamp_thr(base_thr - 10),
            clamp_thr(base_thr + 10),
            120,
            130,
            140,
            150,
            160,
        ]
        # de-dupe while preserving order
        seen: set[int] = set()
        uniq: list[int] = []
        for t in candidates:
            tt = int(t)
            if tt not in seen:
                seen.add(tt)
                uniq.append(tt)

        best_bw = None
        best_score = -1
        min_area = max(4, int((w * h) * 0.002))
        # Left cutoff is intended to skip the 'Cap:'/'Soul:' label area.
        # Keep it bounded so widening the ROI (e.g., to include decimals) doesn't
        # push valid digits below the cutoff.
        min_x = min(6, int(w * 0.20))
        min_h = max(6, int(h * 0.45))

        for thr in uniq:
            _thr, bw0 = cv2.threshold(gray, int(thr), 255, cv2.THRESH_BINARY)
            bw = cv2.medianBlur(bw0, 3)

            try:
                contours, _hier = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            except Exception:
                continue

            score = 0
            for c in contours:
                x, y, ww, hh = cv2.boundingRect(c)
                if ww <= 0 or hh <= 0:
                    continue
                if (ww * hh) < min_area:
                    continue
                if x < min_x:
                    continue
                if hh < min_h:
                    continue
                if ww < 2:
                    continue
                # exclude huge blobs that are unlikely to be a single digit
                if ww >= int(w * 0.75) or hh >= int(h * 0.95):
                    continue
                score += 1

            if score > best_score:
                best_score = int(score)
                best_bw = bw

        if best_bw is None:
            return None
        return (best_bw.tobytes(), int(w), int(h))
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return None


def _extract_digit_glyphs_from_binary(binary: bytes, *, w: int, h: int) -> list[bytes]:
    """Extract digit glyph bitmaps from a binarized (0/255) image.

    Returns glyphs left-to-right. Output glyphs are resized to a canonical size.
    """

    cv2, np = _try_import_cv2_np()
    if cv2 is None or np is None:
        return []

    try:
        bw = np.frombuffer(binary, dtype=np.uint8).reshape((int(h), int(w)))

        contours, _hier = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Only consider blobs on the right side (skip 'Cap:'/'Soul:' labels).
        boxes: list[tuple[int, int, int, int]] = []
        min_area = max(4, int((w * h) * 0.002))
        min_x = min(6, int(w * 0.20))
        for c in contours:
            x, y, ww, hh = cv2.boundingRect(c)
            if ww <= 0 or hh <= 0:
                continue
            if (ww * hh) < min_area:
                continue
            if x < min_x:
                continue
            # digits are typically taller than wide
            if hh < max(6, int(h * 0.45)) or ww < 2:
                continue
            boxes.append((int(x), int(y), int(ww), int(hh)))

        boxes.sort(key=lambda b: b[0])
        glyphs: list[bytes] = []
        for x, y, ww, hh in boxes:
            crop = bw[y : y + hh, x : x + ww]
            # normalize size for template matching
            canon_w, canon_h = 12, 16
            resized = cv2.resize(crop, (int(canon_w), int(canon_h)), interpolation=cv2.INTER_NEAREST)
            glyphs.append(resized.tobytes())
        return glyphs
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return []


def _match_glyph_to_digit(glyph: bytes, templates: dict[str, list[list[int]]]) -> Optional[str]:
    """Match a canonical glyph bitmap to the best digit template."""

    cv2, np = _try_import_cv2_np()
    if cv2 is None or np is None:
        return None

    try:
        g = np.frombuffer(glyph, dtype=np.uint8)
        if g.size <= 0:
            return None
        g01 = (g > 0).astype(np.uint8)

        best_digit: Optional[str] = None
        best_score = 0.0

        for digit, templ_list in templates.items():
            for t in templ_list:
                tt = np.array(t, dtype=np.uint8)
                if tt.size != g01.size:
                    continue
                # similarity = 1 - normalized hamming distance
                diff = np.bitwise_xor(g01, (tt > 0).astype(np.uint8))
                score = 1.0 - float(diff.mean())
                if score > best_score:
                    best_score = float(score)
                    best_digit = str(digit)

        if best_digit is None:
            return None
        # conservative threshold to avoid false positives in prod-emergency
        if best_score < 0.85:
            return None
        return best_digit
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return None


def _read_line_number_from_roi(frame: Frame, roi: Roi, *, y0: int, y1: int, templates: dict[str, list[list[int]]]) -> Optional[int]:
    rgb = _roi_bytes(frame, Roi(name=roi.name, x=int(roi.x), y=int(roi.y) + int(y0), width=int(roi.width), height=max(1, int(y1 - y0))))
    if rgb is None:
        return None

    bw = _binarize_roi_rgb(rgb, w=int(roi.width), h=max(1, int(y1 - y0)))
    if bw is None:
        return None
    binary, w, h = bw
    glyphs = _extract_digit_glyphs_from_binary(binary, w=w, h=h)
    if not glyphs:
        return None

    digits: list[str] = []
    for g in glyphs:
        d = _match_glyph_to_digit(g, templates)
        if d is None:
            return None
        digits.append(str(d))
    try:
        return int(''.join(digits))
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return None


def _read_inventory_visual(frame: Frame, roi: Roi, *, baseline_cap_remaining: Optional[int] = None) -> Optional[InventorySnapshot]:
    """Read inventory snapshot from visible Cap/Soul UI.

    This requires a calibrated `inventory_text` ROI covering the Soul/Cap lines and
    a digit template file at FRBOT_INVENTORY_VISUAL_TEMPLATES.

    Evidence metric:
    - capacity_used is a proxy = baseline_cap_remaining - cap_remaining.
      This makes looting (cap_remaining decreases) yield capacity_used_delta > 0.
    """

    templates_path = _visual_templates_path()

    p = Path(templates_path)
    if not p.exists():
        return None

    templates = _load_digit_templates(p)
    if not templates:
        return None

    h = int(roi.height)
    if h < 8:
        return None

    # Heuristic split: top half ~ Soul line, bottom half ~ Cap line.
    mid = h // 2

    soul = _read_line_number_from_roi(frame, roi, y0=0, y1=mid, templates=templates)
    cap_remaining = _read_line_number_from_roi(frame, roi, y0=mid, y1=h, templates=templates)

    # If ROI contains only one line (e.g. only Cap), fall back to reading digits from the full ROI.
    if cap_remaining is None:
        cap_remaining = _read_line_number_from_roi(frame, roi, y0=0, y1=h, templates=templates)
    if cap_remaining is None:
        return None

    base = cap_remaining if baseline_cap_remaining is None else int(baseline_cap_remaining)
    cap_used_proxy = int(base) - int(cap_remaining)

    # Keep gold empty in visual mode; preserve soul as a best-effort semantic counter.
    slot_counts: dict[str, int] = {}
    if soul is not None:
        slot_counts['soul'] = int(soul)

    return InventorySnapshot(slot_counts=slot_counts, capacity_used=int(cap_used_proxy))


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

    inv = _read_inventory_binary(frame, roi)
    if inv is not None:
        return inv

    if str(os.environ.get('FRBOT_INVENTORY_VISUAL_ENABLED', '1') or '1').strip().lower() in {'0', 'false', 'no', 'off'}:
        return None

    return _read_inventory_visual(frame, roi, baseline_cap_remaining=None)


def read_inventory_pair(before: Frame, after: Frame, roi: Roi) -> Optional[tuple[InventorySnapshot, InventorySnapshot]]:
    """Read a semantically comparable before/after pair.

    - If the binary encoding is present: returns the binary snapshots.
    - Otherwise uses the visible Cap metric with a baseline from BEFORE.
    """

    b_bin = _read_inventory_binary(before, roi)
    a_bin = _read_inventory_binary(after, roi)
    if b_bin is not None and a_bin is not None:
        return b_bin, a_bin

    # If visual inventory reading is disabled, do not fall back.
    if str(os.environ.get('FRBOT_INVENTORY_VISUAL_ENABLED', '1') or '1').strip().lower() in {'0', 'false', 'no', 'off'}:
        return None

    # Visual fallback requires templates and a readable cap_remaining in BEFORE.
    # We obtain baseline by reading cap_remaining once (via visual reader with base=None).
    b0 = _read_inventory_visual(before, roi, baseline_cap_remaining=None)
    if b0 is None:
        return None

    # baseline is encoded as 0 in capacity_used proxy when baseline is None,
    # but we need the absolute cap_remaining; re-read by inverting the proxy is not possible.
    # So we directly read cap_remaining from BEFORE line again.
    templates_path = _visual_templates_path()
    p = Path(templates_path)
    templates = _load_digit_templates(p)
    if not templates:
        return None

    h = int(roi.height)
    mid = h // 2
    cap_before = _read_line_number_from_roi(before, roi, y0=mid, y1=h, templates=templates)
    if cap_before is None:
        cap_before = _read_line_number_from_roi(before, roi, y0=0, y1=h, templates=templates)
    if cap_before is None:
        return None

    b_vis = _read_inventory_visual(before, roi, baseline_cap_remaining=int(cap_before))
    a_vis = _read_inventory_visual(after, roi, baseline_cap_remaining=int(cap_before))
    if b_vis is None or a_vis is None:
        return None
    return b_vis, a_vis


def read_inventory_pair_binary(before: Frame, after: Frame, roi: Roi) -> Optional[tuple[InventorySnapshot, InventorySnapshot]]:
    """Read a comparable pair, binary encoding only."""

    b_bin = _read_inventory_binary(before, roi)
    a_bin = _read_inventory_binary(after, roi)
    if b_bin is None or a_bin is None:
        return None
    return b_bin, a_bin


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
