from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# When executed as a script (python tools/...), sys.path[0] is tools/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from adapters.capture.obs_source_real import ObsSourceRealCapture
from contracts.capture import Frame
from contracts.evidence import Roi
from diagnostics.frame_dump import dump_frame_ppm
from diagnostics.ppm import read_ppm
from runtime.battle_list_semantics import crop_roi_rgb


def _load_rois_config(path: Path) -> tuple[dict[str, Roi], int | None, int | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid json: {type(exc).__name__}: {exc}")

    if not isinstance(data, dict):
        raise ValueError("invalid schema")

    frame_w: int | None = None
    frame_h: int | None = None
    keys = set(data.keys())
    if keys == {"rois"}:
        pass
    elif keys == {"rois", "frame"}:
        frame = data.get("frame")
        if not isinstance(frame, dict):
            raise ValueError("invalid schema")
        frame_w = int(frame.get("width") or 0)
        frame_h = int(frame.get("height") or 0)
        if frame_w <= 0 or frame_h <= 0:
            raise ValueError("invalid schema")
    else:
        raise ValueError("invalid schema")

    rois_node = data.get("rois")
    if not isinstance(rois_node, dict):
        raise ValueError("invalid schema")

    rois: dict[str, Roi] = {}
    for name, r in rois_node.items():
        if not isinstance(name, str) or not isinstance(r, dict):
            raise ValueError("invalid schema")
        rois[str(name)] = Roi(
            name=str(name),
            x=int(r.get("x") or 0),
            y=int(r.get("y") or 0),
            width=int(r.get("width") or 0),
            height=int(r.get("height") or 0),
        )

    return rois, frame_w, frame_h


def _hard_stop(reason: str, *, details: dict[str, Any] | None = None, exit_code: int = 2) -> int:
    payload = {"ok": False, "reason": str(reason)}
    if details:
        payload["details"] = details
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return int(exit_code)


def _try_import_cv2_np() -> tuple[Any | None, Any | None]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        return cv2, np
    except Exception:
        return None, None


def _binarize_rgb(rgb: bytes, *, w: int, h: int) -> bytes | None:
    cv2, np = _try_import_cv2_np()
    if cv2 is None or np is None:
        return None
    try:
        img = np.frombuffer(rgb, dtype=np.uint8).reshape((int(h), int(w), 3))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Dynamic threshold selection (see runtime/inventory_semantics.py).
        gray = cv2.medianBlur(gray, 3)
        mean = float(gray.mean())
        std = float(gray.std())
        base_thr = int(mean + 1.0 * std)

        def clamp_thr(v: int) -> int:
            if v < 60:
                return 60
            if v > 200:
                return 200
            return int(v)

        candidates = [
            clamp_thr(base_thr),
            clamp_thr(base_thr - 10),
            clamp_thr(base_thr + 10),
            120,
            130,
            140,
            150,
            160,
        ]
        seen: set[int] = set()
        uniq: list[int] = []
        for t in candidates:
            tt = int(t)
            if tt not in seen:
                seen.add(tt)
                uniq.append(tt)

        min_area = max(4, int((w * h) * 0.002))
        # Bound the left cutoff so widening the ROI (e.g., to include decimals)
        # doesn't exclude valid digits.
        min_x = min(6, int(w * 0.20))
        min_h = max(6, int(h * 0.45))

        best_bw = None
        best_score = -1
        for thr in uniq:
            _thr, bw0 = cv2.threshold(gray, int(thr), 255, cv2.THRESH_BINARY)
            bw = cv2.medianBlur(bw0, 3)

            contours, _hier = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            score = 0
            for c in contours:
                x, y, ww, hh = cv2.boundingRect(c)
                if ww <= 0 or hh <= 0:
                    continue
                if (ww * hh) < min_area:
                    continue
                if x < min_x:
                    continue
                if hh < min_h:
                    continue
                if ww < 2:
                    continue
                if ww >= int(w * 0.75) or hh >= int(h * 0.95):
                    continue
                score += 1

            if score > best_score:
                best_score = int(score)
                best_bw = bw

        if best_bw is None:
            return None

        return bytes(best_bw.tobytes())
    except Exception:
        return None


def _extract_digit_glyphs(binary: bytes, *, w: int, h: int) -> list[bytes]:
    cv2, np = _try_import_cv2_np()
    if cv2 is None or np is None:
        return []

    try:
        bw = np.frombuffer(binary, dtype=np.uint8).reshape((int(h), int(w)))
        contours, _hier = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes: list[tuple[int, int, int, int]] = []
        min_area = max(4, int((w * h) * 0.002))
        for c in contours:
            x, y, ww, hh = cv2.boundingRect(c)
            if ww <= 0 or hh <= 0:
                continue
            if (ww * hh) < min_area:
                continue
            if x < min(6, int(w * 0.20)):
                continue
            if hh < max(6, int(h * 0.45)) or ww < 2:
                continue
            boxes.append((int(x), int(y), int(ww), int(hh)))

        boxes.sort(key=lambda b: b[0])
        glyphs: list[bytes] = []
        for x, y, ww, hh in boxes:
            crop = bw[y : y + hh, x : x + ww]
            canon_w, canon_h = 12, 16
            resized = cv2.resize(crop, (int(canon_w), int(canon_h)), interpolation=cv2.INTER_NEAREST)
            glyphs.append(resized.tobytes())
        return glyphs
    except Exception:
        return []


def _glyph_to_template(glyph: bytes) -> list[int]:
    return [1 if int(b) > 0 else 0 for b in glyph]


def _load_templates(path: Path) -> dict[str, list[list[int]]]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
        out: dict[str, list[list[int]]] = {}
        for k, v in obj.items():
            if str(k) not in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                continue
            if not isinstance(v, list):
                continue
            out[str(k)] = []
            for t in v:
                if isinstance(t, list):
                    out[str(k)].append([int(x) for x in t])
        return out
    except Exception:
        return {}


def _save_templates(path: Path, templates: dict[str, list[list[int]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(templates, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _merge_templates(dst: dict[str, list[list[int]]], new_items: dict[str, list[list[int]]]) -> dict[str, list[list[int]]]:
    out = {str(k): [list(x) for x in v] for k, v in dst.items()}
    for digit, items in new_items.items():
        d = str(digit)
        out.setdefault(d, [])
        for t in items:
            # avoid duplicates (exact match)
            if not any(existing == t for existing in out[d]):
                out[d].append(t)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Calibrate visual digit templates for inventory_text (Cap/Soul)")
    ap.add_argument("--config", default=os.environ.get("FRBOT_CONFIG_PATH", ""), help="Path to rois JSON")
    ap.add_argument("--roi", default=os.environ.get("FRBOT_INVENTORY_TEXT_ROI", "inventory_text"), help="ROI name")
    ap.add_argument("--obs-source", default=os.environ.get("FRBOT_OBS_SOURCE_NAME", ""), help="OBS source name")
    ap.add_argument(
        "--ppm",
        default="",
        help="Optional path to a cropped ROI PPM (P6). When set, OBS/config are not required and the PPM is used as the ROI crop.",
    )
    ap.add_argument(
        "--out",
        default=os.environ.get("FRBOT_INVENTORY_VISUAL_TEMPLATES", "diagnostics/inventory_digit_templates.json"),
        help="Output templates JSON path",
    )
    ap.add_argument("--soul", type=int, default=None, help="Expected Soul value visible in ROI (top line)")
    ap.add_argument("--cap", type=int, default=None, help="Expected Cap value visible in ROI (bottom line)")
    ap.add_argument("--dump-roi", default="diagnostics/inventory_text_roi.ppm", help="Dump the ROI crop to this PPM")

    args = ap.parse_args(argv)

    ppm_path = Path(str(args.ppm)).expanduser() if str(args.ppm or "").strip() else None
    if ppm_path is None:
        if not args.config:
            return _hard_stop("missing_precondition", details={"missing": "FRBOT_CONFIG_PATH or --config (or use --ppm)"})
        if not args.obs_source:
            return _hard_stop("missing_precondition", details={"missing": "FRBOT_OBS_SOURCE_NAME or --obs-source (or use --ppm)"})
    if args.soul is None and args.cap is None:
        return _hard_stop("missing_precondition", details={"missing": "--soul and/or --cap"})

    crop: bytes
    roi: Roi
    if ppm_path is not None:
        if not ppm_path.exists():
            return _hard_stop("ppm_not_found", details={"ppm": str(ppm_path)})
        try:
            img = read_ppm(ppm_path)
        except Exception as exc:
            return _hard_stop("ppm_unreadable", details={"ppm": str(ppm_path), "error": f"{type(exc).__name__}: {exc}"})

        crop = bytes(img.rgb)
        roi = Roi(name=str(args.roi), x=0, y=0, width=int(img.width), height=int(img.height))
    else:
        cfg_path = Path(str(args.config)).expanduser()
        if not cfg_path.exists():
            return _hard_stop("config_not_found", details={"config": str(cfg_path)})

        try:
            rois, frame_w, frame_h = _load_rois_config(cfg_path)
        except Exception as exc:
            return _hard_stop("config_invalid_schema", details={"error": str(exc)})

        roi_value = rois.get(str(args.roi))
        if roi_value is None:
            return _hard_stop("missing_required_roi", details={"roi": str(args.roi)})
        roi = roi_value

        if frame_w is None or frame_h is None:
            return _hard_stop("config_invalid_schema", details={"need": "frame_width/frame_height for obs_source"})

        cap = ObsSourceRealCapture(
            obs_source_name=str(args.obs_source),
            expected_width=int(frame_w),
            expected_height=int(frame_h),
            rois=rois,
            minimap_roi_name="minimap",
        )

        v = cap.verify()
        if not v.ok:
            return _hard_stop("capture_not_verified", details={"reason": v.reason or ""})

        frame = cap.grab()
        if not frame.rgb:
            return _hard_stop("capture_empty")

        crop = crop_roi_rgb(frame, roi)
        if not crop:
            return _hard_stop("roi_crop_failed")

    # Dump for inspection.
    try:
        dump_frame_ppm(Frame(width=int(roi.width), height=int(roi.height), monotonic_ts_ns=0, digest_hex="", rgb=crop), Path(str(args.dump_roi)))
    except Exception:
        pass

    bw = _binarize_rgb(crop, w=int(roi.width), h=int(roi.height))
    if bw is None:
        return _hard_stop("missing_dependency", details={"need": "opencv-python + numpy"})

    h = int(roi.height)
    mid = max(1, h // 2)

    top = bw[0 : mid * int(roi.width)]
    bottom = bw[mid * int(roi.width) :]

    # Note: bw is 1-channel bytes; reshape in extractor by passing w/h.
    top_glyphs = _extract_digit_glyphs(top, w=int(roi.width), h=mid)
    bottom_glyphs = _extract_digit_glyphs(bottom, w=int(roi.width), h=h - mid)
    full_glyphs = _extract_digit_glyphs(bw, w=int(roi.width), h=h)

    new_templates: dict[str, list[list[int]]] = {}

    if args.soul is not None:
        s = str(int(args.soul))
        glyphs = top_glyphs
        if args.cap is None:
            glyphs = full_glyphs
        if len(glyphs) != len(s):
            return _hard_stop(
                "calibration_failed",
                details={"line": "soul", "expected": s, "glyphs": len(glyphs)},
            )
        for ch, glyph in zip(s, glyphs, strict=True):
            if ch not in "0123456789":
                continue
            new_templates.setdefault(ch, []).append(_glyph_to_template(glyph))

    if args.cap is not None:
        c = str(int(args.cap))
        glyphs = bottom_glyphs
        if args.soul is None:
            glyphs = full_glyphs
        if len(glyphs) != len(c):
            return _hard_stop(
                "calibration_failed",
                details={"line": "cap", "expected": c, "glyphs": len(glyphs)},
            )
        for ch, glyph in zip(c, glyphs, strict=True):
            if ch not in "0123456789":
                continue
            new_templates.setdefault(ch, []).append(_glyph_to_template(glyph))

    out_path = Path(str(args.out)).expanduser()
    existing = _load_templates(out_path)
    merged = _merge_templates(existing, new_templates)
    _save_templates(out_path, merged)

    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "out": str(out_path),
                "digits_added": {k: len(v) for k, v in new_templates.items()},
                "digits_total": {k: len(v) for k, v in merged.items()},
                "roi_dump": str(args.dump_roi),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
