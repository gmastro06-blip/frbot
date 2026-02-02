from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.capture.meld_real import _sample_luma_stats  # re-use exact same stats logic
from adapters.windows import win32 as w32
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import Frame
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from runtime.config_loader import load_rois
from runtime.runner import _load_config_from_env


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _hard_fail(reason: str, *, details: dict) -> int:
    exc = PreflightFailed(reason)
    setattr(exc, 'details', details)
    write_fatal(reason, exc, details=details)
    print(json.dumps({'ok': False, 'reason': reason, 'details': details}, ensure_ascii=False))
    return 2


def _validate_region(*, frame_w: int, frame_h: int, x: int, y: int, w: int, h: int) -> tuple[bool, str | None]:
    if int(x) < 0 or int(y) < 0:
        return False, 'negative_origin'
    if int(w) <= 0 or int(h) <= 0:
        return False, 'non_positive_size'
    if (int(x) + int(w)) > int(frame_w) or (int(y) + int(h)) > int(frame_h):
        return False, 'out_of_bounds'
    return True, None


def _clamp_region(*, frame_w: int, frame_h: int, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(frame_w), int(x) + int(w))
    y1 = min(int(frame_h), int(y) + int(h))
    return int(x0), int(y0), int(max(0, x1 - x0)), int(max(0, y1 - y0))


def _sample_region_luma_stats(
    rgb: bytes,
    *,
    frame_w: int,
    frame_h: int,
    x: int,
    y: int,
    w: int,
    h: int,
    grid: int = 10,
) -> tuple[float, float, bool]:
    """Fast approximate luma stats over a sub-rectangle.

    Samples a grid x grid points inside the region to estimate (mean, std, all_zero).
    """

    if frame_w <= 0 or frame_h <= 0 or w <= 0 or h <= 0:
        return 0.0, 0.0, True
    if x < 0 or y < 0 or (x + w) > frame_w or (y + h) > frame_h:
        return 0.0, 0.0, True
    if len(rgb) != frame_w * frame_h * 3:
        return 0.0, 0.0, True

    g = max(2, int(grid))
    # Welford online variance
    mean = 0.0
    m2 = 0.0
    n = 0
    all_zero = True

    for gy in range(g):
        sy = y + int((gy + 0.5) * h / g)
        if sy >= (y + h):
            sy = (y + h) - 1
        row = sy * frame_w * 3
        for gx in range(g):
            sx = x + int((gx + 0.5) * w / g)
            if sx >= (x + w):
                sx = (x + w) - 1
            idx = row + (sx * 3)
            r = rgb[idx]
            gch = rgb[idx + 1]
            b = rgb[idx + 2]
            if (r | gch | b) != 0:
                all_zero = False
            yv = (float(r) + float(gch) + float(b)) / 3.0
            n += 1
            delta = yv - mean
            mean += delta / float(n)
            delta2 = yv - mean
            m2 += delta * delta2

    if n <= 1:
        return float(mean), 0.0, bool(all_zero)
    var = m2 / float(n - 1)
    return float(mean), float(var ** 0.5), bool(all_zero)


def _suggest_regions(
    rgb: bytes,
    *,
    frame_w: int,
    frame_h: int,
    region_w: int,
    region_h: int,
    step: int = 48,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Suggest likely "interesting" regions (high std) for ROI recalibration."""

    if frame_w <= 0 or frame_h <= 0 or region_w <= 0 or region_h <= 0:
        return []
    if len(rgb) != frame_w * frame_h * 3:
        return []

    stride = max(8, int(step))
    best: list[tuple[float, dict[str, object]]] = []

    max_x = max(0, frame_w - region_w)
    max_y = max(0, frame_h - region_h)
    for y in range(0, max_y + 1, stride):
        for x in range(0, max_x + 1, stride):
            mean, std, all_zero = _sample_region_luma_stats(
                rgb,
                frame_w=frame_w,
                frame_h=frame_h,
                x=int(x),
                y=int(y),
                w=int(region_w),
                h=int(region_h),
                grid=10,
            )
            score = float(std) if not all_zero else 0.0
            item: dict[str, object] = {
                'x': int(x),
                'y': int(y),
                'width': int(region_w),
                'height': int(region_h),
                'mean_luma': float(mean),
                'std_luma': float(std),
                'all_zero': bool(all_zero),
                'score': float(score),
            }
            best.append((score, item))

    best.sort(key=lambda t: t[0], reverse=True)
    out: list[dict[str, object]] = []
    for score, item in best[: max(1, int(limit))]:
        if float(score) <= 0.0:
            continue
        out.append(item)
    return out


def _dxcam_output_map(dxcam_mod: Any, *, max_outputs: int = 8) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for idx in range(int(max_outputs)):
        try:
            cam = dxcam_mod.create(output_idx=int(idx))
        except Exception:
            continue
        name = ''
        try:
            o = getattr(cam, '_output', None)
            name = str(getattr(o, 'name', '') or '')
        except Exception:
            name = ''
        if not name:
            continue
        out[name] = {'output_idx': int(idx), 'width': int(getattr(cam, 'width', 0) or 0), 'height': int(getattr(cam, 'height', 0) or 0)}
    return out


def _roi_dict(roi: object) -> dict[str, int | str]:
    return {
        'name': str(getattr(roi, 'name', '')),
        'x': int(getattr(roi, 'x', 0) or 0),
        'y': int(getattr(roi, 'y', 0) or 0),
        'width': int(getattr(roi, 'width', 0) or 0),
        'height': int(getattr(roi, 'height', 0) or 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--wait-seconds', type=float, default=0.0, help='Wait up to N seconds for Tibia window to be foreground')
    ap.add_argument('--focus', action='store_true', help='Best-effort focus the bound window before verification')
    ap.add_argument('--try-all-outputs', action='store_true', help='Probe multiple dxcam outputs and pick the one with the most non-black signal (diagnostic only)')
    ap.add_argument('--max-outputs', type=int, default=6, help='Max outputs to probe when --try-all-outputs is enabled')
    args = ap.parse_args()

    backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'meld') or 'meld').strip().lower()
    if backend != 'meld':
        return _hard_fail('capture_black_or_unavailable', details={'expected_backend': 'meld', 'got': backend})

    try:
        import dxcam  # type: ignore
    except Exception as exc:
        return _hard_fail('capture_black_or_unavailable', details={'import_error': f'{type(exc).__name__}: {exc}'})

    cfg = _load_config_from_env()

    # Load ROIs from config.
    ctx = RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )
    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    roi = ctx.rois.get(cfg.minimap_roi)
    if roi is None:
        return _hard_fail('minimap_not_detected', details={'minimap_roi': str(cfg.minimap_roi)})

    binding = Win32WindowBinding(hwnd=int(cfg.window_hwnd), title_substring=cfg.window_title_substring)

    # Optional focus/wait helpers (still enforces strict foreground).
    target_hwnd = int(cfg.window_hwnd)
    if target_hwnd <= 0 and (cfg.window_title_substring or '').strip():
        m = w32.find_window_by_title_substring(cfg.window_title_substring)
        if m is not None:
            target_hwnd = int(m.hwnd)

    if args.focus and target_hwnd > 0:
        w32.try_focus_window(target_hwnd)
        time.sleep(0.15)

    if float(args.wait_seconds) > 0 and target_hwnd > 0:
        deadline = time.time() + float(args.wait_seconds)
        while time.time() < deadline:
            try:
                if int(w32.get_foreground_window()) == int(target_hwnd):
                    break
            except Exception:
                pass
            time.sleep(0.2)

    bvr = binding.verify()
    if not bvr.ok:
        return _hard_fail('window_binding_lost', details={'reason': bvr.reason})

    snap = binding.snapshot()
    hwnd = int(getattr(snap, 'hwnd', 0) or 0)
    if hwnd <= 0:
        return _hard_fail('window_binding_lost', details={'reason': 'window_binding_lost'})

    # Strict: must be foreground before capture.
    try:
        fg = int(w32.get_foreground_window())
    except Exception:
        fg = 0
    if fg != hwnd:
        return _hard_fail('window_binding_lost', details={'reason': 'window_not_foreground'})

    rect_client = w32.get_client_rect_in_screen(hwnd)

    # Select monitor and dxcam output_idx.
    mons = w32.list_monitors()
    cx = (int(rect_client.left) + int(rect_client.right)) // 2
    cy = (int(rect_client.top) + int(rect_client.bottom)) // 2
    mon = w32.monitor_for_point(cx, cy, mons) or w32.primary_monitor(mons)
    if mon is None:
        return _hard_fail('capture_black_or_unavailable', details={'reason': 'no_monitors'})

    output_map = _dxcam_output_map(dxcam)
    output_idx = 0
    if mon.device in output_map:
        v = output_map[mon.device].get('output_idx', 0)
        output_idx = int(v) if isinstance(v, int) else 0

    # Optional: probe all outputs and select the one that looks most "real".
    dxcam_probe: list[dict[str, object]] = []
    output_name = ''
    if bool(args.try_all_outputs):
        best_idx = None
        best_score = -1.0
        best_name = ''
        max_out = max(1, int(args.max_outputs))
        for idx in range(max_out):
            try:
                cam_try = dxcam.create(output_idx=int(idx))
            except Exception as exc:
                dxcam_probe.append({'output_idx': int(idx), 'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
                continue

            try:
                o = getattr(cam_try, '_output', None)
                name = str(getattr(o, 'name', '') or '')
            except Exception:
                name = ''

            try:
                fr = cam_try.grab()
            except Exception as exc:
                dxcam_probe.append({'output_idx': int(idx), 'ok': False, 'name': name, 'error': f'grab_failed: {type(exc).__name__}: {exc}'})
                continue

            from adapters.capture.meld_real import _to_rgb_bytes  # local import to avoid exporting

            rgb, fw, fh, meta_try = _to_rgb_bytes(fr)
            if not rgb or fw <= 0 or fh <= 0:
                dxcam_probe.append({'output_idx': int(idx), 'ok': False, 'name': name, 'meta': meta_try, 'frame_size': [int(fw), int(fh)]})
                continue

            mean_try, std_try, all_zero_try = _sample_luma_stats(rgb, width=int(fw), height=int(fh))
            score = float(std_try) if not all_zero_try else 0.0
            dxcam_probe.append(
                {
                    'output_idx': int(idx),
                    'ok': True,
                    'name': name,
                    'frame_size': [int(fw), int(fh)],
                    'mean_luma': float(mean_try),
                    'std_luma': float(std_try),
                    'all_zero': bool(all_zero_try),
                }
            )
            if score > best_score:
                best_score = score
                best_idx = int(idx)
                best_name = name

        if best_idx is not None:
            output_idx = int(best_idx)
            output_name = str(best_name)

    cam = dxcam.create(output_idx=int(output_idx))
    if not output_name:
        try:
            o = getattr(cam, '_output', None)
            output_name = str(getattr(o, 'name', '') or '')
        except Exception:
            output_name = ''

    # 1) FULL FRAME baseline capture
    full = cam.grab()
    shape = getattr(full, 'shape', None)
    if not (isinstance(shape, tuple) and len(shape) >= 2):
        return _hard_fail('capture_black_or_unavailable', details={'reason': 'frame_no_shape'})

    frame_h = int(shape[0])
    frame_w = int(shape[1])

    # Normalize to RGB via Frame helper approach (assume 3 or 4 channels).
    # We reuse the same conversion logic by constructing a temporary Frame from meld_real's _to_rgb_bytes.
    from adapters.capture.meld_real import _to_rgb_bytes  # local import to avoid exporting

    rgb24, w, h, meta = _to_rgb_bytes(full)
    if not rgb24 or w <= 0 or h <= 0:
        return _hard_fail('capture_black_or_unavailable', details={'reason': 'frame_empty', 'meta': meta})

    ts_ns = time.monotonic_ns()
    full_frame = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts_ns), digest_hex='', rgb=rgb24)

    dump_frame_ppm(full_frame, Path('diagnostics/region_full.ppm'))

    full_mean, full_std, full_all_zero = _sample_luma_stats(rgb24, width=int(w), height=int(h))

    # 2) Derive region from ROI config (ROI is relative to window capture)
    win_x = int(rect_client.left) - int(mon.rect.left)
    win_y = int(rect_client.top) - int(mon.rect.top)

    region_x = int(win_x) + int(roi.x)
    region_y = int(win_y) + int(roi.y)
    region_w = int(roi.width)
    region_h = int(roi.height)

    clamp_enabled = _env_bool('FRBOT_CAPTURE_CLAMP', False)

    ok, why = _validate_region(frame_w=int(w), frame_h=int(h), x=int(region_x), y=int(region_y), w=int(region_w), h=int(region_h))
    clamped = False
    clamped_region = None
    if (not ok) and clamp_enabled:
        region_x, region_y, region_w, region_h = _clamp_region(frame_w=int(w), frame_h=int(h), x=int(region_x), y=int(region_y), w=int(region_w), h=int(region_h))
        clamped = True
        clamped_region = [int(region_x), int(region_y), int(region_w), int(region_h)]
        ok, why = _validate_region(frame_w=int(w), frame_h=int(h), x=int(region_x), y=int(region_y), w=int(region_w), h=int(region_h))

    requested_region = [int(win_x) + int(roi.x), int(win_y) + int(roi.y), int(roi.width), int(roi.height)]
    region_now = [int(region_x), int(region_y), int(region_w), int(region_h)]

    if not ok:
        return _hard_fail(
            'invalid_capture_region',
            details={
                'frame_size': [int(w), int(h)],
                'roi': _roi_dict(roi),
                'requested_region': requested_region,
                'region': region_now,
                'region_valid': False,
                'clamp_enabled': bool(clamp_enabled),
                'clamped': bool(clamped),
                'clamped_region': clamped_region,
                'monitor': {'device': mon.device, 'rect': {'left': mon.rect.left, 'top': mon.rect.top, 'right': mon.rect.right, 'bottom': mon.rect.bottom}},
                'output': {'output_idx': int(output_idx)},
                'hint': 'ROI fuera de bounds. Recalibrar ROI con picker.',
                'validation': why,
            },
        )

    # 4) Capture region
    region_tuple = (int(region_x), int(region_y), int(region_x + region_w), int(region_y + region_h))
    crop = cam.grab(region=region_tuple)
    rgb_crop, cw, ch, meta2 = _to_rgb_bytes(crop)
    if not rgb_crop or cw <= 0 or ch <= 0:
        return _hard_fail(
            'capture_black_or_unavailable',
            details={
                'frame_size': [int(w), int(h)],
                'roi': _roi_dict(roi),
                'requested_region': requested_region,
                'region': region_now,
                'region_valid': True,
                'clamp_enabled': bool(clamp_enabled),
                'clamped': bool(clamped),
                'clamped_region': clamped_region,
                'hint': 'ROI fuera de bounds. Recalibrar ROI con picker.',
                'meta': meta2,
            },
        )

    crop_frame = Frame(width=int(cw), height=int(ch), monotonic_ts_ns=int(time.monotonic_ns()), digest_hex='', rgb=rgb_crop)
    dump_frame_ppm(crop_frame, Path('diagnostics/region_crop.ppm'))

    mean, std, all_zero = _sample_luma_stats(rgb_crop, width=int(cw), height=int(ch))

    success = (int(cw) > 0 and int(ch) > 0 and (not bool(all_zero)) and float(std) > 5.0)

    suggested = None
    if (not success) and (not bool(full_all_zero)) and float(full_std) > 5.0 and bool(all_zero):
        # Full frame contains signal, but this ROI is landing on a black patch.
        # Suggest a few candidate regions to speed up ROI recalibration.
        suggested = _suggest_regions(
            rgb24,
            frame_w=int(w),
            frame_h=int(h),
            region_w=int(region_w),
            region_h=int(region_h),
            step=48,
            limit=6,
        )

    payload = {
        'ok': True,
        'success': bool(success),
        'frame_size': [int(w), int(h)],
        'roi': _roi_dict(roi),
        'region_valid': True,
        'clamped': bool(clamped),
        'crop_size': [int(cw), int(ch)],
        'full_mean_luma': float(full_mean),
        'full_std_luma': float(full_std),
        'full_all_zero': bool(full_all_zero),
        'mean_luma': float(mean),
        'std_luma': float(std),
        'all_zero': bool(all_zero),
        'hwnd': int(hwnd),
        'rect_client': {'left': int(rect_client.left), 'top': int(rect_client.top), 'right': int(rect_client.right), 'bottom': int(rect_client.bottom)},
        'monitor': {'device': str(mon.device), 'rect': {'left': int(mon.rect.left), 'top': int(mon.rect.top), 'right': int(mon.rect.right), 'bottom': int(mon.rect.bottom)}},
        'output': {'output_idx': int(output_idx), 'name': str(output_name)},
        'win_offset_in_output': {'x': int(win_x), 'y': int(win_y)},
        'requested_region': requested_region,
        'region': region_now,
        'suggested_regions': suggested,
        'dxcam_probe': dxcam_probe if bool(args.try_all_outputs) else None,
    }

    print(json.dumps(payload, ensure_ascii=False))

    # 5) Success criteria
    if not success:
        # Evidence-rich failure: write fatal.log for CI/automation.
        write_fatal(
            'capture_black_or_unavailable',
            PreflightFailed('capture_black_or_unavailable'),
            details={
                'frame_size': [int(w), int(h)],
                'roi': _roi_dict(roi),
                'requested_region': requested_region,
                'region': region_now,
                'monitor': {'device': str(mon.device), 'rect': {'left': int(mon.rect.left), 'top': int(mon.rect.top), 'right': int(mon.rect.right), 'bottom': int(mon.rect.bottom)}},
                'output': {'output_idx': int(output_idx), 'name': str(output_name)},
                'rect_client': {'left': int(rect_client.left), 'top': int(rect_client.top), 'right': int(rect_client.right), 'bottom': int(rect_client.bottom)},
                'full': {'mean_luma': float(full_mean), 'std_luma': float(full_std), 'all_zero': bool(full_all_zero)},
                'crop': {'mean_luma': float(mean), 'std_luma': float(std), 'all_zero': bool(all_zero)},
                'dxcam_probe': dxcam_probe if bool(args.try_all_outputs) else None,
                'hint': 'Si full y crop salen all_zero/STD≈0, el backend está bloqueado (overlay/exclusive fullscreen/protección). Prueba modo borderless/windowed o una salida distinta con --try-all-outputs.',
            },
        )
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
