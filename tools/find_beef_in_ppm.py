from __future__ import annotations

import argparse
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


def find_beef_hits(
    ppm_path: Path,
    *,
    limit: int,
    region: tuple[int, int, int, int] | None = None,
    cap_max: int | None = None,
    gold_max: int | None = None,
) -> list[Hit]:
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

    hits: list[Hit] = []
    # Scan by pixels (step 3 bytes). Magic is little-endian 0xBEEF => bytes EF BE.
    row_stride = w * 3
    for yy in range(y0, y0 + rh):
        row_base = yy * row_stride
        for xx in range(x0, x0 + rw):
            i = row_base + (xx * 3)
            if i + 5 >= len(rgb):
                break
            if rgb[i] != 0xEF or rgb[i + 1] != 0xBE:
                continue
            gold = int.from_bytes(rgb[i + 2 : i + 4], "little", signed=False)
            cap = int.from_bytes(rgb[i + 4 : i + 6], "little", signed=False)
            if cap_max is not None and int(cap) > int(cap_max):
                continue
            if gold_max is not None and int(gold) > int(gold_max):
                continue
            hits.append(Hit(x=int(xx), y=int(yy), gold=int(gold), cap_used=int(cap), raw6_hex=rgb[i : i + 6].hex()))
            if len(hits) >= int(limit):
                return hits
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Find prod-emergency inventory magic 0xBEEF in PPM frames.")
    parser.add_argument("paths", nargs="+", help="PPM file(s) to scan")
    parser.add_argument("--limit", type=int, default=20, help="Max hits per file")
    parser.add_argument("--cap-max", type=int, default=None, help="Filter: require cap_used <= cap-max")
    parser.add_argument("--gold-max", type=int, default=None, help="Filter: require gold <= gold-max")
    parser.add_argument(
        "--region",
        nargs=4,
        type=int,
        metavar=("X", "Y", "W", "H"),
        help="Optional scan region within the frame (x y w h)",
    )
    args = parser.parse_args()

    region = None
    if args.region is not None:
        region = (int(args.region[0]), int(args.region[1]), int(args.region[2]), int(args.region[3]))

    for raw in args.paths:
        p = Path(raw)
        if not p.exists():
            print(f"{raw}: not found")
            continue
        try:
            hits = find_beef_hits(
                p,
                limit=int(args.limit),
                region=region,
                cap_max=args.cap_max,
                gold_max=args.gold_max,
            )
            img = read_ppm(p)
        except Exception as exc:
            print(f"{raw}: failed: {type(exc).__name__}: {exc}")
            continue

        print(f"\n{raw} size=({img.width},{img.height}) hits={len(hits)}")
        for h in hits:
            print(f"  x={h.x} y={h.y} gold={h.gold} cap_used={h.cap_used} raw6={h.raw6_hex}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
