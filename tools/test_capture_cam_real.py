from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.capture.meld_real import _sample_luma_stats
from adapters.window.win32 import Win32WindowBinding
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from runtime.config_loader import load_rois
from runtime.runner import _load_config_from_env
from runtime.pacing import sleep_ms


def _hard_fail(reason: str, *, details: dict) -> int:
    exc = PreflightFailed(reason)
    setattr(exc, "details", details)
    write_fatal(reason, exc, details=details)
    print(json.dumps({"ok": False, "reason": reason, "details": details}, ensure_ascii=False))
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diagnostic: capture via OBS VirtualCam / camera backend")
    ap.add_argument("--hwnd", required=False, default="", help="Target HWND (decimal or hex like 0x1234). Optional; env/config may provide it.")
    ap.add_argument("--config", default="", help="Path to ROI config JSON (top-level {'rois': {...}}). If omitted, uses FRBOT_CONFIG_PATH.")
    ap.add_argument("--wait-seconds", type=int, default=10, help="Time to allow focusing the window before capture")
    ap.add_argument("--frames", type=int, default=2, help="Number of frames to dump")
    ap.add_argument("--out-dir", default="diagnostics/cam", help="Output directory for dumped frames")
    args = ap.parse_args(argv)

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        write_fatal('feature_disabled', details={'tool': 'test_capture_cam_real', 'profile': profile})
        print(json.dumps({'ok': False, 'reason': 'feature_disabled', 'details': {'tool': 'test_capture_cam_real', 'profile': profile}}, ensure_ascii=False))
        return 2

    cfg = _load_config_from_env()
    if str(args.config).strip():
        cfg = replace(cfg, config_path=str(args.config).strip())
    if str(args.hwnd).strip():
        try:
            cfg = replace(cfg, window_hwnd=int(str(args.hwnd), 0))
        except Exception:
            return _hard_fail("window_hwnd_invalid", details={"hwnd": str(args.hwnd)})

    ctx = RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT, reason=""),
        telemetry=RuntimeTelemetry(),
    )

    try:
        loaded = load_rois(ctx)
        ctx.rois = dict(loaded.rois)
    except PreflightFailed as exc:
        return _hard_fail(
            str(exc) or 'config_invalid_schema',
            details={
                'config_path': str(ctx.config.config_path or ''),
                'hint': 'Set FRBOT_CONFIG_PATH or pass --config .\\rois_15y.json (must be {"rois": {...}}).',
            },
        )
    roi = ctx.rois.get(ctx.config.minimap_roi)
    if roi is None:
        return _hard_fail("minimap_not_detected", details={"minimap_roi": str(ctx.config.minimap_roi)})

    if int(args.wait_seconds) > 0:
        print(json.dumps({"ok": True, "note": "waiting", "seconds": int(args.wait_seconds)}, ensure_ascii=False))
        sleep_ms(float(int(args.wait_seconds)) * 1000.0)

    binding = Win32WindowBinding(hwnd=int(cfg.window_hwnd), title_substring=cfg.window_title_substring)
    require_fg = (os.environ.get('FRBOT_CAM_REQUIRE_FOREGROUND', '1') or '1').strip().lower() not in {'', '0', 'false', 'no', 'off'}
    if require_fg:
        bvr = binding.verify()
        if not bvr.ok:
            return _hard_fail("window_binding_lost", details={"reason": bvr.reason})
    else:
        if int(cfg.window_hwnd) <= 0 and not (cfg.window_title_substring or '').strip():
            return _hard_fail(
                'window_binding_lost',
                details={
                    'reason': 'missing_hwnd_and_title',
                    'hint': 'Set FRBOT_WINDOW_TITLE=Tibia (substring) or pass --hwnd 0x..., then rerun.',
                },
            )
        try:
            _ = binding.snapshot()
        except Exception as exc:
            return _hard_fail('window_binding_lost', details={'reason': f'{type(exc).__name__}: {exc}'})

    backend = (os.environ.get("FRBOT_CAPTURE_BACKEND", "cam") or "cam").strip().lower()
    if backend not in {"cam", "obs", "virtualcam"}:
        return _hard_fail("capture_black_or_unavailable", details={"expected_backend": "cam", "got": backend})

    from adapters.capture.cam_real import CamMinimapRealCapture

    try:
        cap = CamMinimapRealCapture(minimap_roi=roi, binding=binding)
    except ImportError as exc:
        return _hard_fail("capture_black_or_unavailable", details={"error": str(exc)})

    vr = cap.verify()
    if not vr.ok:
        return _hard_fail(vr.reason or "capture_black_or_unavailable", details={"backend": backend})

    out_dir = Path(str(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    dumps: list[dict[str, object]] = []
    for i in range(max(1, int(args.frames))):
        fr = cap.grab()
        full_path = out_dir / f"cam_full_{i}.ppm"
        mini_path = out_dir / f"cam_minimap_{i}.ppm"
        dump_frame_ppm(fr, full_path)
        if fr.minimap_detected:
            from contracts.capture import Frame as CFrame

            mm = CFrame(width=int(fr.minimap_width), height=int(fr.minimap_height), monotonic_ts_ns=0, digest_hex="", rgb=bytes(fr.minimap_rgb))
            dump_frame_ppm(mm, mini_path)

        mean, std, all_zero = _sample_luma_stats(fr.rgb, width=int(fr.width), height=int(fr.height))
        mmean, mstd, mz = (0.0, 0.0, True)
        if fr.minimap_detected:
            mmean, mstd, mz = _sample_luma_stats(fr.minimap_rgb, width=int(fr.minimap_width), height=int(fr.minimap_height))

        dumps.append(
            {
                "i": int(i),
                "full": {"path": str(full_path), "mean": float(mean), "std": float(std), "all_zero": bool(all_zero), "size": [int(fr.width), int(fr.height)]},
                "minimap": {
                    "detected": bool(fr.minimap_detected),
                    "path": str(mini_path),
                    "mean": float(mmean),
                    "std": float(mstd),
                    "all_zero": bool(mz),
                    "size": [int(fr.minimap_width), int(fr.minimap_height)],
                },
            }
        )

        sleep_ms(100.0)

    print(json.dumps({"ok": True, "backend": backend, "device_index": int(os.environ.get("FRBOT_CAM_DEVICE_INDEX", "0") or "0"), "dumps": dumps}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
