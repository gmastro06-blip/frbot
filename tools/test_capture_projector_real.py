from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, TypedDict

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.capture.meld_real import _sample_luma_stats
from adapters.window.win32 import Win32WindowBinding
from adapters.windows import win32 as w32
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from runtime.config_loader import load_rois
from runtime.runner import _load_config_from_env
from runtime.env import parse_window_hwnd_env


class _JsonStdoutCtx(TypedDict):
    saved_out: int
    saved_err: int


@contextlib.contextmanager
def _json_stdout_only(enabled: bool) -> Iterator[_JsonStdoutCtx | None]:
    """Silence all stdout/stderr, but allow emitting an exact JSON line.

    dxcam can print warnings directly to stdout/stderr fds, which pollutes
    redirected output files. We silence fds 1/2 for the duration of the run,
    and write our JSON payload to a dup() of the original fd1.
    """

    if not enabled:
        yield None
        return

    devnull_fd: int | None = None
    saved_out: int | None = None
    saved_err: int | None = None
    try:
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield {'saved_out': saved_out, 'saved_err': saved_err}
    finally:
        # Do NOT restore stdout/stderr here: late prints from third-party libs
        # (including during interpreter teardown) would pollute redirected output.
        for fd in (devnull_fd, saved_out, saved_err):
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass


_JSON_CTX: _JsonStdoutCtx | None = None


def _emit_json(payload: object) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    ctx = _JSON_CTX
    if ctx is not None:
        try:
            os.write(int(ctx['saved_out']), (line + '\n').encode('utf-8', errors='replace'))
            return
        except Exception:
            pass
    print(line)


def _hard_fail(reason: str, *, details: dict) -> int:
    exc = PreflightFailed(reason)
    setattr(exc, 'details', details)
    write_fatal(reason, exc, details=details)
    _emit_json({'ok': False, 'reason': reason, 'details': details})
    return 2


def _hard_fail_binding_lost(*, expected_hwnd: int) -> int:
    fg_hwnd = 0
    fg_title = ''
    fg_error: str | None = None
    try:
        fg_hwnd = int(w32.get_foreground_window())
        fg_title = str(w32.get_window_text(int(fg_hwnd)) or '') if fg_hwnd > 0 else ''
    except Exception as fg_exc:
        fg_hwnd = 0
        fg_title = ''
        fg_error = f'{type(fg_exc).__name__}: {fg_exc}'

    expected_title = ''
    expected_is_window = False
    expected_visible = False
    expected_minimized = False
    try:
        if int(expected_hwnd) > 0:
            expected_is_window = bool(w32.is_window(int(expected_hwnd)))
        if expected_is_window:
            expected_title = str(w32.get_window_text(int(expected_hwnd)) or '')
            expected_visible = bool(w32.is_window_visible(int(expected_hwnd)))
            expected_minimized = bool(w32.is_window_minimized(int(expected_hwnd)))
    except Exception:
        pass

    payload = {
        'reason': 'window_binding_lost',
        'expected_hwnd': hex(int(expected_hwnd)),
        'expected_title': expected_title,
        'expected_is_window': bool(expected_is_window),
        'expected_visible': bool(expected_visible),
        'expected_minimized': bool(expected_minimized),
        'foreground_hwnd': hex(int(fg_hwnd)),
        'foreground_title': fg_title,
        'foreground_error': fg_error,
    }
    exc = PreflightFailed('window_binding_lost')
    setattr(exc, 'details', payload)
    write_fatal('window_binding_lost', exc, details=payload)
    _emit_json(payload)
    return 2


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _maybe_focus_expected(hwnd: int) -> None:
    if int(hwnd) <= 0:
        return
    if not _env_bool('FRBOT_PROJECTOR_FOCUS_ON_START', False):
        return
    try:
        timeout_s_raw = (os.environ.get('FRBOT_PROJECTOR_FOCUS_TIMEOUT_S', '2.0') or '2.0').strip()
        try:
            timeout_s = float(timeout_s_raw)
        except Exception:
            timeout_s = 2.0
        timeout_s = max(0.0, min(10.0, float(timeout_s)))

        deadline = time.monotonic() + float(timeout_s)
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
    except Exception:
        pass


def _probe_dxcam_outputs(*, max_outputs: int = 6) -> tuple[int | None, list[dict[str, object]], object | None]:
    """Probe multiple dxcam outputs and pick the one with most non-black signal."""

    probe: list[dict[str, object]] = []
    best_idx: int | None = None
    best_std = -1.0
    best_cam: object | None = None

    try:
        import dxcam  # type: ignore
    except Exception as exc:
        probe.append({'output_idx': None, 'ok': False, 'error': f'dxcam_import_failed: {type(exc).__name__}: {exc}'})
        return None, probe, None

    try:
        from adapters.capture.meld_real import _to_rgb_bytes  # type: ignore
    except Exception as exc:
        probe.append({'output_idx': None, 'ok': False, 'error': f'meld_helpers_unavailable: {type(exc).__name__}: {exc}'})
        return None, probe, None

    for idx in range(max(1, int(max_outputs))):
        cam: Any | None = None
        try:
            cam = dxcam.create(output_idx=int(idx))
        except Exception as exc:
            probe.append({'output_idx': int(idx), 'ok': False, 'error': f'create_failed: {type(exc).__name__}: {exc}'})
            continue

        if cam is None:
            probe.append({'output_idx': int(idx), 'ok': False, 'error': 'create_returned_none'})
            continue

        try:
            frame = cam.grab()
            rgb, w, h, meta = _to_rgb_bytes(frame)
            if not rgb or int(w) <= 0 or int(h) <= 0:
                probe.append({'output_idx': int(idx), 'ok': False, 'frame_size': [int(w), int(h)], 'meta': meta, 'error': 'frame_empty'})
                continue
            mean, std, all_zero = _sample_luma_stats(rgb, width=int(w), height=int(h))
            item = {
                'output_idx': int(idx),
                'ok': True,
                'frame_size': [int(w), int(h)],
                'meta': meta,
                'mean': float(mean),
                'std': float(std),
                'all_zero': bool(all_zero),
            }
            probe.append(item)
            # Prefer highest std among non-all-zero frames.
            if (not all_zero) and float(std) > float(best_std):
                best_std = float(std)
                best_idx = int(idx)
                best_cam = cam
                cam = None
        except Exception as exc:
            probe.append({'output_idx': int(idx), 'ok': False, 'error': f'grab_failed: {type(exc).__name__}: {exc}'})
        finally:
            if cam is not None:
                try:
                    stop = getattr(cam, 'stop', None)
                    if callable(stop):
                        stop()
                except Exception:
                    pass

    return best_idx, probe, best_cam


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Diagnostic: capture OBS projector via dxcam')
    ap.add_argument('--hwnd', default='', help='Target HWND (decimal or hex like 0x1234). Optional; env/config may provide it.')
    ap.add_argument('--window-title', default='', help='Substring of the window title to bind/verify (overrides config/env).')
    ap.add_argument('--config', default='', help='Path to ROI config JSON (top-level {"rois": {...}}). If omitted, uses FRBOT_CONFIG_PATH.')
    ap.add_argument('--wait-seconds', type=int, default=5, help='Seconds to wait before grabbing (gives you time to focus Tibia)')
    ap.add_argument('--frames', type=int, default=2, help='Number of frames to dump')
    ap.add_argument('--out-dir', default='diagnostics/projector', help='Output directory for dumped frames')
    ap.add_argument('--try-all-outputs', action='store_true', help='Probe multiple monitors/outputs and pick the best automatically')
    ap.add_argument('--max-outputs', type=int, default=6, help='Max outputs to probe when --try-all-outputs is set')
    args = ap.parse_args(argv)

    # Default to JSON-only stdout to avoid dxcam noise polluting output.
    global _JSON_CTX
    raw = (os.environ.get('FRBOT_PROJECTOR_JSON_ONLY', '1') or '1').strip().lower()
    json_only = raw not in {'', '0', 'false', 'no', 'off'}
    with _json_stdout_only(bool(json_only)) as ctx:
        _JSON_CTX = ctx
        return _main_inner(args)


def _main_inner(args: argparse.Namespace) -> int:

    # Force backend expectation.
    Path('diagnostics').mkdir(parents=True, exist_ok=True)
    # Support requested env contract:
    # - FRBOT_CAPTURE_BACKEND=dxcam
    # - FRBOT_CAPTURE_TARGET=projector
    # Map it internally to obs-projector.
    backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'projector') or 'projector').strip().lower()
    capture_target = (os.environ.get('FRBOT_CAPTURE_TARGET', '') or '').strip().lower()
    if backend == 'dxcam' and capture_target == 'projector':
        backend = 'obs-projector'
    if backend not in {'projector', 'meld-projector', 'obs-projector'}:
        return _hard_fail('capture_black_or_unavailable', details={'expected_backend': 'projector', 'got': backend})

    cfg = _load_config_from_env()
    if str(args.config).strip():
        cfg = replace(cfg, config_path=str(args.config).strip())
    if str(args.hwnd).strip():
        try:
            cfg = replace(cfg, window_hwnd=int(str(args.hwnd), 0))
        except Exception:
            return _hard_fail('window_hwnd_invalid', details={'hwnd': str(args.hwnd)})

    # If FRBOT_CAPTURE_TITLE is provided, treat it as projector window title substring.
    # (Does not override if FRBOT_WINDOW_HWND is set.)
    capture_title = (os.environ.get('FRBOT_CAPTURE_TITLE', '') or '').strip()
    if capture_title:
        cfg = replace(cfg, window_title_substring=capture_title)

    ctx = RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT, reason=''),
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
        return _hard_fail('minimap_not_detected', details={'minimap_roi': str(ctx.config.minimap_roi)})

    if int(args.wait_seconds) > 0:
        time.sleep(int(args.wait_seconds))

    # If requested (or if no explicit output idx is set), probe all outputs and pick the best.
    output_idx_raw = (os.environ.get('FRBOT_PROJECTOR_OUTPUT_IDX', '') or '').strip()
    if not output_idx_raw:
        output_idx_raw = (os.environ.get('FRBOT_MELD_OUTPUT_IDX', '') or '').strip()  # legacy
    want_probe = bool(args.try_all_outputs) or (not output_idx_raw) or _env_bool('FRBOT_TRY_ALL_OUTPUTS', False)
    dxcam_probe: list[dict[str, object]] = []
    selected_output_idx: int | None = None
    selected_cam: object | None = None
    if want_probe:
        best_idx, dxcam_probe, selected_cam = _probe_dxcam_outputs(max_outputs=int(args.max_outputs))
        selected_output_idx = best_idx
        if best_idx is not None:
            os.environ['FRBOT_PROJECTOR_OUTPUT_IDX'] = str(int(best_idx))

    # Deterministic binding rule:
    # If FRBOT_WINDOW_HWND is set to a real value -> do NOT search by title.
    # If it's a common placeholder (0xXXXXXXXX), treat as unset (same as runtime).
    expected_hwnd = 0
    try:
        expected_hwnd = int(parse_window_hwnd_env('FRBOT_WINDOW_HWND'))
    except PreflightFailed as exc:
        # Keep diagnostic tool behavior: hard-fail with a canonical reason.
        return _hard_fail(str(exc) or 'window_hwnd_invalid', details={'hwnd': str(os.environ.get('FRBOT_WINDOW_HWND') or '')})

    if expected_hwnd > 0:
        binding = Win32WindowBinding(hwnd=int(expected_hwnd), title_substring='')
    else:
        # Projector mode can bind to OBS Projector window title/HWND (separate from Tibia).
        proj_hwnd_raw = (os.environ.get('FRBOT_PROJECTOR_WINDOW_HWND', '') or '').strip()
        proj_title_raw = (os.environ.get('FRBOT_PROJECTOR_WINDOW_TITLE', '') or '').strip()

        binding_hwnd = int(cfg.window_hwnd)
        if proj_hwnd_raw:
            try:
                binding_hwnd = int(proj_hwnd_raw, 0)
            except Exception:
                pass

        binding_title = str(cfg.window_title_substring or '')
        if proj_title_raw:
            binding_title = proj_title_raw
        if (args.window_title or '').strip():
            binding_title = str(args.window_title)

        expected_hwnd = int(binding_hwnd)
        binding = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)

        # If binding is by title (HWND=0), resolve a deterministic expected HWND for diagnostics/focus.
        if int(expected_hwnd) <= 0:
            try:
                snap = binding.snapshot()
                if int(getattr(snap, 'hwnd', 0)) > 0:
                    expected_hwnd = int(getattr(snap, 'hwnd', 0))
            except Exception:
                pass
    # For projector diagnostics we often don't want to steal focus.
    # If foreground requirement is disabled, only require that the window exists/resolves.
    require_fg = (os.environ.get('FRBOT_PROJECTOR_REQUIRE_FOREGROUND', '1') or '1').strip().lower() not in {'', '0', 'false', 'no', 'off'}
    if require_fg:
        _maybe_focus_expected(int(expected_hwnd))
        bvr = binding.verify()
        if not bvr.ok:
            return _hard_fail_binding_lost(expected_hwnd=int(expected_hwnd))
    else:
        # Ensure we can resolve the HWND at least once.
        if int(cfg.window_hwnd) <= 0 and not (cfg.window_title_substring or '').strip():
            return _hard_fail(
                'window_binding_lost',
                details={
                    'reason': 'missing_hwnd_and_title',
                    'hint': 'For projector mode, set FRBOT_PROJECTOR_WINDOW_TITLE="Proyector en ventana" (substring) or FRBOT_PROJECTOR_WINDOW_HWND=0x..., then rerun. You can list windows via: poetry run python tools/test_capture_real.py --list-windows --filter Proyector',
                },
            )
        bvr = binding.verify()  # may fail on foreground, but will still set a useful reason.
        # Try a snapshot to resolve existence (no foreground needed).
        try:
            _ = binding.snapshot()
        except Exception as exc:
            return _hard_fail('window_binding_lost', details={'reason': f'{type(exc).__name__}: {exc}'})

    from adapters.capture.meld_projector_real import MeldProjectorMinimapRealCapture

    try:
        cap = MeldProjectorMinimapRealCapture(
            minimap_roi=roi,
            binding=binding,
            dxcam_cam=selected_cam,
            output_idx=(int(selected_output_idx) if selected_output_idx is not None else None),
        )
    except ImportError as exc:
        return _hard_fail('capture_black_or_unavailable', details={'error': str(exc)})

    vr = cap.verify()
    if not vr.ok:
        # Spec: if validation fails -> abort reason="obs_projector_invalid_capture".
        return _hard_fail(
            'obs_projector_invalid_capture',
            details={
                'backend': backend,
                'verify_reason': str(vr.reason or ''),
                'selected_output_idx': (int(selected_output_idx) if selected_output_idx is not None else None),
                'output_idx_env': (os.environ.get('FRBOT_PROJECTOR_OUTPUT_IDX', '') or os.environ.get('FRBOT_MELD_OUTPUT_IDX', '') or ''),
                'dxcam_probe': dxcam_probe,
            },
        )

    out_dir = Path(str(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    dumps: list[dict[str, object]] = []
    last_full = None
    for i in range(max(1, int(args.frames))):
        # Hard security invariant: projector HWND must be foreground before every grab.
        if require_fg:
            try:
                binding.assert_bound()
            except Exception:
                return _hard_fail_binding_lost(expected_hwnd=int(expected_hwnd))

        fr = cap.grab()
        last_full = fr

        full_path = out_dir / f'projector_full_{i}.ppm'
        dump_frame_ppm(fr, full_path)

        mini_path = out_dir / f'projector_minimap_{i}.ppm'
        if fr.minimap_detected:
            from contracts.capture import Frame as CFrame

            mm = CFrame(
                width=int(fr.minimap_width),
                height=int(fr.minimap_height),
                monotonic_ts_ns=0,
                digest_hex='',
                rgb=bytes(fr.minimap_rgb),
            )
            dump_frame_ppm(mm, mini_path)

        mean, std, all_zero = _sample_luma_stats(fr.rgb, width=int(fr.width), height=int(fr.height))
        dumps.append({'i': int(i), 'full': str(full_path), 'minimap': str(mini_path), 'full_stats': {'std_luma': float(std), 'all_zero': bool(all_zero)}})
        time.sleep(0.1)

    # Required evidence: diagnostics/obs_projector_full.ppm
    if last_full is not None:
        dump_frame_ppm(last_full, Path('diagnostics/obs_projector_full.ppm'))

    # Keep monitor label as best-effort only; avoid re-creating dxcam cameras here.
    monitor_label = 'unknown'

    window_title = (
        (os.environ.get('FRBOT_PROJECTOR_WINDOW_TITLE', '') or '').strip()
        or (args.window_title or '').strip()
        or str(cfg.window_title_substring or '').strip()
    )

    required = {
        'source': 'OBS Projector',
        'window_title': window_title or 'Proyector en ventana (Fuente) - Tibia_Fuente',
        'monitor': monitor_label,
        'validated': True,
    }
    Path('diagnostics/obs_projector.json').write_text(json.dumps(required, ensure_ascii=False, indent=2), encoding='utf-8')

    # Print EXACT required JSON.
    _emit_json(required)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
