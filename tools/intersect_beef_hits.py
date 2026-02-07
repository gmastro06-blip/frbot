from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root is first on sys.path.
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


def scan_hits(
    ppm_path: Path,
    *,
    region: tuple[int, int, int, int] | None,
    cap_max: int | None,
    gold_max: int | None,
) -> dict[tuple[int, int], Hit]:
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
            if cap_max is not None and int(cap) > int(cap_max):
                continue
            if gold_max is not None and int(gold) > int(gold_max):
                continue
            out[(int(xx), int(yy))] = Hit(x=int(xx), y=int(yy), gold=int(gold), cap_used=int(cap), raw6_hex=rgb[i : i + 6].hex())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description='Intersect 0xBEEF hit coordinates across multiple PPM frames.')
    parser.add_argument('paths', nargs='+', help='PPM frames to scan')
    parser.add_argument('--region', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'))
    parser.add_argument('--cap-max', type=int, default=None, help='Filter: require cap_used <= cap-max')
    parser.add_argument('--gold-max', type=int, default=None, help='Filter: require gold <= gold-max')
    args = parser.parse_args()

    region = tuple(args.region) if args.region is not None else None

    scanned: list[dict[tuple[int, int], Hit]] = []
    for raw in args.paths:
        p = Path(raw)
        if not p.exists():
            print(f'{raw}: not found')
            return 2
        hits = scan_hits(p, region=region, cap_max=args.cap_max, gold_max=args.gold_max)
        print(f'{raw}: hits={len(hits)}')
        scanned.append(hits)

    if not scanned:
        return 0

    common = set(scanned[0].keys())
    for d in scanned[1:]:
        common &= set(d.keys())

    print(f'\ncommon_coords={len(common)}')
    for (x, y) in sorted(common)[:50]:
        samples = []
        for d in scanned:
            h = d[(x, y)]
            samples.append(f'gold={h.gold} cap={h.cap_used} raw6={h.raw6_hex}')
        print(f'  x={x} y={y} | ' + ' | '.join(samples))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
