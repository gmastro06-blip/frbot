from __future__ import annotations

import argparse
import os
from pathlib import Path

from adapters.window.win32 import Win32WindowBinding
from contracts.errors import PreflightFailed
from runtime.config_loader import load_rois
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env
from adapters.capture.mss_real import MssRealCapture


def _write_ppm(path: Path, *, w: int, h: int, rgb: bytes) -> None:
    header = f"P6\n{w} {h}\n255\n".encode('ascii')
    path.write_bytes(header + rgb)


def _write_pgm_mask(path: Path, *, w: int, h: int, rgb: bytes, target_rgb: tuple[int, int, int], tol: int) -> None:
    tr, tg, tb = target_rgb
    out = bytearray(w * h)
    for pix in range(w * h):
        i = pix * 3
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        ok = abs(int(r) - tr) <= tol and abs(int(g) - tg) <= tol and abs(int(b) - tb) <= tol
        out[pix] = 255 if ok else 0
    header = f"P5\n{w} {h}\n255\n".encode('ascii')
    path.write_bytes(header + bytes(out))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Calibrate minimap marker detection (real mode).')
    p.add_argument('--window-title', default=os.environ.get('FRBOT_WINDOW_TITLE', ''), help='Substring of the target window title')
    p.add_argument('--hwnd', type=int, default=int(os.environ.get('FRBOT_WINDOW_HWND', '0') or '0'), help='Target HWND (overrides title search)')
    p.add_argument('--config', default=os.environ.get('FRBOT_CONFIG_PATH', ''), help='ROI config path (must contain minimap ROI)')
    p.add_argument('--minimap-roi', default=os.environ.get('FRBOT_MINIMAP_ROI', 'minimap'), help='ROI name for minimap')
    p.add_argument('--out-dir', default='diagnostics', help='Where to write dumps')

    args = p.parse_args(argv)

    if not args.hwnd and not args.window_title:
        raise SystemExit('Need --hwnd or --window-title (or FRBOT_WINDOW_HWND/FRBOT_WINDOW_TITLE).')

    # Resolve and bind.
    binding = Win32WindowBinding(hwnd=int(args.hwnd), title_substring=str(args.window_title))
    vr = binding.verify()
    if not vr.ok:
        raise SystemExit('window_binding_lost')

    snap = binding.snapshot()
    region = {
        'left': int(snap.rect.left),
        'top': int(snap.rect.top),
        'width': int(snap.rect.width),
        'height': int(snap.rect.height),
    }

    # Load ROIs.
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeStatus, RuntimeState, RuntimeTelemetry

    ctx = RuntimeContext(
        config=RuntimeConfig(mode='real', config_path=str(args.config), minimap_roi=str(args.minimap_roi)),
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )
    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)
    roi = ctx.rois.get(ctx.config.minimap_roi)
    if roi is None:
        raise SystemExit('minimap_not_detected')

    cap = MssRealCapture(minimap_roi=roi, region=region)
    cvr = cap.verify()
    if not cvr.ok:
        raise SystemExit(cvr.reason or 'capture not verified')

    frame = cap.grab()
    if not frame.minimap_detected:
        raise SystemExit('minimap_not_detected')

    cfg = marker_config_from_env(
        os.environ.get('FRBOT_PLAYER_MARKER_RGB', '255,0,255'),
        os.environ.get('FRBOT_PLAYER_MARKER_TOL', '30'),
        os.environ.get('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5'),
        os.environ.get('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0'),
        os.environ.get('FRBOT_PLAYER_MARKER_MIN_FILL_RATIO', '0.15'),
        os.environ.get('FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO', '4.0'),
    )

    det = detect_player_marker(frame, cfg)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ppm_path = out_dir / 'minimap.ppm'
    _write_ppm(ppm_path, w=int(frame.minimap_width), h=int(frame.minimap_height), rgb=frame.minimap_rgb)
    mask_path = out_dir / 'minimap_mask.pgm'
    _write_pgm_mask(mask_path, w=int(frame.minimap_width), h=int(frame.minimap_height), rgb=frame.minimap_rgb, target_rgb=cfg.rgb, tol=int(cfg.tol))

    print(f"Bound HWND: {snap.hwnd} rect={snap.rect.left},{snap.rect.top},{snap.rect.width}x{snap.rect.height}")
    print(f"Minimap ROI: {roi.x},{roi.y} {roi.width}x{roi.height}")
    print(f"Wrote: {ppm_path}")
    print(f"Wrote: {mask_path}")

    if det is None:
        print('DETECTION: NOT FOUND')
        return 1

    print('DETECTION: FOUND')
    print(f"- centroid_px=({det.pos.px:.2f},{det.pos.py:.2f})")
    print(f"- pixels={det.pixel_count}")
    print(f"- bbox=({det.bbox_left},{det.bbox_top})..({det.bbox_right},{det.bbox_bottom})")
    print(f"- fill={det.fill_ratio:.3f} aspect={det.aspect_ratio:.3f}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
