from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root is first on sys.path (avoid collisions with any installed `diagnostics` module).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diagnostics.ppm import read_ppm


@dataclass(frozen=True, slots=True)
class Hit:
    x: int
    y: int
    gold: int
    cap_used: int
    raw6_hex: str


def _scan_hits(ppm_path: Path, *, region: tuple[int, int, int, int] | None) -> dict[tuple[int, int], Hit]:
    img = read_ppm(ppm_path)
    rgb = img.rgb
    w = int(img.width)
    h = int(img.height)

    if region is None:
        x0, y0, rw, rh = 0, 0, w, h
    else:
        x0, y0, rw, rh = region
        x0 = max(0, min(int(x0), w - 1))
        y0 = max(0, min(int(y0), h - 1))
        rw = max(1, min(int(rw), w - x0))
        rh = max(1, min(int(rh), h - y0))

    out: dict[tuple[int, int], Hit] = {}
    row_stride = w * 3

    # Pixel-aligned scan: magic is 0xBEEF little-endian => bytes EF BE at pixel start.
    # NOTE: In REAL captures, EF BE can occur naturally in RGB pixels; use filters to
    # reduce false positives.
    default_cap_max = 50000
    try:
        cap_max = int((os.environ.get('FRBOT_INVENTORY_BINARY_CAP_MAX', str(default_cap_max)) or str(default_cap_max)).strip() or str(default_cap_max))
    except Exception:
        cap_max = int(default_cap_max)
    cap_max = max(1, min(int(cap_max), 65535))
    for yy in range(y0, y0 + rh):
        row_base = yy * row_stride
        for xx in range(x0, x0 + rw):
            i = row_base + (xx * 3)
            if i + 5 >= len(rgb):
                break
            if rgb[i] != 0xEF or rgb[i + 1] != 0xBE:
                continue
            gold = int.from_bytes(rgb[i + 2 : i + 4], 'little', signed=False)
            cap = int.from_bytes(rgb[i + 4 : i + 6], 'little', signed=False)
            if int(cap) > int(cap_max):
                continue
            out[(int(xx), int(yy))] = Hit(x=int(xx), y=int(yy), gold=int(gold), cap_used=int(cap), raw6_hex=rgb[i : i + 6].hex())

    return out


def _write_inventory_roi(json_path: Path, *, x: int, y: int) -> None:
    obj = json.loads(json_path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('config root must be an object')

    rois = obj.get('rois')
    if not isinstance(rois, dict):
        raise ValueError('config missing rois object')

    inv = rois.get('inventory_text')
    if not isinstance(inv, dict):
        inv = {}
        rois['inventory_text'] = inv

    inv['x'] = int(x)
    inv['y'] = int(y)
    inv['width'] = 2
    inv['height'] = 1

    json_path.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main() -> int:
    p = argparse.ArgumentParser(description='Calibrate inventory_text ROI for binary 0xBEEF encoding by intersecting hits across frames.')
    p.add_argument('frames', nargs='+', help='PPM frames to scan (use multiple for stability)')
    p.add_argument('--region', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'), default=None)
    p.add_argument(
        '--cap-max',
        type=int,
        default=None,
        help='Override FRBOT_INVENTORY_BINARY_CAP_MAX (filter: require cap_used <= cap-max).',
    )
    p.add_argument(
        '--write',
        nargs='*',
        default=[
            'rois_prod_emergency_combat_basic.json',
            'rois_prod_emergency_looting_basic.json',
        ],
        help='ROI json file(s) to update (defaults to prod_emergency combat_basic + looting_basic configs)',
    )
    args = p.parse_args()

    region = tuple(args.region) if args.region is not None else None

    if args.cap_max is not None:
        # Allow tool-local override without affecting the caller's shell.
        os.environ['FRBOT_INVENTORY_BINARY_CAP_MAX'] = str(int(args.cap_max))

    scanned: list[dict[tuple[int, int], Hit]] = []
    for raw in args.frames:
        path = Path(raw)
        if not path.exists():
            print(f'{raw}: not found')
            return 2
        hits = _scan_hits(path, region=region)
        print(f'{raw}: hits={len(hits)}')
        scanned.append(hits)

    if not scanned:
        print('no frames provided')
        return 2

    common = set(scanned[0].keys())
    for d in scanned[1:]:
        common &= set(d.keys())

    if not common:
        print('\ncommon_coords=0 (no stable 0xBEEF coordinates across the provided frames)')
        return 3

    # Pick a deterministic candidate.
    x, y = sorted(common)[0]
    samples = [d[(x, y)] for d in scanned]

    print(f'\ncommon_coords={len(common)}; selected=(x={x}, y={y})')
    for i, h in enumerate(samples):
        print(f'  sample[{i}]: gold={h.gold} cap_used={h.cap_used} raw6={h.raw6_hex}')

    for raw_cfg in args.write:
        cfg = Path(raw_cfg)
        if not cfg.is_absolute():
            cfg = REPO_ROOT / cfg
        if not cfg.exists():
            print(f'WARN: {cfg} not found; skipping')
            continue
        _write_inventory_roi(cfg, x=int(x), y=int(y))
        print(f'updated: {cfg}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
