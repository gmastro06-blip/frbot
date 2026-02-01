from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi
from diagnostics.ppm import PpmImage, read_ppm


@dataclass(frozen=True, slots=True)
class FrameBundle:
    full: Optional[PpmImage]
    minimap: Optional[PpmImage]


@dataclass(frozen=True, slots=True)
class Pair:
    gate: str
    stamp: str
    reason: str
    before: FrameBundle
    after: FrameBundle


_FILENAME_RE = re.compile(
    r'^(?P<gate>[a-z0-9-]+)_(?P<stamp>\d{8}-\d{6})_(?P<reason>.+)_(?P<side>before|after)(?P<mini>_minimap)?\.ppm$',
    re.IGNORECASE,
)


def _load_rois(config_path: str) -> dict[str, Roi]:
    data = json.loads(Path(config_path).read_text(encoding='utf-8'))
    rois_node = data.get('rois') if isinstance(data, dict) else None
    if not isinstance(rois_node, dict):
        raise ValueError('config missing required object: rois')

    rois: dict[str, Roi] = {}
    for name, roi_raw in rois_node.items():
        if not isinstance(name, str) or not isinstance(roi_raw, dict):
            continue
        rois[name] = Roi(
            name=name,
            x=int(roi_raw['x']),
            y=int(roi_raw['y']),
            width=int(roi_raw['width']),
            height=int(roi_raw['height']),
        )
    return rois


def _bundle_to_frame(bundle: FrameBundle) -> Optional[Frame]:
    if bundle.full is None:
        return None

    f = bundle.full
    minimap_detected = bundle.minimap is not None
    minimap_rgb = b''
    minimap_w = 0
    minimap_h = 0
    if bundle.minimap is not None:
        minimap_rgb = bundle.minimap.rgb
        minimap_w = int(bundle.minimap.width)
        minimap_h = int(bundle.minimap.height)

    return Frame(
        width=int(f.width),
        height=int(f.height),
        monotonic_ts_ns=0,
        digest_hex='',
        rgb=f.rgb,
        minimap_detected=bool(minimap_detected),
        minimap_rgb=minimap_rgb,
        minimap_width=minimap_w,
        minimap_height=minimap_h,
        minimap_digest_hex='',
    )


def _roi_in_bounds(img: PpmImage, roi: Roi) -> tuple[bool, str]:
    if int(roi.width) <= 0 or int(roi.height) <= 0:
        return False, 'roi_invalid_dimensions'
    if int(roi.x) < 0 or int(roi.y) < 0:
        return False, 'roi_negative_origin'
    if (int(roi.x) + int(roi.width)) > int(img.width) or (int(roi.y) + int(roi.height)) > int(img.height):
        return False, 'roi_out_of_bounds'
    return True, 'ok'


def _crop_rgb(img: PpmImage, roi: Roi) -> bytes:
    w = int(img.width)
    row_stride = w * 3
    out_row_stride = int(roi.width) * 3
    out = bytearray(int(roi.width) * int(roi.height) * 3)
    for row in range(int(roi.height)):
        src_start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = img.rgb[src_start:src_end]
    return bytes(out)


def _byte_change_ratio(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    changed = 0
    for i in range(len(a)):
        if a[i] != b[i]:
            changed += 1
    return float(changed) / float(len(a))


def _contrast_score(rgb: bytes) -> float:
    if not rgb:
        return 0.0
    n = len(rgb) // 3
    if n <= 0:
        return 0.0
    step = max(1, n // 10_000)
    vals: list[int] = []
    idx = 0
    for _ in range(0, n, step):
        r = rgb[idx]
        g = rgb[idx + 1]
        b = rgb[idx + 2]
        y = (int(r) * 77 + int(g) * 150 + int(b) * 29) >> 8
        vals.append(int(y))
        idx += 3 * step
        if idx + 2 >= len(rgb):
            break
    if not vals:
        return 0.0
    mean = sum(vals) / float(len(vals))
    var = sum((v - mean) ** 2 for v in vals) / float(len(vals))
    return float(var)


def _parse_pairs(frames_dir: Path) -> list[Pair]:
    items = list(frames_dir.glob('*.ppm'))
    by_key: dict[tuple[str, str, str], dict[str, dict[str, Path]]] = {}

    for p in items:
        m = _FILENAME_RE.match(p.name)
        if not m:
            continue
        gate = (m.group('gate') or '').lower()
        stamp = m.group('stamp') or ''
        reason = m.group('reason') or ''
        side = (m.group('side') or '').lower()
        is_mini = bool(m.group('mini'))

        key = (gate, stamp, reason)
        bucket = by_key.setdefault(key, {'before': {}, 'after': {}})
        bucket[side]['minimap' if is_mini else 'full'] = p

    out: list[Pair] = []
    for (gate, stamp, reason), sides in sorted(by_key.items()):
        b_full = sides['before'].get('full')
        a_full = sides['after'].get('full')
        if b_full is None and a_full is None:
            continue

        before = FrameBundle(
            full=(read_ppm(b_full) if b_full is not None else None),
            minimap=(read_ppm(sides['before'].get('minimap')) if sides['before'].get('minimap') is not None else None),
        )
        after = FrameBundle(
            full=(read_ppm(a_full) if a_full is not None else None),
            minimap=(read_ppm(sides['after'].get('minimap')) if sides['after'].get('minimap') is not None else None),
        )

        out.append(Pair(gate=gate, stamp=stamp, reason=reason, before=before, after=after))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='Evidence-only REAL-mode audit (frames + ROIs)')
    ap.add_argument('--frames', default='diagnostics/frames', help='frames directory (PPM)')
    ap.add_argument('--config', default=os.environ.get('FRBOT_CONFIG_PATH', ''), help='ROI config (json with rois{})')
    ap.add_argument('--max-pairs', type=int, default=50)
    args = ap.parse_args()

    frames_dir = Path(str(args.frames))
    if not frames_dir.exists():
        print('DECISION: REAL-MODE NO APTO')
        print('REASON: NO_EVIDENCE_FRAMES (diagnostics/frames missing)')
        print('ACTION: run a gate with FRBOT_DUMP_FRAMES=1 and ensure preflight reaches at least one grab')
        return 2

    config_path = str(args.config or '').strip()
    if not config_path:
        print('DECISION: REAL-MODE NO APTO')
        print('REASON: NO_CONFIG (FRBOT_CONFIG_PATH missing)')
        return 2

    rois = _load_rois(config_path)

    pairs = _parse_pairs(frames_dir)
    if not pairs:
        print('DECISION: REAL-MODE NO APTO')
        print('REASON: NO_PARSEABLE_FRAMES (filenames not recognized)')
        return 2

    pairs = pairs[: int(args.max_pairs)]

    idle_pairs = [p for p in pairs if 'idle' in p.reason.lower()]
    if not idle_pairs:
        print('DECISION: REAL-MODE NO APTO')
        print('REASON: INSUFFICIENT_EVIDENCE (no idle BEFORE/AFTER frames)')
        print('ACTION: capture idle frames (no input) with FRBOT_DUMP_FRAMES=1 before any gate input')
        return 2

    critical_rois = {
        'minimap': rois.get('minimap'),
        'battle_list': rois.get('battle_list'),
        'inventory_text': rois.get('inventory_text'),
        'hp_bar': rois.get('hp_bar'),
        'mp_bar': rois.get('mp_bar'),
        'trade_npc': rois.get('trade_npc'),
        'trade_inventory': rois.get('trade_inventory'),
    }

    calibration_fail: list[str] = []
    for name, roi in critical_rois.items():
        if roi is None:
            calibration_fail.append(f'{name}:roi_missing_in_config')
            continue

        ip = idle_pairs[0]
        if ip.before.full is None or ip.after.full is None:
            calibration_fail.append(f'{name}:idle_frames_missing_full')
            continue

        ok, why = _roi_in_bounds(ip.before.full, roi)
        if not ok:
            calibration_fail.append(f'{name}:{why}')
            continue

        b = _crop_rgb(ip.before.full, roi)
        a = _crop_rgb(ip.after.full, roi)

        ratio = _byte_change_ratio(b, a)
        if ratio > 0.01:
            calibration_fail.append(f'{name}:unstable_pixels change_ratio={ratio:.4f}')
            continue

        contrast = _contrast_score(b)
        if contrast < 20.0:
            calibration_fail.append(f'{name}:low_contrast var={contrast:.1f}')
            continue

    if calibration_fail:
        print('DECISION: REAL-MODE NO APTO')
        print('CLASSIFICATION: CALIBRATION_FAIL')
        for item in calibration_fail:
            print(f'FAIL: {item}')
        return 2

    suspicious: list[str] = []
    ip = idle_pairs[0]
    if ip.before.full is not None and ip.after.full is not None:
        full_ratio = _byte_change_ratio(ip.before.full.rgb, ip.after.full.rgb)
        if full_ratio > 0.02:
            suspicious.append(f'idle_full_frame_changed ratio={full_ratio:.4f}')

    if suspicious:
        print('DECISION: REAL-MODE NO APTO')
        print('CLASSIFICATION: POSSIBLE_INTERFERENCE_CLIENT')
        for s in suspicious:
            print(f'EVIDENCE: {s}')
        return 2

    print('DECISION: REAL-MODE APTO CON CONDICIONES')
    print('CONDITIONS: run each gate and validate semantic evidence per gate using dumped BEFORE/AFTER pairs')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
