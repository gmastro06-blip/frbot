from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_ppm_p6(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError(f"{path}: not a P6 PPM")

    i = data.find(b"\n") + 1

    def next_token() -> bytes:
        nonlocal i
        while i < len(data) and data[i : i + 1].isspace():
            i += 1
        if i < len(data) and data[i : i + 1] == b"#":
            i = data.find(b"\n", i) + 1
            return next_token()
        j = i
        while j < len(data) and not data[j : j + 1].isspace():
            j += 1
        tok = data[i:j]
        i = j
        return tok

    w = int(next_token())
    h = int(next_token())
    maxv = int(next_token())
    if maxv != 255:
        raise ValueError(f"{path}: expected maxval=255, got {maxv}")

    while i < len(data) and data[i : i + 1].isspace():
        i += 1

    pix = data[i:]
    expected = w * h * 3
    if len(pix) != expected:
        raise ValueError(f"{path}: pixel data len={len(pix)} expected={expected}")

    return w, h, pix


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute a sampled pixel-diff score between two PPM P6 frames.")
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--sample-step", type=int, default=20, help="Sample every Nth pixel (larger=faster).")
    ap.add_argument("--threshold", type=int, default=15, help="Sum(abs(dr)+abs(dg)+abs(db)) to count as changed.")
    args = ap.parse_args()

    w1, h1, p1 = _read_ppm_p6(args.before)
    w2, h2, p2 = _read_ppm_p6(args.after)
    if (w1, h1) != (w2, h2):
        raise SystemExit(f"size mismatch: {(w1, h1)} vs {(w2, h2)}")

    step = max(1, int(args.sample_step))
    thr = max(0, int(args.threshold))

    sampled = 0
    changed = 0
    min_x: int | None = None
    min_y: int | None = None
    max_x: int | None = None
    max_y: int | None = None
    sum_x = 0
    sum_y = 0
    for idx in range(0, w1 * h1, step):
        o = idx * 3
        dr = abs(p1[o] - p2[o])
        dg = abs(p1[o + 1] - p2[o + 1])
        db = abs(p1[o + 2] - p2[o + 2])
        if (dr + dg + db) > thr:
            changed += 1
            x = idx % w1
            y = idx // w1
            sum_x += x
            sum_y += y
            min_x = x if min_x is None else min(min_x, x)
            min_y = y if min_y is None else min(min_y, y)
            max_x = x if max_x is None else max(max_x, x)
            max_y = y if max_y is None else max(max_y, y)
        sampled += 1

    out = {
        "before": str(args.before.as_posix()),
        "after": str(args.after.as_posix()),
        "w": w1,
        "h": h1,
        "sample_step": step,
        "threshold": thr,
        "sampled": sampled,
        "changed": changed,
        "changed_pct": (changed / sampled) if sampled else 0.0,
        "changed_bbox": None
        if changed == 0
        else {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
        "changed_centroid": None
        if changed == 0
        else {"x": float(sum_x / changed), "y": float(sum_y / changed)},
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
