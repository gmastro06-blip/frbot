from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage  # type: ignore

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True, slots=True)
class RoiDef:
    name: str
    x: int
    y: int
    width: int
    height: int


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _aspect_ratio_pair(w: int, h: int) -> tuple[int, int]:
    if w <= 0 or h <= 0:
        return (0, 0)
    g = math.gcd(int(w), int(h))
    return (int(w) // g, int(h) // g)


def _hard_fail(msg: str) -> NoReturn:
    raise SystemExit(msg)


def _require_exact_keys(obj: dict[str, Any], *, expected: set[str], context: str) -> None:
    actual = set(obj.keys())
    if actual != set(expected):
        _hard_fail(
            f'roi_json_invalid: {context} keys mismatch: expected={sorted(expected)} got={sorted(actual)}'
        )


def _json_int(value: Any, *, field: str) -> int:
    if value is None:
        _hard_fail(f'roi_json_invalid: {field} required')

    # JSON numbers are int/float; also allow numeric strings for convenience.
    if isinstance(value, bool):
        _hard_fail(f'roi_json_invalid: {field} must be int')
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        _hard_fail(f'roi_json_invalid: {field} must be int')
    if isinstance(value, str):
        try:
            return int(value, 10)
        except Exception:
            _hard_fail(f'roi_json_invalid: {field} must be int')

    _hard_fail(f'roi_json_invalid: {field} must be int')


def _validate_roi(roi: RoiDef, *, img_w: int, img_h: int) -> None:
    if not roi.name.strip():
        _hard_fail('roi_invalid: empty name')
    if roi.x < 0 or roi.y < 0:
        _hard_fail(f'roi_invalid: negative origin: {roi.name}')
    if roi.width <= 0 or roi.height <= 0:
        _hard_fail(f'roi_invalid: non-positive size: {roi.name}')
    if roi.x + roi.width > img_w or roi.y + roi.height > img_h:
        _hard_fail(f'roi_invalid: out_of_bounds: {roi.name}')


def _load_image(image_path: Path) -> "PILImage":
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        _hard_fail(f'missing_dependency: pillow: {type(exc).__name__}: {exc}')

    try:
        im = Image.open(image_path)
        im.load()  # force decode now (deterministic metadata)
        return im
    except Exception as exc:
        _hard_fail(f'image_open_failed: {type(exc).__name__}: {exc}')


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def _draw_overlay(*, image_path: Path, rois: list[RoiDef], out_path: Path) -> None:
    try:
        from PIL import ImageDraw  # type: ignore
    except Exception as exc:
        _hard_fail(f'missing_dependency: pillow: {type(exc).__name__}: {exc}')

    im = _load_image(image_path)
    if im.mode != 'RGB':
        im = im.convert('RGB')

    d = ImageDraw.Draw(im)
    for r in rois:
        # Rectangle is [x0,y0,x1-1,y1-1] in pixel coords for inclusive outline.
        x0 = int(r.x)
        y0 = int(r.y)
        x1 = int(r.x + r.width - 1)
        y1 = int(r.y + r.height - 1)
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=2)
        d.text((x0 + 3, y0 + 3), str(r.name), fill=(255, 0, 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, format='PPM')


def _load_rois_json(path: Path) -> tuple[dict[str, Any], list[RoiDef]]:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _hard_fail(f'roi_json_invalid: {type(exc).__name__}: {exc}')

    if not isinstance(obj, dict):
        _hard_fail('roi_json_invalid: root must be object')

    obj = cast(dict[str, Any], obj)

    # Strict schema: deterministic + auditable.
    _require_exact_keys(
        obj,
        expected={'source', 'roi_mode', 'reference_resolution', 'rois'},
        context='root',
    )

    if str(obj.get('source') or '') != 'OBS Projector':
        _hard_fail('roi_json_invalid: source must be "OBS Projector"')
    if str(obj.get('roi_mode') or '') != 'absolute_px':
        _hard_fail('roi_json_invalid: roi_mode must be "absolute_px"')

    ref_raw = obj.get('reference_resolution')
    if not isinstance(ref_raw, dict):
        _hard_fail('roi_json_invalid: reference_resolution must be object')
    ref = cast(dict[str, Any], ref_raw)
    _require_exact_keys(ref, expected={'width', 'height'}, context='reference_resolution')
    _ = _json_int(ref.get('width'), field='reference_resolution.width')
    _ = _json_int(ref.get('height'), field='reference_resolution.height')

    rois_raw = obj.get('rois')
    if not isinstance(rois_raw, list):
        _hard_fail('roi_json_invalid: rois must be list')

    if len(rois_raw) < 1:
        _hard_fail('roi_json_invalid: rois must be non-empty')

    rois: list[RoiDef] = []
    seen_names: set[str] = set()
    for i, item in enumerate(rois_raw):
        if not isinstance(item, dict):
            _hard_fail(f'roi_json_invalid: rois[{i}] must be object')
        try:
            item = cast(dict[str, Any], item)
            _require_exact_keys(item, expected={'name', 'x', 'y', 'width', 'height'}, context=f'rois[{i}]')
            rois.append(
                RoiDef(
                    name=str(item.get('name') or ''),
                    x=_json_int(item.get('x'), field=f'rois[{i}].x'),
                    y=_json_int(item.get('y'), field=f'rois[{i}].y'),
                    width=_json_int(item.get('width'), field=f'rois[{i}].width'),
                    height=_json_int(item.get('height'), field=f'rois[{i}].height'),
                )
            )
        except Exception:
            _hard_fail(f'roi_json_invalid: rois[{i}] fields must be ints + name')

        if not rois[-1].name.strip():
            _hard_fail(f'roi_json_invalid: rois[{i}].name empty')
        if rois[-1].name in seen_names:
            _hard_fail(f'roi_json_invalid: duplicate roi name: {rois[-1].name}')
        seen_names.add(rois[-1].name)

    return obj, rois


def _validate_resolution_drift(*, image_path: Path, reference_w: int, reference_h: int) -> dict[str, object]:
    im = _load_image(image_path)
    w, h = (int(im.size[0]), int(im.size[1]))
    ref_ar = _aspect_ratio_pair(int(reference_w), int(reference_h))
    cur_ar = _aspect_ratio_pair(int(w), int(h))

    ok = (int(w) == int(reference_w)) and (int(h) == int(reference_h)) and (ref_ar == cur_ar)
    return {
        'ok': bool(ok),
        'current': {'width': int(w), 'height': int(h), 'aspect_ratio': {'w': int(cur_ar[0]), 'h': int(cur_ar[1])}},
        'reference': {'width': int(reference_w), 'height': int(reference_h), 'aspect_ratio': {'w': int(ref_ar[0]), 'h': int(ref_ar[1])}},
    }


def _interactive_calibrate(
    *,
    image_path: Path,
    out_json: Path,
    out_verify_ppm: Path,
    out_log: Path,
    load_json: Path | None,
) -> int:
    # Import GUI deps only when needed.
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        _hard_fail(f'missing_dependency: tkinter: {type(exc).__name__}: {exc}')

    try:
        from PIL import ImageTk  # type: ignore
    except Exception as exc:
        _hard_fail(f'missing_dependency: pillow: {type(exc).__name__}: {exc}')

    if not image_path.exists():
        _hard_fail(f'image_not_found: {image_path}')

    im = _load_image(image_path)
    img_w, img_h = (int(im.size[0]), int(im.size[1]))
    img_mode = str(im.mode)
    img_format = str(im.format or '')
    img_sha256 = _sha256_file(image_path)

    ref_ar = _aspect_ratio_pair(img_w, img_h)

    # App state
    rois: list[RoiDef] = []
    if load_json is not None:
        # Strictly validate existing ROI file and ensure it matches this image.
        root_obj, loaded = _load_rois_json(load_json)
        ref = cast(dict[str, Any], root_obj['reference_resolution'])
        ref_w = _json_int(ref.get('width'), field='reference_resolution.width')
        ref_h = _json_int(ref.get('height'), field='reference_resolution.height')
        drift = _validate_resolution_drift(image_path=image_path, reference_w=int(ref_w), reference_h=int(ref_h))
        if not bool(drift.get('ok')):
            _hard_fail('resolution_or_aspect_drift_detected: full recalibration required')
        rois = list(loaded)
    current_rect_id: int | None = None
    start_xy: tuple[int, int] | None = None

    root = tk.Tk()
    root.title('OBS Projector ROI Calibration (absolute pixels)')

    # Left: canvas
    # Tkinter types are often treated as untyped; keep canvas/listbox as Any to avoid unsafe assumptions.
    canvas: Any = tk.Canvas(root, width=img_w, height=img_h, highlightthickness=0)
    canvas.grid(row=0, column=0, rowspan=2, sticky='nsew')

    # Ensure no scaling: canvas exactly equals image size.
    tk_im = ImageTk.PhotoImage(im)
    canvas.create_image(0, 0, image=tk_im, anchor='nw')

    # Right: controls
    right = ttk.Frame(root, padding=8)
    right.grid(row=0, column=1, sticky='nsew')

    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)

    # Live status
    status_var = tk.StringVar(value=f'image: {img_w}x{img_h} mode={img_mode} format={img_format}')
    status = ttk.Label(root, textvariable=status_var)
    status.grid(row=1, column=1, sticky='ew', padx=8, pady=(0, 8))

    # ROI form
    name_var = tk.StringVar(value='')
    x_var = tk.StringVar(value='0')
    y_var = tk.StringVar(value='0')
    w_var = tk.StringVar(value='0')
    h_var = tk.StringVar(value='0')

    def _set_form_from_roi(r: RoiDef) -> None:
        name_var.set(r.name)
        x_var.set(str(int(r.x)))
        y_var.set(str(int(r.y)))
        w_var.set(str(int(r.width)))
        h_var.set(str(int(r.height)))

    def _form_to_roi() -> RoiDef:
        return RoiDef(
            name=str(name_var.get() or '').strip(),
            x=int(str(x_var.get() or '0'), 10),
            y=int(str(y_var.get() or '0'), 10),
            width=int(str(w_var.get() or '0'), 10),
            height=int(str(h_var.get() or '0'), 10),
        )

    ttk.Label(right, text='ROI name').grid(row=0, column=0, sticky='w')
    ttk.Entry(right, textvariable=name_var, width=28).grid(row=0, column=1, sticky='ew')

    ttk.Label(right, text='x').grid(row=1, column=0, sticky='w')
    ttk.Entry(right, textvariable=x_var, width=10).grid(row=1, column=1, sticky='w')
    ttk.Label(right, text='y').grid(row=2, column=0, sticky='w')
    ttk.Entry(right, textvariable=y_var, width=10).grid(row=2, column=1, sticky='w')
    ttk.Label(right, text='width').grid(row=3, column=0, sticky='w')
    ttk.Entry(right, textvariable=w_var, width=10).grid(row=3, column=1, sticky='w')
    ttk.Label(right, text='height').grid(row=4, column=0, sticky='w')
    ttk.Entry(right, textvariable=h_var, width=10).grid(row=4, column=1, sticky='w')

    list_var = tk.Variable(value=())
    roi_list: Any = tk.Listbox(right, listvariable=list_var, height=10)
    roi_list.grid(row=5, column=0, columnspan=2, sticky='nsew', pady=(8, 4))

    right.grid_rowconfigure(5, weight=1)
    right.grid_columnconfigure(1, weight=1)

    def _curselection(lb: "tk.Listbox") -> tuple[int, ...]:
        # Some type-checking configs treat tkinter methods as untyped; isolate/suppress here.
        return cast(tuple[int, ...], lb.curselection())  # type: ignore[no-untyped-call]

    def _refresh_list() -> None:
        list_var.set(tuple(f'{r.name}: ({r.x},{r.y}) {r.width}x{r.height}' for r in rois))

    def _draw_all_overlays() -> None:
        canvas.delete('roi')
        for r in rois:
            x0, y0 = int(r.x), int(r.y)
            x1, y1 = int(r.x + r.width), int(r.y + r.height)
            canvas.create_rectangle(x0, y0, x1, y1, outline='red', width=2, tags=('roi',))
            canvas.create_text(x0 + 4, y0 + 4, anchor='nw', text=r.name, fill='red', tags=('roi',))

    def _select_idx(evt: object) -> None:
        try:
            sel = _curselection(roi_list)
            if not sel:
                return
            r = rois[int(sel[0])]
            _set_form_from_roi(r)
        except Exception:
            return

    roi_list.bind('<<ListboxSelect>>', _select_idx)

    err_var = tk.StringVar(value='')
    err_label = ttk.Label(right, textvariable=err_var, foreground='red')
    err_label.grid(row=6, column=0, columnspan=2, sticky='ew')

    def _set_err(msg: str) -> None:
        err_var.set(str(msg))

    def _apply_form_preview() -> None:
        nonlocal current_rect_id
        _set_err('')
        try:
            r = _form_to_roi()
            _validate_roi(r, img_w=img_w, img_h=img_h)
        except Exception as exc:
            _set_err(str(exc))
            return

        # draw preview
        if current_rect_id is not None:
            try:
                canvas.delete(current_rect_id)
            except Exception:
                pass
            current_rect_id = None

        x0, y0 = int(r.x), int(r.y)
        x1, y1 = int(r.x + r.width), int(r.y + r.height)
        current_rect_id = canvas.create_rectangle(x0, y0, x1, y1, outline='cyan', width=2, dash=(3, 2), tags=('preview',))

    def _add_or_update() -> None:
        _set_err('')
        try:
            r = _form_to_roi()
            _validate_roi(r, img_w=img_w, img_h=img_h)
        except Exception as exc:
            _set_err(str(exc))
            return

        # Update selected, else append.
        try:
            sel = _curselection(roi_list)
            if sel:
                rois[int(sel[0])] = r
            else:
                rois.append(r)
        except Exception:
            rois.append(r)

        _refresh_list()
        _draw_all_overlays()

    def _delete_selected() -> None:
        _set_err('')
        try:
            sel = _curselection(roi_list)
            if not sel:
                return
            del rois[int(sel[0])]
            _refresh_list()
            _draw_all_overlays()
        except Exception as exc:
            _set_err(str(exc))

    def _save() -> None:
        _set_err('')
        # Validate at least one ROI.
        if not rois:
            _set_err('no_rois_defined')
            return

        # Drift check: re-open image from disk and ensure exact resolution + aspect.
        drift = _validate_resolution_drift(image_path=image_path, reference_w=img_w, reference_h=img_h)
        if not bool(drift.get('ok')):
            _set_err('resolution_or_aspect_drift_detected: full recalibration required')
            _hard_fail('resolution_or_aspect_drift_detected: full recalibration required')

        # Validate all ROIs again.
        seen_names: set[str] = set()
        for r in rois:
            _validate_roi(r, img_w=img_w, img_h=img_h)
            if r.name in seen_names:
                _set_err(f'duplicate_roi_name: {r.name}')
                _hard_fail(f'duplicate_roi_name: {r.name}')
            seen_names.add(r.name)

        payload = {
            'source': 'OBS Projector',
            'roi_mode': 'absolute_px',
            'reference_resolution': {'width': int(img_w), 'height': int(img_h)},
            'rois': [
                {
                    'name': r.name,
                    'x': int(r.x),
                    'y': int(r.y),
                    'width': int(r.width),
                    'height': int(r.height),
                }
                for r in rois
            ],
        }

        # Strict schema check before writing.
        _require_exact_keys(
            cast(dict[str, Any], payload),
            expected={'source', 'roi_mode', 'reference_resolution', 'rois'},
            context='payload',
        )

        # Persist outputs.
        out_json.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out_json, payload)
        _draw_overlay(image_path=image_path, rois=rois, out_path=out_verify_ppm)

        log_obj = {
            'image': {
                'path': str(image_path),
                'sha256': str(img_sha256),
                'bytes': int(image_path.stat().st_size),
                'width': int(img_w),
                'height': int(img_h),
                'aspect_ratio': {'w': int(ref_ar[0]), 'h': int(ref_ar[1])},
                'pixel_format': str(img_mode),
                'container_format': str(img_format),
            },
            'roi_json': {
                'path': str(out_json),
                'sha256': _sha256_file(out_json),
                'bytes': int(out_json.stat().st_size),
            },
            'validation': {
                'drift_check': drift,
                'rois_in_bounds': True,
            },
            'outputs': {
                'rois_json': str(out_json),
                'verify_ppm': str(out_verify_ppm),
            },
            'rois': [
                {'name': r.name, 'x': int(r.x), 'y': int(r.y), 'width': int(r.width), 'height': int(r.height)}
                for r in rois
            ],
        }
        out_log.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out_log, log_obj)

        status_var.set(f'SAVED: {out_json.name} / {out_verify_ppm.name}')

    # Buttons
    btns = ttk.Frame(right)
    btns.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(8, 0))

    ttk.Button(btns, text='Preview from fields', command=_apply_form_preview).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(btns, text='Add/Update ROI', command=_add_or_update).grid(row=0, column=1, padx=(0, 6))
    ttk.Button(btns, text='Delete selected', command=_delete_selected).grid(row=0, column=2, padx=(0, 6))
    ttk.Button(btns, text='Save', command=_save).grid(row=0, column=3)

    # Mouse handlers (click+drag)
    def _clamp_xy(x: int, y: int) -> tuple[int, int]:
        return (max(0, min(int(img_w - 1), int(x))), max(0, min(int(img_h - 1), int(y))))

    def _on_move(evt: tk.Event) -> None:  # type: ignore[name-defined]
        x, y = _clamp_xy(int(getattr(evt, 'x', 0)), int(getattr(evt, 'y', 0)))
        # Live coordinates display
        if start_xy is None:
            status_var.set(f'x={x} y={y}   image={img_w}x{img_h} mode={img_mode}')
        else:
            sx, sy = start_xy
            w = max(1, x - sx)
            h = max(1, y - sy)
            status_var.set(f'x={x} y={y}   sel: ({sx},{sy}) {w}x{h}')

    def _on_down(evt: tk.Event) -> None:  # type: ignore[name-defined]
        nonlocal start_xy, current_rect_id
        x, y = _clamp_xy(int(getattr(evt, 'x', 0)), int(getattr(evt, 'y', 0)))
        start_xy = (x, y)
        # Start new preview rectangle
        canvas.delete('preview')
        current_rect_id = canvas.create_rectangle(x, y, x + 1, y + 1, outline='cyan', width=2, dash=(3, 2), tags=('preview',))

    def _on_drag(evt: tk.Event) -> None:  # type: ignore[name-defined]
        nonlocal current_rect_id
        if start_xy is None:
            return
        x, y = _clamp_xy(int(getattr(evt, 'x', 0)), int(getattr(evt, 'y', 0)))
        sx, sy = start_xy
        x1 = max(sx + 1, x)
        y1 = max(sy + 1, y)

        # current_rect_id can be None if the preview was cleared; ensure it exists before coords().
        if current_rect_id is None:
            current_rect_id = canvas.create_rectangle(
                sx, sy, x1, y1, outline='cyan', width=2, dash=(3, 2), tags=('preview',)
            )
        else:
            canvas.coords(current_rect_id, sx, sy, x1, y1)

        # Update fields live
        name = str(name_var.get() or '').strip()
        x_var.set(str(int(sx)))
        y_var.set(str(int(sy)))
        w_var.set(str(int(x1 - sx)))
        h_var.set(str(int(y1 - sy)))
        if name:
            name_var.set(name)

    def _on_up(evt: tk.Event) -> None:  # type: ignore[name-defined]
        nonlocal start_xy
        start_xy = None

    canvas.bind('<Motion>', _on_move)
    canvas.bind('<ButtonPress-1>', _on_down)
    canvas.bind('<B1-Motion>', _on_drag)
    canvas.bind('<ButtonRelease-1>', _on_up)

    # Draw initial overlays
    _draw_all_overlays()

    root.mainloop()
    return 0


def _headless_validate_and_overlay(*, image_path: Path, in_json: Path, out_verify_ppm: Path, out_log: Path) -> int:
    if not image_path.exists():
        _hard_fail(f'image_not_found: {image_path}')
    if not in_json.exists():
        _hard_fail(f'roi_json_not_found: {in_json}')

    root_obj, rois = _load_rois_json(in_json)

    ref = cast(dict[str, Any], root_obj['reference_resolution'])
    ref_w = _json_int(ref.get('width'), field='reference_resolution.width')
    ref_h = _json_int(ref.get('height'), field='reference_resolution.height')

    drift = _validate_resolution_drift(image_path=image_path, reference_w=ref_w, reference_h=ref_h)
    if not bool(drift.get('ok')):
        _hard_fail('resolution_or_aspect_drift_detected: full recalibration required')

    im = _load_image(image_path)
    img_w, img_h = (int(im.size[0]), int(im.size[1]))
    for r in rois:
        _validate_roi(r, img_w=img_w, img_h=img_h)

    _draw_overlay(image_path=image_path, rois=rois, out_path=out_verify_ppm)

    log_obj = {
        'image': {
            'path': str(image_path),
            'sha256': _sha256_file(image_path),
            'bytes': int(image_path.stat().st_size),
            'width': int(img_w),
            'height': int(img_h),
            'aspect_ratio': {'w': int(_aspect_ratio_pair(img_w, img_h)[0]), 'h': int(_aspect_ratio_pair(img_w, img_h)[1])},
            'pixel_format': str(im.mode),
            'container_format': str(im.format or ''),
        },
        'roi_json': {
            'path': str(in_json),
            'sha256': _sha256_file(in_json),
            'bytes': int(in_json.stat().st_size),
        },
        'validation': {
            'drift_check': drift,
            'rois_in_bounds': True,
        },
        'outputs': {
            'verify_ppm': str(out_verify_ppm),
        },
        'rois': [
            {'name': r.name, 'x': int(r.x), 'y': int(r.y), 'width': int(r.width), 'height': int(r.height)}
            for r in rois
        ],
    }

    out_log.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out_log, log_obj)
    print(json.dumps({'ok': True, 'validated': True}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Calibrate deterministic absolute-pixel ROIs for OBS Projector capture.')
    ap.add_argument('--image', default='diagnostics/obs_projector_full.ppm', help='Input PPM (lossless) full-frame capture')
    ap.add_argument('--out-json', default='', help='Output JSON path (default: ./obs_projector_rois.json)')
    ap.add_argument('--out-verify-ppm', default='', help='Output verification PPM path (default: ./obs_projector_roi_verify.ppm)')
    ap.add_argument('--out-log', default='', help='Output log path (default: ./obs_projector_roi_calibration.log.json)')
    ap.add_argument('--load-json', default='', help='Load an existing obs_projector_rois.json to edit/append (strict drift check)')
    ap.add_argument('--validate-only', action='store_true', help='Non-interactive: validate existing JSON + generate overlay')
    ap.add_argument('--in-json', default='', help='When --validate-only: path to obs_projector_rois.json')
    args = ap.parse_args(argv)

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        try:
            from diagnostics.fatal import write_fatal

            write_fatal('feature_disabled', details={'tool': 'calibrate_obs_projector_rois', 'profile': profile})
        except Exception:
            pass
        print(json.dumps({'ok': False, 'reason': 'feature_disabled', 'details': {'tool': 'calibrate_obs_projector_rois', 'profile': profile}}, ensure_ascii=False))
        return 2

    image_path = Path(str(args.image)).resolve()
    out_json = Path(str(args.out_json)).resolve() if str(args.out_json).strip() else Path('obs_projector_rois.json').resolve()
    out_verify = (
        Path(str(args.out_verify_ppm)).resolve()
        if str(args.out_verify_ppm).strip()
        else Path('obs_projector_roi_verify.ppm').resolve()
    )
    out_log = (
        Path(str(args.out_log)).resolve()
        if str(args.out_log).strip()
        else Path('obs_projector_roi_calibration.log.json').resolve()
    )

    load_json = Path(str(args.load_json)).resolve() if str(args.load_json).strip() else None

    if args.validate_only:
        in_json = Path(str(args.in_json)).resolve() if str(args.in_json).strip() else out_json
        return _headless_validate_and_overlay(image_path=image_path, in_json=in_json, out_verify_ppm=out_verify, out_log=out_log)

    return _interactive_calibrate(
        image_path=image_path,
        out_json=out_json,
        out_verify_ppm=out_verify,
        out_log=out_log,
        load_json=load_json,
    )


if __name__ == '__main__':
    raise SystemExit(main())
