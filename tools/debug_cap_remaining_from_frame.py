from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_root_on_syspath()

from contracts.capture import Frame
from contracts.evidence import Roi
from diagnostics.ppm import read_ppm
from runtime import inventory_semantics as inv


def _frame(path: Path) -> Frame:
    img = read_ppm(path)
    return Frame(width=int(img.width), height=int(img.height), monotonic_ts_ns=0, digest_hex="", rgb=bytes(img.rgb))


def _cap_remaining(fr: Frame, roi: Roi) -> int | None:
    templates = inv._load_digit_templates(Path(inv._visual_templates_path()))
    if not templates:
        return None

    h = int(roi.height)
    mid = h // 2
    v = inv._read_line_number_from_roi(fr, roi, y0=mid, y1=h, templates=templates)
    if v is None:
        v = inv._read_line_number_from_roi(fr, roi, y0=0, y1=h, templates=templates)
    return None if v is None else int(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ppm", required=True)
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--w", type=int, required=True)
    ap.add_argument("--h", type=int, required=True)
    args = ap.parse_args()

    fr = _frame(Path(args.ppm))
    roi = Roi(name="inventory_text", x=int(args.x), y=int(args.y), width=int(args.w), height=int(args.h))
    v = _cap_remaining(fr, roi)
    print({"ppm": args.ppm, "roi": {"x": args.x, "y": args.y, "w": args.w, "h": args.h}, "cap_remaining": v})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
