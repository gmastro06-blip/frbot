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

from contracts.capture import Frame
from diagnostics.frame_dump import dump_frame_ppm
from diagnostics.ppm import read_ppm


def _crop_rgb(*, rgb: bytes, frame_w: int, frame_h: int, x: int, y: int, w: int, h: int) -> bytes:
    if frame_w <= 0 or frame_h <= 0:
        return b""
    if w <= 0 or h <= 0:
        return b""
    if x < 0 or y < 0:
        return b""
    if (x + w) > frame_w or (y + h) > frame_h:
        return b""

    row_stride = frame_w * 3
    out = bytearray(w * h * 3)
    out_row_stride = w * 3

    dst = 0
    for yy in range(y, y + h):
        src0 = yy * row_stride + x * 3
        src1 = src0 + out_row_stride
        out[dst : dst + out_row_stride] = rgb[src0:src1]
        dst += out_row_stride

    return bytes(out)


def _load_rois(config_path: Path) -> dict[str, dict[str, int]]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    rois = data.get("rois", data)
    out: dict[str, dict[str, int]] = {}
    if not isinstance(rois, dict):
        return out
    for name, r in rois.items():
        if not isinstance(r, dict):
            continue
        try:
            out[str(name)] = {
                "x": int(r.get("x", 0)),
                "y": int(r.get("y", 0)),
                "width": int(r.get("width", 0)),
                "height": int(r.get("height", 0)),
            }
        except Exception:
            continue
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Dump ROI crops from a full-frame PPM.")
    p.add_argument("--ppm", required=True, help="Path to full-frame .ppm (P6)")
    p.add_argument("--config", required=True, help="ROI json path (expects {rois:{...}})")
    p.add_argument("--out-dir", default="diagnostics/roi_crops", help="Output directory")
    p.add_argument("--roi", action="append", default=[], help="ROI name to dump (repeatable). If omitted, dumps all.")

    args = p.parse_args()

    ppm_path = Path(args.ppm)
    config_path = Path(args.config)
    out_dir = Path(args.out_dir)

    if not ppm_path.exists():
        raise SystemExit(f"ppm not found: {ppm_path}")
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")

    img = read_ppm(ppm_path)
    rois = _load_rois(config_path)

    want = [str(r).strip() for r in (args.roi or []) if str(r).strip()]
    names = want if want else sorted(rois.keys())

    out_dir.mkdir(parents=True, exist_ok=True)

    dumped = 0
    for name in names:
        r = rois.get(name)
        if not r:
            continue
        x, y, w, h = int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"])
        crop = _crop_rgb(rgb=img.rgb, frame_w=int(img.width), frame_h=int(img.height), x=x, y=y, w=w, h=h)
        if not crop:
            continue
        f = Frame(width=int(w), height=int(h), monotonic_ts_ns=0, digest_hex="", rgb=crop)
        if dump_frame_ppm(f, out_dir / f"{ppm_path.stem}__{name}.ppm"):
            dumped += 1

    print(json.dumps({"ok": True, "dumped": int(dumped), "out_dir": str(out_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
