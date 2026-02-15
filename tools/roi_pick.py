from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# When executed as a script (python tools/roi_pick.py), sys.path[0] is tools/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from diagnostics.ppm import read_ppm


def _is_truthy_env(name: str, default: str = "1") -> bool:
    v = os.environ.get(name, default)
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _quick_luma_stats(im: Any) -> tuple[float, float]:
    """Return (mean, std) luma for a small downsample.

    Uses Pillow Image if available.
    """

    try:
        from PIL import ImageStat  # type: ignore

        gray = im.convert("L")
        # Small downsample for speed.
        gray = gray.resize((max(1, gray.size[0] // 8), max(1, gray.size[1] // 8)))
        st = ImageStat.Stat(gray)
        mean = float(st.mean[0]) if st.mean else 0.0
        std = float(st.stddev[0]) if st.stddev else 0.0
        return mean, std
    except Exception:
        return 0.0, 0.0


def _enhance_for_visibility(im: Any) -> tuple[Any, dict[str, object]]:
    """Enhance very dark frames for human ROI selection.

    This does NOT affect ROI coordinates; it's purely for display.
    Controlled by env vars:
      - FRBOT_ROI_PICK_ENHANCE=0 to disable
      - FRBOT_ROI_PICK_BRIGHTNESS (float, default 1.8 when needed)
      - FRBOT_ROI_PICK_GAMMA (float, default 0.65 when needed)
      - FRBOT_ROI_PICK_AUTOCONTRAST=0 to disable autocontrast
    """

    meta: dict[str, object] = {"enhanced": False}
    if not _is_truthy_env("FRBOT_ROI_PICK_ENHANCE", "1"):
        return im, meta

    meta["enhanced"] = True

    try:
        from PIL import ImageEnhance  # type: ignore
        from PIL import ImageOps  # type: ignore

        if _is_truthy_env("FRBOT_ROI_PICK_AUTOCONTRAST", "1"):
            # Always autocontrast a bit; real captures can be almost-black but non-zero.
            im = ImageOps.autocontrast(im, cutoff=1)
            meta["autocontrast"] = True
        else:
            meta["autocontrast"] = False

        mean, std = _quick_luma_stats(im)
        meta["luma_mean"] = mean
        meta["luma_std"] = std

        # If still extremely dark, apply gamma + brightness.
        if mean <= 18.0 and std <= 25.0:
            try:
                gamma = float(os.environ.get("FRBOT_ROI_PICK_GAMMA", "0.65") or "0.65")
            except Exception:
                gamma = 0.65
            gamma = max(0.1, min(gamma, 5.0))
            inv = 1.0 / gamma
            lut = [min(255, int(((i / 255.0) ** inv) * 255.0 + 0.5)) for i in range(256)]
            im = im.point(lut * 3)
            meta["gamma"] = gamma

            try:
                brightness = float(os.environ.get("FRBOT_ROI_PICK_BRIGHTNESS", "1.8") or "1.8")
            except Exception:
                brightness = 1.8
            brightness = max(0.5, min(brightness, 6.0))
            im = ImageEnhance.Brightness(im).enhance(brightness)
            meta["brightness"] = brightness
        else:
            meta["gamma"] = 1.0
            meta["brightness"] = 1.0

        return im, meta
    except Exception as exc:
        meta["enhance_error"] = f"{type(exc).__name__}: {exc}"
        return im, meta


@dataclass(slots=True)
class _Selection:
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0

    def normalized(self) -> tuple[int, int, int, int]:
        left = min(self.x0, self.x1)
        top = min(self.y0, self.y1)
        right = max(self.x0, self.x1)
        bottom = max(self.y0, self.y1)
        return left, top, right, bottom


def _hard_stop(reason: str, *, details: dict[str, Any] | None = None, exit_code: int = 2) -> int:
    payload = {
        "reason": str(reason),
        "details": (details or {}),
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return int(exit_code)


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except Exception as exc:
        return None, f"json_read_failed:{path.name}:{type(exc).__name__}"


def _write_rois_config(config_path: Path, *, name: str, roi: dict[str, int]) -> tuple[bool, str | None]:
    data, err = _read_json(config_path)
    if err is not None:
        return False, err

    if not isinstance(data, dict):
        return False, "invalid_config_schema:top_level_must_be_object"

    keys = set(data.keys())
    if keys == {"rois"}:
        out: dict[str, Any] = {"rois": data.get("rois")}
    elif keys == {"rois", "frame"}:
        # Preserve optional frame resolution metadata used by OBS-source capture.
        out = {"frame": data.get("frame"), "rois": data.get("rois")}
    else:
        return False, "invalid_config_schema:top_level_must_be_only_rois_or_frame_plus_rois"

    rois_node = out.get("rois")
    if not isinstance(rois_node, dict):
        return False, "invalid_config_schema:rois_must_be_object"

    rois_node[str(name)] = {
        "x": int(roi["x"]),
        "y": int(roi["y"]),
        "width": int(roi["width"]),
        "height": int(roi["height"]),
    }

    config_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True, None


def _nearest_downscale_rgb(rgb: bytes, *, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    if dst_w <= 0 or dst_h <= 0:
        return b""
    if dst_w == src_w and dst_h == src_h:
        return rgb

    out = bytearray(dst_w * dst_h * 3)
    src_row_stride = src_w * 3
    dst_row_stride = dst_w * 3

    # Nearest neighbor sampling.
    for dy in range(dst_h):
        sy = int(dy * src_h / dst_h)
        sy = min(max(sy, 0), src_h - 1)
        src_row = sy * src_row_stride
        dst_row = dy * dst_row_stride
        for dx in range(dst_w):
            sx = int(dx * src_w / dst_w)
            sx = min(max(sx, 0), src_w - 1)
            sidx = src_row + (sx * 3)
            didx = dst_row + (dx * 3)
            out[didx : didx + 3] = rgb[sidx : sidx + 3]

    return bytes(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Interactive ROI picker for real .ppm frames")
    ap.add_argument("--image", required=True, help="Path to a frame image (.ppm recommended; PNG/JPG supported if Pillow installed)")
    ap.add_argument("--name", required=True, help="ROI name to create/update (e.g. hp_text)")
    ap.add_argument(
        "--config",
        default="",
        help="Optional path to rois config JSON to update (writes top-level {'rois': {...}})",
    )
    ap.add_argument(
        "--max-width",
        type=int,
        default=1200,
        help="Downscale for display if frame wider than this (keeps aspect ratio)",
    )
    ap.add_argument(
        "--max-height",
        type=int,
        default=900,
        help="Downscale for display if frame taller than this (keeps aspect ratio)",
    )

    args = ap.parse_args(argv)

    img_path = Path(str(args.image)).expanduser()
    if not img_path.exists():
        return _hard_stop("image_not_found", details={"image": str(img_path)})

    name = str(args.name).strip()
    if not name:
        return _hard_stop("invalid_roi_name")

    config_path: Path | None = None
    if str(args.config).strip():
        config_path = Path(str(args.config)).expanduser()
        if not config_path.exists():
            return _hard_stop("config_not_found", details={"config": str(config_path)})

    # Prefer Pillow for opening images (supports PNG/JPG/PPM). Fallback to our PPM parser.
    pil_ok = False
    pil_err: str | None = None
    Image: Any | None = None
    ImageTk: Any | None = None
    try:
        from PIL import Image as _PILImage  # type: ignore
        from PIL import ImageTk as _PILImageTk  # type: ignore

        Image = _PILImage
        ImageTk = _PILImageTk
        pil_ok = True
    except Exception as exc:
        pil_ok = False
        pil_err = f"{type(exc).__name__}: {exc}"

    src_w = 0
    src_h = 0
    rgb_src: bytes = b""

    if pil_ok:
        try:
            im_src = Image.open(str(img_path))  # type: ignore[union-attr]
            im_src = im_src.convert("RGB")
            src_w, src_h = map(int, im_src.size)
            rgb_src = im_src.tobytes()
        except Exception as exc:
            return _hard_stop(
                "image_load_failed",
                details={"image": str(img_path), "error": f"{type(exc).__name__}: {exc}", "pillow_error": (pil_err or "")},
            )
    else:
        if img_path.suffix.lower() != ".ppm":
            return _hard_stop(
                "image_unsupported_without_pillow",
                details={"image": str(img_path), "pillow_available": False, "pillow_error": (pil_err or "")},
            )
        ppm = read_ppm(img_path)
        src_w = int(ppm.width)
        src_h = int(ppm.height)
        rgb_src = ppm.rgb

    max_w = max(1, int(args.max_width))
    max_h = max(1, int(args.max_height))

    scale = 1.0
    dst_w = src_w
    dst_h = src_h

    if dst_w > max_w:
        scale = max_w / float(dst_w)
        dst_w = max_w
        dst_h = max(1, int(round(src_h * scale)))

    if dst_h > max_h:
        scale2 = max_h / float(dst_h)
        scale = scale * scale2
        dst_h = max_h
        dst_w = max(1, int(round(src_w * scale)))

    rgb_disp = _nearest_downscale_rgb(rgb_src, src_w=src_w, src_h=src_h, dst_w=dst_w, dst_h=dst_h)

    try:
        try:
            import tkinter as tk
        except Exception as exc:
            return _hard_stop("tk_unavailable", details={"error": type(exc).__name__})

        # Prefer Pillow for robust image display (Tk's native PPM loader can be unreliable).
        # We may already have Pillow loaded above.

        root = tk.Tk()
        root.title(f"ROI Pick: {name} ({dst_w}x{dst_h})")

        # Canvas with image.
        tmp_ppm: Path | None = None

        if pil_ok:
            im = Image.frombytes("RGB", (dst_w, dst_h), rgb_disp)  # type: ignore[union-attr]
            im, meta = _enhance_for_visibility(im)
            img = ImageTk.PhotoImage(im)  # type: ignore[union-attr]
            # Helpful hint in the status bar.
            if meta.get("enhanced"):
                status_hint = " (enhanced)"
            else:
                status_hint = ""
        else:
            # Fallback: write a temp PPM and ask Tk to load it.
            header = f"P6\n{dst_w} {dst_h}\n255\n".encode("ascii")
            tmp_dir = Path(tempfile.gettempdir())
            tmp_ppm = tmp_dir / f"frbot_roi_pick_{os.getpid()}.ppm"
            tmp_ppm.write_bytes(header + rgb_disp)
            try:
                img = tk.PhotoImage(file=str(tmp_ppm))
            except Exception as exc:
                return _hard_stop(
                    'image_load_failed',
                    details={
                        'error': f'{type(exc).__name__}: {exc}',
                        'pillow_available': False,
                        'pillow_error': pil_err,
                        'note': 'Install/enable Pillow (PIL) to make ROI picker reliable.',
                    },
                )

        canvas = tk.Canvas(root, width=dst_w, height=dst_h)
        canvas.pack()
        canvas.create_image(0, 0, image=img, anchor="nw")
        # Prevent Tk image GC (can appear as blank/black canvas on some setups).
        canvas.image = img  # type: ignore[attr-defined]
        root._roi_pick_img = img  # type: ignore[attr-defined]

        status = tk.StringVar(value=f"Drag to select. Enter=accept, Esc=cancel{status_hint if 'status_hint' in locals() else ''}")
        label = tk.Label(root, textvariable=status, anchor="w")
        label.pack(fill="x")

        sel = _Selection()
        rect_id: int | None = None
        accepted = {"ok": False}

        def _update_status() -> None:
            left, top, right, bottom = sel.normalized()
            w = max(0, right - left)
            h = max(0, bottom - top)
            status.set(f"sel: x={left} y={top} w={w} h={h} | Enter=accept Esc=cancel")

        def on_down(event: tk.Event) -> None:  # type: ignore[name-defined]
            nonlocal rect_id
            sel.x0 = int(event.x)
            sel.y0 = int(event.y)
            sel.x1 = int(event.x)
            sel.y1 = int(event.y)
            if rect_id is not None:
                canvas.delete(rect_id)
            rect_id = canvas.create_rectangle(sel.x0, sel.y0, sel.x1, sel.y1, outline="red", width=2)
            _update_status()

        def on_move(event: tk.Event) -> None:  # type: ignore[name-defined]
            nonlocal rect_id
            sel.x1 = int(event.x)
            sel.y1 = int(event.y)
            if rect_id is not None:
                canvas.coords(rect_id, sel.x0, sel.y0, sel.x1, sel.y1)
            _update_status()

        def on_up(event: tk.Event) -> None:  # type: ignore[name-defined]
            sel.x1 = int(event.x)
            sel.y1 = int(event.y)
            _update_status()

        def on_accept(_event: tk.Event | None = None) -> None:  # type: ignore[name-defined]
            accepted["ok"] = True
            root.quit()

        def on_cancel(_event: tk.Event | None = None) -> None:  # type: ignore[name-defined]
            accepted["ok"] = False
            root.quit()

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_up)
        root.bind("<Return>", on_accept)
        root.bind("<Escape>", on_cancel)

        root.mainloop()
        root.destroy()

        if not accepted["ok"]:
            return _hard_stop("roi_pick_cancelled", details={"name": name, "image": str(img_path)})

        left, top, right, bottom = sel.normalized()
        disp_w = max(1, right - left)
        disp_h = max(1, bottom - top)

        # Map to source coords.
        scale_x = src_w / float(dst_w)
        scale_y = src_h / float(dst_h)
        x = int(left * scale_x)
        y = int(top * scale_y)
        w = int(disp_w * scale_x)
        h = int(disp_h * scale_y)

        # Clamp.
        x = max(0, min(x, src_w - 1))
        y = max(0, min(y, src_h - 1))
        w = max(1, min(w, src_w - x))
        h = max(1, min(h, src_h - y))

        roi = {"x": x, "y": y, "width": w, "height": h}

        updated = False
        update_error: str | None = None
        if config_path is not None:
            updated, update_error = _write_rois_config(config_path, name=name, roi=roi)

        payload = {
            "reason": "roi_picked",
            "name": name,
            "roi": roi,
            "image": str(img_path),
            "config_path": (str(config_path) if config_path is not None else ""),
            "updated": bool(updated),
            "update_error": (update_error or ""),
            "display": {
                "width": dst_w,
                "height": dst_h,
                "source_width": src_w,
                "source_height": src_h,
            },
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")

        if config_path is not None and not updated:
            return 2
        return 0

    finally:
        try:
            if isinstance(locals().get("tmp_ppm"), Path) and locals()["tmp_ppm"].exists():
                locals()["tmp_ppm"].unlink()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
