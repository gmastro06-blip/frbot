from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_root_on_syspath()

from diagnostics.frame_dump import dump_frame_ppm
from diagnostics.ppm import read_ppm
from contracts.capture import Frame


def main() -> int:
    p = argparse.ArgumentParser(description="Create a visual diff overlay from two full-frame PPMs.")
    p.add_argument("--before", required=True, help="Before full-frame PPM (P6)")
    p.add_argument("--after", required=True, help="After full-frame PPM (P6)")
    p.add_argument("--out", default="diagnostics/diff_overlays", help="Output directory")
    p.add_argument("--thr", type=int, default=15, help="Per-channel abs diff threshold to mark a pixel as changed")
    p.add_argument("--alpha", type=float, default=0.7, help="Overlay strength (0..1). Higher = redder")

    args = p.parse_args()

    before_p = Path(args.before)
    after_p = Path(args.after)
    out_dir = Path(args.out)

    if not before_p.exists():
        raise SystemExit(f"before not found: {before_p}")
    if not after_p.exists():
        raise SystemExit(f"after not found: {after_p}")

    b = read_ppm(before_p)
    a = read_ppm(after_p)

    if b.width != a.width or b.height != a.height:
        raise SystemExit(f"dimension mismatch: before={b.width}x{b.height} after={a.width}x{a.height}")

    W, H = int(b.width), int(b.height)
    thr = int(args.thr)
    if thr < 0:
        thr = 0
    alpha = float(args.alpha)
    if alpha < 0.0:
        alpha = 0.0
    if alpha > 1.0:
        alpha = 1.0

    out = bytearray(b.rgb)

    changed = 0
    minx = 10**9
    miny = 10**9
    maxx = -1
    maxy = -1

    for pi in range(W * H):
        i = pi * 3
        dr = abs(int(b.rgb[i + 0]) - int(a.rgb[i + 0]))
        dg = abs(int(b.rgb[i + 1]) - int(a.rgb[i + 1]))
        db = abs(int(b.rgb[i + 2]) - int(a.rgb[i + 2]))
        if dr > thr or dg > thr or db > thr:
            x = int(pi % W)
            y = int(pi // W)
            changed += 1
            if x < minx:
                minx = x
            if y < miny:
                miny = y
            if x > maxx:
                maxx = x
            if y > maxy:
                maxy = y

            # Blend toward red.
            r0 = int(out[i + 0])
            g0 = int(out[i + 1])
            b0 = int(out[i + 2])
            out[i + 0] = int((1.0 - alpha) * r0 + alpha * 255)
            out[i + 1] = int((1.0 - alpha) * g0)
            out[i + 2] = int((1.0 - alpha) * b0)

    out_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{before_p.stem}__DIFF_thr{thr}_a{alpha:.2f}"
    overlay_path = out_dir / f"{stem}.ppm"
    dump_frame_ppm(Frame(width=W, height=H, monotonic_ts_ns=0, digest_hex="", rgb=bytes(out)), overlay_path)

    bbox = None if changed == 0 else (int(minx), int(miny), int(maxx), int(maxy))
    result = {
        "ok": True,
        "w": W,
        "h": H,
        "changed_pixels": int(changed),
        "changed_ratio": float(changed) / float(W * H),
        "bbox": bbox,
        "overlay": str(overlay_path),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
