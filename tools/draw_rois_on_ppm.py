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

from diagnostics.ppm import read_ppm
from diagnostics.frame_dump import dump_frame_ppm
from contracts.capture import Frame


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _set_px(img: bytearray, *, w: int, h: int, x: int, y: int, r: int, g: int, b: int) -> None:
    if x < 0 or y < 0 or x >= w or y >= h:
        return
    i = (y * w + x) * 3
    if i < 0 or i + 2 >= len(img):
        return
    img[i + 0] = _clamp(int(r), 0, 255)
    img[i + 1] = _clamp(int(g), 0, 255)
    img[i + 2] = _clamp(int(b), 0, 255)


def _draw_rect(img: bytearray, *, w: int, h: int, x: int, y: int, rw: int, rh: int, color: tuple[int, int, int]) -> None:
    if rw <= 0 or rh <= 0:
        return
    r, g, b = color
    x0 = _clamp(int(x), 0, w - 1)
    y0 = _clamp(int(y), 0, h - 1)
    x1 = _clamp(int(x + rw - 1), 0, w - 1)
    y1 = _clamp(int(y + rh - 1), 0, h - 1)

    for xx in range(x0, x1 + 1):
        _set_px(img, w=w, h=h, x=xx, y=y0, r=r, g=g, b=b)
        _set_px(img, w=w, h=h, x=xx, y=y1, r=r, g=g, b=b)
    for yy in range(y0, y1 + 1):
        _set_px(img, w=w, h=h, x=x0, y=yy, r=r, g=g, b=b)
        _set_px(img, w=w, h=h, x=x1, y=yy, r=r, g=g, b=b)


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
    ap = argparse.ArgumentParser(description="Draw ROI rectangles on a full-frame PPM for debugging.")
    ap.add_argument("--ppm", required=True, help="Input full-frame .ppm (P6)")
    ap.add_argument("--config", required=True, help="ROI json path (expects {rois:{...}})")
    ap.add_argument("--out", default="diagnostics/roi_overlays", help="Output directory")
    ap.add_argument("--roi", action="append", default=[], help="ROI name to draw (repeatable). If omitted, draws all.")

    args = ap.parse_args()

    ppm_path = Path(args.ppm)
    config_path = Path(args.config)
    out_dir = Path(args.out)

    if not ppm_path.exists():
        raise SystemExit(f"ppm not found: {ppm_path}")
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")

    img = read_ppm(ppm_path)
    rois = _load_rois(config_path)

    want = [str(r).strip() for r in (args.roi or []) if str(r).strip()]
    names = want if want else sorted(rois.keys())

    out = bytearray(img.rgb)

    palette = [
        (0, 255, 0),
        (255, 0, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
        (255, 128, 0),
        (128, 255, 0),
    ]

    drawn = 0
    for idx, name in enumerate(names):
        r = rois.get(name)
        if not r:
            continue
        color = palette[idx % len(palette)]
        _draw_rect(
            out,
            w=int(img.width),
            h=int(img.height),
            x=int(r["x"]),
            y=int(r["y"]),
            rw=int(r["width"]),
            rh=int(r["height"]),
            color=color,
        )
        drawn += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ppm_path.stem}__rois.ppm"
    dump_frame_ppm(Frame(width=int(img.width), height=int(img.height), monotonic_ts_ns=0, digest_hex="", rgb=bytes(out)), out_path)

    print(json.dumps({"ok": True, "drawn": int(drawn), "out": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
