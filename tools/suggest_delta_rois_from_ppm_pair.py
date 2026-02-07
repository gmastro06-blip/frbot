from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_ppm_p6(path: Path) -> tuple[int, int, bytes]:
    b = path.read_bytes()
    if not b.startswith(b"P6"):
        raise ValueError("not a P6 PPM")

    # Parse header, skipping comments.
    i = 0
    lines: list[bytes] = []
    while len(lines) < 3:
        j = b.find(b"\n", i)
        if j < 0:
            raise ValueError("invalid PPM header")
        line = b[i:j].strip()
        i = j + 1
        if not line or line.startswith(b"#"):
            continue
        lines.append(line)

    if lines[0] != b"P6":
        raise ValueError("not P6")

    w_str, h_str = lines[1].split()
    w = int(w_str)
    h = int(h_str)

    maxv = int(lines[2])
    if maxv != 255:
        raise ValueError("unsupported maxval")

    data = b[i:]
    expected = w * h * 3
    if len(data) != expected:
        raise ValueError(f"unexpected data length: got={len(data)} expected={expected}")
    return w, h, data


def _diff_mask(before: bytes, after: bytes, *, px_tol: int) -> list[int]:
    if len(before) != len(after):
        raise ValueError("size mismatch")

    mask = [0] * (len(before) // 3)
    idx = 0
    for i in range(0, len(before), 3):
        if (
            abs(before[i + 0] - after[i + 0]) > px_tol
            or abs(before[i + 1] - after[i + 1]) > px_tol
            or abs(before[i + 2] - after[i + 2]) > px_tol
        ):
            mask[idx] = 1
        idx += 1
    return mask


def _bbox_from_mask(mask: list[int], *, w: int, h: int) -> list[int] | None:
    minx = 10**9
    miny = 10**9
    maxx = -1
    maxy = -1
    for yy in range(h):
        row0 = yy * w
        for xx in range(w):
            if mask[row0 + xx]:
                if xx < minx:
                    minx = xx
                if yy < miny:
                    miny = yy
                if xx > maxx:
                    maxx = xx
                if yy > maxy:
                    maxy = yy
    if maxx < minx or maxy < miny:
        return None
    return [int(minx), int(miny), int(maxx), int(maxy)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Suggest ROI candidates based on pixel deltas between two PPM frames.")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--px-tol", type=int, default=15)
    ap.add_argument("--grid-x", type=int, default=16)
    ap.add_argument("--grid-y", type=int, default=9)
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    before_p = Path(args.before)
    after_p = Path(args.after)

    w1, h1, b = _read_ppm_p6(before_p)
    w2, h2, a = _read_ppm_p6(after_p)
    if (w1, h1) != (w2, h2):
        raise SystemExit("frame_size_mismatch")

    w, h = w1, h1
    mask = _diff_mask(b, a, px_tol=int(args.px_tol))
    bbox = _bbox_from_mask(mask, w=w, h=h)

    gx = max(1, int(args.grid_x))
    gy = max(1, int(args.grid_y))
    cell_w = max(1, w // gx)
    cell_h = max(1, h // gy)

    cells: list[dict[str, Any]] = []
    total = w * h
    changed_total = int(sum(mask))

    for cy in range(gy):
        for cx in range(gx):
            x0 = cx * cell_w
            y0 = cy * cell_h
            x1 = w if cx == gx - 1 else (x0 + cell_w)
            y1 = h if cy == gy - 1 else (y0 + cell_h)

            c = 0
            for yy in range(y0, y1):
                row0 = yy * w
                c += sum(mask[row0 + x0 : row0 + x1])

            area = (x1 - x0) * (y1 - y0)
            cells.append(
                {
                    "cell": [int(cx), int(cy)],
                    "roi": {"x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0)},
                    "changed_pixels": int(c),
                    "changed_ratio": float(c) / float(area) if area else 0.0,
                }
            )

    cells_sorted = sorted(cells, key=lambda d: (d["changed_pixels"], d["changed_ratio"]), reverse=True)

    payload = {
        "ok": True,
        "frame": {"w": int(w), "h": int(h)},
        "px_tol": int(args.px_tol),
        "changed_total": int(changed_total),
        "changed_ratio": float(changed_total) / float(total) if total else 0.0,
        "bbox": bbox,
        "grid": {"gx": int(gx), "gy": int(gy), "cell_w": int(cell_w), "cell_h": int(cell_h)},
        "top_cells": cells_sorted[: max(1, int(args.top))],
    }

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
