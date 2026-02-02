from __future__ import annotations

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

from adapters.windows import win32 as w32
from contracts.capture import Frame
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from runtime.runner import _load_config_from_env
from runtime.preflight import preflight
from runtime.minimap_semantics import marker_config_from_env


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _maybe_focus_projector_window() -> None:
    if not _env_bool('FRBOT_PROJECTOR_FOCUS_ON_START', False):
        return

    timeout_s = max(0.0, min(10.0, _env_float('FRBOT_PROJECTOR_FOCUS_TIMEOUT_S', 2.0)))
    deadline = time.monotonic() + float(timeout_s)

    hwnd = 0
    raw_hwnd = (os.environ.get('FRBOT_PROJECTOR_WINDOW_HWND', '') or '').strip()
    if raw_hwnd:
        try:
            hwnd = int(raw_hwnd, 0)
        except Exception:
            hwnd = 0

    if hwnd <= 0:
        title = (os.environ.get('FRBOT_PROJECTOR_WINDOW_TITLE', '') or '').strip()
        if title:
            try:
                match = w32.find_window_by_title_substring(str(title))
                if match is not None:
                    hwnd = int(match.hwnd)
            except Exception:
                hwnd = 0

    if hwnd <= 0:
        return

    while True:
        _ = w32.try_focus_window(int(hwnd))
        try:
            if int(w32.get_foreground_window()) == int(hwnd):
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return
        time.sleep(0.05)


def main() -> int:
    # Ensure diagnostics dir exists for fatal evidence.
    diagnostics_dir = REPO_ROOT / 'diagnostics'
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    # Make relative config paths deterministic regardless of cwd.
    cfg_env = (os.environ.get('FRBOT_CONFIG_PATH', '') or '').strip()
    if cfg_env and not Path(cfg_env).is_absolute():
        candidate = (REPO_ROOT / cfg_env).resolve()
        if candidate.exists():
            os.environ['FRBOT_CONFIG_PATH'] = str(candidate)

    # Force real mode preflight.
    os.environ['FRBOT_MODE'] = 'real'

    # Best-effort focusing to satisfy the strict foreground invariant.
    _maybe_focus_projector_window()

    try:
        cfg = _load_config_from_env()
        # Make sure we operate in real mode.
        cfg = replace(cfg, mode='real')
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT, reason=''),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = preflight(ctx)

        payload = {
            'ok': True,
            'gate': 'preflight',
            'mode': 'real',
            'capture_backend': str(getattr(capture, 'name', '')),
            'input_backend': str(getattr(input_, 'name', '')),
            'bound_hwnd': hex(int(getattr(binding.snapshot(), 'hwnd', 0))),
            'config_path': str(ctx.config.config_path),
            'minimap_roi': str(ctx.config.minimap_roi),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    except PreflightFailed as exc:
        # Best-effort evidence for common minimap failures (helps tune marker params).
        try:
            cfg = _load_config_from_env()
            cfg = replace(cfg, mode='real')
            ctx = RuntimeContext(
                config=cfg,
                status=RuntimeStatus(state=RuntimeState.PREFLIGHT, reason=''),
                telemetry=RuntimeTelemetry(),
            )

            # Import locally to avoid pulling these deps unless needed.
            from adapters.capture.cam_real import CamMinimapRealCapture
            from adapters.capture.meld_projector_real import MeldProjectorMinimapRealCapture
            from adapters.capture.meld_real import MeldBoundMinimapRealCapture
            from adapters.capture.mss_bound_window_real import MssBoundMinimapRealCapture
            from adapters.window.win32 import Win32WindowBinding
            from runtime.config_loader import load_rois

            backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()

            binding_hwnd = int(ctx.config.window_hwnd)
            binding_title = ctx.config.window_title_substring
            if backend in {'projector', 'meld-projector', 'obs-projector'}:
                raw_hwnd = (os.environ.get('FRBOT_PROJECTOR_WINDOW_HWND', '') or '').strip()
                if raw_hwnd:
                    try:
                        binding_hwnd = int(raw_hwnd, 0)
                    except Exception:
                        pass
                raw_title = (os.environ.get('FRBOT_PROJECTOR_WINDOW_TITLE', '') or '').strip()
                if raw_title:
                    binding_title = raw_title

            binding = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)
            if binding.verify().ok:
                _maybe_focus_projector_window()
                binding.assert_bound()

                loaded = load_rois(ctx)
                ctx.rois = dict(loaded.rois)
                minimap_roi = ctx.rois.get(ctx.config.minimap_roi)

                if minimap_roi is not None:
                    if backend == 'meld':
                        capture = MeldBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)
                    elif backend in {'obs-projector', 'projector', 'meld-projector'}:
                        capture = MeldProjectorMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)
                    elif backend in {'cam', 'obs', 'virtualcam'}:
                        capture = CamMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)
                    else:
                        capture = MssBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)

                    f = capture.grab()
                    dump_frame_ppm(f, diagnostics_dir / 'preflight_failure_full.ppm')
                    if bool(getattr(f, 'minimap_detected', False)) and len(getattr(f, 'minimap_rgb', b'')):
                        mm = Frame(
                            width=int(f.minimap_width),
                            height=int(f.minimap_height),
                            monotonic_ts_ns=0,
                            digest_hex='',
                            rgb=bytes(f.minimap_rgb),
                        )
                        dump_frame_ppm(mm, diagnostics_dir / 'preflight_failure_minimap.ppm')

                        cfgm = marker_config_from_env(
                            ctx.config.player_marker_rgb,
                            str(ctx.config.player_marker_tol),
                            str(ctx.config.player_marker_min_pixels),
                            str(ctx.config.player_marker_max_pixels),
                            str(ctx.config.player_marker_min_fill_ratio),
                            str(ctx.config.player_marker_max_aspect_ratio),
                        )
                        w = int(f.minimap_width)
                        h = int(f.minimap_height)
                        src = bytes(f.minimap_rgb)
                        tr, tg, tb = cfgm.rgb
                        tol = int(cfgm.tol)

                        # Mask: white pixels are matches to the configured marker RGB/tolerance.
                        mask = bytearray(w * h * 3)
                        for pix in range(w * h):
                            i = pix * 3
                            r = src[i]
                            g = src[i + 1]
                            b = src[i + 2]
                            ok = abs(int(r) - tr) <= tol and abs(int(g) - tg) <= tol and abs(int(b) - tb) <= tol
                            v = 255 if ok else 0
                            mask[i] = v
                            mask[i + 1] = v
                            mask[i + 2] = v
                        mm_mask = Frame(width=w, height=h, monotonic_ts_ns=0, digest_hex='', rgb=bytes(mask))
                        dump_frame_ppm(mm_mask, diagnostics_dir / 'preflight_failure_marker_mask.ppm')

        except Exception:
            # Evidence is best-effort; never mask the primary failure.
            pass

        details = {
            'ok': False,
            'gate': 'preflight',
            'reason': str(exc) or 'preflight_failed',
            'evidence_dir': str(diagnostics_dir),
        }
        write_fatal(str(exc) or 'preflight_failed', exc, details=details)
        print(json.dumps(details, ensure_ascii=False))
        return 1
    except Exception as exc:
        details = {
            'ok': False,
            'gate': 'preflight',
            'reason': f'{type(exc).__name__}: {exc}',
        }
        write_fatal('runtime crashed', exc, details=details)
        print(json.dumps(details, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
