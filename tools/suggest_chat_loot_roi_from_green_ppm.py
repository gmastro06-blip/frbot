from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_ppm_p6(path: Path) -> tuple[int, int, bytes]:
    b = path.read_bytes()
    if not b.startswith(b"P6"):
        raise ValueError("not a P6 PPM")

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


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def main() -> int:
    ap = argparse.ArgumentParser(description="Suggest chat_loot_area ROI by finding green-ish text pixels.")
    ap.add_argument("--ppm", required=True)
    ap.add_argument("--g-min", type=int, default=130)
    ap.add_argument("--delta", type=int, default=70, help="min (g-r) and (g-b)")
    ap.add_argument("--r-max", type=int, default=110)
    ap.add_argument("--b-max", type=int, default=110)
    ap.add_argument("--y-min", type=int, default=-1, help="only scan pixels with y >= y-min")
    ap.add_argument("--y-min-ratio", type=float, default=0.55, help="if y-min not set, use floor(h*y-min-ratio)")
    ap.add_argument("--pad", type=int, default=12)
    ap.add_argument("--min-pixels", type=int, default=800)
    ap.add_argument("--max-height", type=int, default=260)
    args = ap.parse_args()

    w, h, rgb = read_ppm_p6(Path(args.ppm))

    gmin = int(args.g_min)
    d = int(args.delta)
    rmax = int(args.r_max)
    bmax = int(args.b_max)

    if int(args.y_min) >= 0:
        y_min = int(args.y_min)
    else:
        try:
            y_min = int(h * float(args.y_min_ratio))
        except Exception:
            y_min = int(h * 0.55)
    y_min = clamp(int(y_min), 0, h - 1)

    minx = 10**9
    miny = 10**9
    maxx = -1
    maxy = -1
    hits = 0

    # Scan all pixels; flag green-ish text.
    # IMPORTANT: if y_min > 0, skip the preceding rows in the linear RGB buffer.
    idx = int(y_min) * int(w) * 3
    for yy in range(y_min, h):
        for xx in range(w):
            r = rgb[idx]
            g = rgb[idx + 1]
            b = rgb[idx + 2]
            idx += 3
            if r <= rmax and b <= bmax and g >= gmin and (g - r) >= d and (g - b) >= d:
                hits += 1
                if xx < minx:
                    minx = xx
                if yy < miny:
                    miny = yy
                if xx > maxx:
                    maxx = xx
                if yy > maxy:
                    maxy = yy

    out: dict[str, Any] = {
        "ok": True,
        "frame": {"w": w, "h": h},
        "params": {
            "g_min": gmin,
            "delta": d,
            "r_max": rmax,
            "b_max": bmax,
            "y_min": int(y_min),
            "pad": int(args.pad),
        },
        "green_hits": hits,
    }

    if hits < int(args.min_pixels) or maxx < minx or maxy < miny:
        out["suggested"] = None
        out["reason"] = "not_enough_green_pixels"
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    pad = int(args.pad)
    x0 = clamp(minx - pad, 0, w - 1)
    y0 = clamp(miny - pad, 0, h - 1)
    x1 = clamp(maxx + pad, 0, w - 1)
    y1 = clamp(maxy + pad, 0, h - 1)

    # Heuristic: chat window is usually near bottom; cap height.
    max_h = max(40, int(args.max_height))
    if (y1 - y0 + 1) > max_h:
        y0 = max(0, y1 - max_h + 1)

    out["suggested"] = {"x": int(x0), "y": int(y0), "w": int(x1 - x0 + 1), "h": int(y1 - y0 + 1)}
    out["bbox"] = [int(minx), int(miny), int(maxx), int(maxy)]

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
