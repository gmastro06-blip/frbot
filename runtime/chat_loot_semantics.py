from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json
import os

from contracts.capture import Frame
from contracts.evidence import Roi


@dataclass(frozen=True, slots=True)
class LootEvidence:
    ok: bool
    delta_items: int
    delta_gold: int
    source: str
    debug: dict[str, Any]


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

    row_stride = int(frame.width) * 3
    out = bytearray(int(roi.width) * int(roi.height) * 3)
    out_row_stride = int(roi.width) * 3
    src = frame.rgb
    for row in range(int(roi.height)):
        src_start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = src[src_start:src_end]
    return bytes(out)


def _dhash64(rgb: bytes, *, w: int, h: int) -> int:
    """Compute a 64-bit difference hash for an RGB region.

    No OCR: this is a pure pixel-level signature used for template-style matching.
    """

    if not rgb or w <= 0 or h <= 0:
        return 0

    # Downsample to 9x8 luma grid (nearest-neighbor).
    ow = 9
    oh = 8
    row_stride = int(w) * 3

    def sample_luma(x: int, y: int) -> int:
        xx = max(0, min(int(w - 1), int(x)))
        yy = max(0, min(int(h - 1), int(y)))
        i = int(yy) * row_stride + int(xx) * 3
        if i + 2 >= len(rgb):
            return 0
        r = int(rgb[i + 0])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        return int((r * 2126 + g * 7152 + b * 722) // 10000)

    grid: list[list[int]] = []
    for yy in range(oh):
        sy = int(round((yy / float(oh - 1)) * float(h - 1))) if oh > 1 else 0
        row: list[int] = []
        for xx in range(ow):
            sx = int(round((xx / float(ow - 1)) * float(w - 1))) if ow > 1 else 0
            row.append(sample_luma(sx, sy))
        grid.append(row)

    # Adjacent horizontal comparisons -> 8x8 bits.
    out = 0
    bit = 0
    for yy in range(oh):
        r = grid[yy]
        for xx in range(8):
            out |= (1 if r[xx] > r[xx + 1] else 0) << bit
            bit += 1
    return int(out)


def _ham64(a: int, b: int) -> int:
    return int((int(a) ^ int(b)).bit_count())


def _find_changed_bands(
    before_rgb: bytes,
    after_rgb: bytes,
    *,
    w: int,
    h: int,
    px_tol: int = 18,
) -> list[tuple[int, int, int, int]]:
    """Return list of (x0,y0,x1,y1) boxes for changed text-like bands."""

    if not before_rgb or not after_rgb or w <= 0 or h <= 0:
        return []
    if len(before_rgb) != len(after_rgb):
        return []

    row_stride = int(w) * 3

    # Per-row change density.
    changed_rows = [0] * int(h)
    for yy in range(int(h)):
        base = int(yy) * row_stride
        c = 0
        for i in range(base, base + row_stride, 3):
            dr = abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0]))
            dg = abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1]))
            db = abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2]))
            if dr > px_tol or dg > px_tol or db > px_tol:
                c += 1
        changed_rows[int(yy)] = int(c)

    # Row threshold: at least ~1.5% pixels changed.
    row_thresh = max(8, int(float(w) * 0.015))

    bands: list[tuple[int, int]] = []
    in_band = False
    start = 0
    for yy in range(int(h)):
        hot = changed_rows[int(yy)] >= row_thresh
        if hot and not in_band:
            in_band = True
            start = int(yy)
        elif (not hot) and in_band:
            in_band = False
            end = int(yy) - 1
            if end - start + 1 >= 6:
                bands.append((start, end))
    if in_band:
        end = int(h) - 1
        if end - start + 1 >= 6:
            bands.append((start, end))

    # For each band, compute a tight bbox of changed pixels.
    out: list[tuple[int, int, int, int]] = []
    for (y0, y1) in bands:
        minx = 10**9
        maxx = -1
        miny = int(y0)
        maxy = int(y1)

        for yy in range(int(y0), int(y1) + 1):
            base = int(yy) * row_stride
            for xx in range(int(w)):
                i = base + int(xx) * 3
                dr = abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0]))
                dg = abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1]))
                db = abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2]))
                if dr > px_tol or dg > px_tol or db > px_tol:
                    if xx < minx:
                        minx = int(xx)
                    if xx > maxx:
                        maxx = int(xx)

        if maxx < minx:
            continue

        # Expand a bit for stable hashing.
        pad = 4
        x0 = max(0, int(minx) - pad)
        x1 = min(int(w - 1), int(maxx) + pad)
        if (x1 - x0 + 1) < 40:
            continue
        if (maxy - miny + 1) < 6:
            continue

        out.append((int(x0), int(miny), int(x1), int(maxy)))

    return out


def _crop_box(rgb: bytes, *, w: int, h: int, x0: int, y0: int, x1: int, y1: int) -> bytes:
    if not rgb or w <= 0 or h <= 0:
        return b''
    x0 = max(0, min(int(w - 1), int(x0)))
    x1 = max(0, min(int(w - 1), int(x1)))
    y0 = max(0, min(int(h - 1), int(y0)))
    y1 = max(0, min(int(h - 1), int(y1)))
    if x1 < x0 or y1 < y0:
        return b''

    bw = int(x1 - x0 + 1)
    bh = int(y1 - y0 + 1)
    row_stride = int(w) * 3
    out_row = int(bw) * 3
    out = bytearray(int(bw) * int(bh) * 3)

    dst = 0
    for yy in range(int(y0), int(y1) + 1):
        src0 = int(yy) * row_stride + int(x0) * 3
        src1 = src0 + out_row
        out[dst : dst + out_row] = rgb[src0:src1]
        dst += out_row

    return bytes(out)


def _load_patterns() -> tuple[list[int], int]:
    """Load reference dhashes for loot chat patterns.

    This is intentionally NOT OCR: patterns are pixel-hash signatures of known
    loot message renderings (font/theme specific).
    """

    p = (os.environ.get('FRBOT_CHAT_LOOT_PATTERNS_PATH', '') or '').strip()
    if not p:
        return [], 0

    try:
        data = json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        return [], 0

    try:
        tol = int(data.get('tolerance', 6))
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        tol = 6
    tol = max(0, min(int(tol), 32))

    hashes: list[int] = []
    for item in (data.get('dhashes') or []):
        try:
            hx = str(item.get('hex') or '').strip().lower()
            if hx.startswith('0x'):
                hx = hx[2:]
            if not hx:
                continue
            hashes.append(int(hx, 16))
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            continue

    return hashes, int(tol)


def detect_loot_from_chat(
    before: Frame,
    after: Frame,
    roi: Roi,
    *,
    action_kind: str | None = None,
    action_ts_ns: int | None = None,
) -> LootEvidence:
    """Secondary evidence: detect new loot chat lines via pixel-delta hashing.

    Contract:
    - No OCR.
    - No string parsing.
    - Pixel-only evidence derived from ROI changes.
    """

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    before_crop = _crop_rgb(before, roi)
    after_crop = _crop_rgb(after, roi)

    w = int(roi.width)
    h = int(roi.height)

    try:
        prefix_w = int((os.environ.get('FRBOT_CHAT_LOOT_PREFIX_W', '220') or '220').strip() or '220')
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        prefix_w = 220
    prefix_w = max(40, min(int(prefix_w), int(w)))

    patterns, tol = _load_patterns()

    # Optional dev override; hard-disabled in PROD_EMERGENCY.
    allow_any_delta = (os.environ.get('FRBOT_CHAT_LOOT_ALLOW_ANY_DELTA', '') or '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    if profile == 'prod_emergency':
        allow_any_delta = False

    try:
        px_tol = int((os.environ.get('FRBOT_CHAT_LOOT_PX_TOL', '18') or '18').strip() or '18')
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        px_tol = 18
    px_tol = max(0, min(int(px_tol), 255))

    # Timing/action binding (strict in PROD_EMERGENCY).
    latency_ms: float | None = None
    after_ts = int(getattr(after, 'monotonic_ts_ns', 0) or 0)
    if action_ts_ns is not None and after_ts > 0:
        latency_ms = float(int(after_ts) - int(action_ts_ns)) / 1_000_000.0

    act = str(action_kind or '').strip().lower()

    # Chat fallback timing window.
    # - Default: 1500ms
    # - PROD_EMERGENCY + alt_q only: widen slightly to tolerate observed chat render latency.
    try:
        if profile == 'prod_emergency' and act == 'alt_q':
            max_latency_ms = int(
                (
                    os.environ.get('FRBOT_CHAT_LOOT_MAX_LATENCY_MS_ALT_Q', '2500')
                    or '2500'
                ).strip()
                or '2500'
            )
        else:
            max_latency_ms = int(
                (
                    os.environ.get('FRBOT_CHAT_LOOT_MAX_LATENCY_MS', '1500')
                    or '1500'
                ).strip()
                or '1500'
            )
    except Exception:
        max_latency_ms = 2500 if (profile == 'prod_emergency' and act == 'alt_q') else 1500
    max_latency_ms = max(50, min(int(max_latency_ms), 10000))
    boxes = _find_changed_bands(before_crop, after_crop, w=w, h=h, px_tol=int(px_tol))

    def _changed_stats(before_rgb: bytes, after_rgb: bytes, *, w: int, h: int, px_tol: int) -> tuple[int, float]:
        if not before_rgb or not after_rgb or w <= 0 or h <= 0:
            return 0, 0.0
        if len(before_rgb) != len(after_rgb):
            return 0, 0.0

        changed = 0
        for i in range(0, len(before_rgb), 3):
            if (
                abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0])) > px_tol
                or abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1])) > px_tol
                or abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2])) > px_tol
            ):
                changed += 1
        total = int(w) * int(h)
        return int(changed), (float(changed) / float(total) if total else 0.0)

    changed_px, changed_ratio = _changed_stats(before_crop, after_crop, w=w, h=h, px_tol=int(px_tol))

    def _green_changed_ratio(
        before_rgb: bytes,
        after_rgb: bytes,
        *,
        w: int,
        h: int,
        px_tol: int,
        green_delta: int,
        green_min_g: int,
    ) -> tuple[int, int, float]:
        if not before_rgb or not after_rgb or w <= 0 or h <= 0:
            return 0, 0, 0.0
        if len(before_rgb) != len(after_rgb):
            return 0, 0, 0.0

        changed = 0
        greenish = 0
        for i in range(0, len(before_rgb), 3):
            dr = abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0]))
            dg = abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1]))
            db = abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2]))
            if dr > px_tol or dg > px_tol or db > px_tol:
                changed += 1
                r = int(after_rgb[i + 0])
                g = int(after_rgb[i + 1])
                b = int(after_rgb[i + 2])
                if g >= int(green_min_g) and g >= (r + int(green_delta)) and g >= (b + int(green_delta)):
                    greenish += 1

        ratio = (float(greenish) / float(changed)) if changed > 0 else 0.0
        return int(changed), int(greenish), float(ratio)

    try:
        min_changed_px = int((os.environ.get('FRBOT_CHAT_LOOT_MIN_CHANGED_PIXELS', '600') or '600').strip() or '600')
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        min_changed_px = 600
    min_changed_px = max(1, int(min_changed_px))

    try:
        min_ratio = float((os.environ.get('FRBOT_CHAT_LOOT_MIN_RATIO', '0.0015') or '0.0015').strip() or '0.0015')
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        min_ratio = 0.0015
    min_ratio = max(0.0, float(min_ratio))

    try:
        max_ratio = float((os.environ.get('FRBOT_CHAT_LOOT_MAX_RATIO', '0.20') or '0.20').strip() or '0.20')
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        max_ratio = 0.20
    max_ratio = max(0.0, float(max_ratio))

    debug: dict[str, Any] = {
        'roi': {'x': int(roi.x), 'y': int(roi.y), 'w': int(roi.width), 'h': int(roi.height)},
        'action_kind': str(action_kind or ''),
        'delta_latency_ms': None if latency_ms is None else float(latency_ms),
        'max_latency_ms': int(max_latency_ms),
        'px_tol': int(px_tol),
        'prefix_w': int(prefix_w),
        'patterns_loaded': int(len(patterns)),
        'tolerance': int(tol),
        'allow_any_delta': bool(allow_any_delta),
        'boxes': [list(map(int, b)) for b in boxes],
        'delta_lines': int(len(boxes)),
        'delta_area_px': int(sum((x1 - x0 + 1) * (y1 - y0 + 1) for (x0, y0, x1, y1) in boxes)),
        'changed_pixels': int(changed_px),
        'changed_ratio': float(changed_ratio),
    }

    # Timing/action binding (strict in PROD_EMERGENCY).
    # Keep diagnostics: even if timing fails, we still report pixel-delta stats.
    if profile == 'prod_emergency':
        if act != 'alt_q':
            debug['reason'] = 'action_mismatch'
            return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)
        if latency_ms is None:
            debug['reason'] = 'latency_unknown'
            return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)
        if float(latency_ms) < 0.0 or float(latency_ms) > float(max_latency_ms):
            debug['reason'] = 'latency_out_of_window'
            return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)

    # Noise rejection: reject tiny changes and large redraws.
    if int(changed_px) < int(min_changed_px) or float(changed_ratio) < float(min_ratio) or float(changed_ratio) > float(max_ratio):
        debug['reason'] = 'noise_or_insufficient_delta'
        return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)

    # Multiline requirement.
    if len(boxes) < 2:
        debug['reason'] = 'not_multiline'
        return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)

    # Bottom-bias: at least one band near the bottom.
    bottom_thresh = int(float(h) * 0.75)
    if not any(int(y1) >= int(bottom_thresh) for (_x0, _y0, _x1, y1) in boxes):
        debug['reason'] = 'delta_not_near_bottom'
        debug['bottom_thresh'] = int(bottom_thresh)
        return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)

    # If explicitly allowed, accept any semantic pixel delta even without patterns.
    if allow_any_delta and not patterns:
        hb = _dhash64(before_crop, w=w, h=h)
        ha = _dhash64(after_crop, w=w, h=h)
        ok_any = int(ha) != int(hb)
        debug['reason'] = 'allow_any_delta' if ok_any else 'allow_any_delta_nohash'
        debug['roi_dhash_after'] = hex(int(ha))
        debug['roi_dhash_before'] = hex(int(hb))
        return LootEvidence(ok=bool(ok_any), delta_items=1 if ok_any else 0, delta_gold=0, source='chat', debug=debug)

    matched = 0
    candidates: list[dict[str, Any]] = []
    for (x0, y0, x1, y1) in boxes:
        x1p = min(int(x1), int(x0) + int(prefix_w) - 1)
        patch_after = _crop_box(after_crop, w=w, h=h, x0=x0, y0=y0, x1=x1p, y1=y1)
        patch_before = _crop_box(before_crop, w=w, h=h, x0=x0, y0=y0, x1=x1p, y1=y1)
        bw = int(x1p - x0 + 1)
        bh = int(y1 - y0 + 1)
        ha = _dhash64(patch_after, w=bw, h=bh)
        hb = _dhash64(patch_before, w=bw, h=bh)

        best = None
        is_hit = False
        if patterns:
            best = min((_ham64(ha, ph) for ph in patterns), default=None)
            is_hit = best is not None and int(best) <= int(tol)

        candidates.append(
            {
                'box': [int(x0), int(y0), int(x1), int(y1)],
                'box_prefix': [int(x0), int(y0), int(x1p), int(y1)],
                'dhash_after': hex(int(ha)),
                'dhash_before': hex(int(hb)),
                'best_hamming': None if best is None else int(best),
                'hit': bool(is_hit),
            }
        )

        if is_hit:
            matched += 1

    debug['candidates'] = candidates

    if patterns:
        # PROD_EMERGENCY: require >=2 matched lines (two new loot lines) to reduce false positives.
        req = 2 if profile == 'prod_emergency' else 1
        ok = int(matched) >= int(req)
        debug['required_matches'] = int(req)
        debug['reason'] = 'pattern_match' if ok else 'pattern_miss'
        return LootEvidence(ok=bool(ok), delta_items=int(matched), delta_gold=0, source='chat', debug=debug)

    # No patterns configured:
    # - Outside PROD_EMERGENCY: accept multiline + bottom-biased delta (legacy/dev convenience).
    # - In PROD_EMERGENCY: require loot-like green text dominance in the changed pixels.
    if profile == 'prod_emergency':
        try:
            green_delta = int((os.environ.get('FRBOT_CHAT_LOOT_GREEN_DELTA', '25') or '25').strip() or '25')
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            green_delta = 25
        green_delta = max(0, min(int(green_delta), 255))

        try:
            green_min_g = int((os.environ.get('FRBOT_CHAT_LOOT_GREEN_MIN_G', '60') or '60').strip() or '60')
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            green_min_g = 60
        green_min_g = max(0, min(int(green_min_g), 255))

        try:
            min_green_ratio = float((os.environ.get('FRBOT_CHAT_LOOT_GREEN_MIN_RATIO', '0.08') or '0.08').strip() or '0.08')
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            min_green_ratio = 0.08
        min_green_ratio = max(0.0, min(float(min_green_ratio), 1.0))

        try:
            max_green_ratio = float((os.environ.get('FRBOT_CHAT_LOOT_GREEN_MAX_RATIO', '0.98') or '0.98').strip() or '0.98')
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            max_green_ratio = 0.98
        max_green_ratio = max(0.0, min(float(max_green_ratio), 1.0))

        try:
            min_green_px = int((os.environ.get('FRBOT_CHAT_LOOT_GREEN_MIN_PIXELS', '120') or '120').strip() or '120')
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
            min_green_px = 120
        min_green_px = max(0, int(min_green_px))

        changed2, green_px, green_ratio = _green_changed_ratio(
            before_crop,
            after_crop,
            w=w,
            h=h,
            px_tol=int(px_tol),
            green_delta=int(green_delta),
            green_min_g=int(green_min_g),
        )
        debug['green_changed_pixels'] = int(green_px)
        debug['green_changed_ratio'] = float(green_ratio)
        debug['green_min_ratio'] = float(min_green_ratio)
        debug['green_max_ratio'] = float(max_green_ratio)
        debug['green_min_pixels'] = int(min_green_px)

        if int(changed2) <= 0:
            debug['reason'] = 'no_changed_pixels'
            return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)
        if int(green_px) < int(min_green_px) or float(green_ratio) < float(min_green_ratio) or float(green_ratio) > float(max_green_ratio):
            debug['reason'] = 'no_patterns_and_not_green_loot'
            return LootEvidence(ok=False, delta_items=0, delta_gold=0, source='chat', debug=debug)

        debug['reason'] = 'green_multiline_no_patterns'
        return LootEvidence(ok=True, delta_items=int(len(boxes)), delta_gold=0, source='chat', debug=debug)

    debug['reason'] = 'multiline_delta_no_patterns'
    return LootEvidence(ok=True, delta_items=int(len(boxes)), delta_gold=0, source='chat', debug=debug)
