from __future__ import annotations

import hashlib
import os
import time
import contextlib
from pathlib import Path
from typing import Any

from adapters.windows import win32 as w32
from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair

# Reuse MELD helpers for dxcam interop and exact luma stats.
from adapters.capture.meld_real import (  # type: ignore
    _to_rgb_bytes,
    _dxcam_has_monitors,
    _sample_luma_stats,
    _env_bool,
    _env_int,
)


def _hard_stop(reason: str, *, details: dict[str, object]) -> None:
    exc = PreflightFailed(reason)
    setattr(exc, 'details', details)
    write_fatal(reason, exc, details=details)
    raise exc


@contextlib.contextmanager
def _suppress_output() -> Any:
    """Suppress noisy third-party output (dxcam can write directly to fds 1/2)."""

    devnull_fd: int | None = None
    saved_out: int | None = None
    saved_err: int | None = None
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        try:
            if saved_out is not None:
                os.dup2(saved_out, 1)
        except Exception:
            pass
        try:
            if saved_err is not None:
                os.dup2(saved_err, 2)
        except Exception:
            pass
        for fd in (saved_out, saved_err, devnull_fd):
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass


def _parse_region_env() -> tuple[int, int, int, int] | None:
    """Parse FRBOT_PROJECTOR_REGION as x,y,w,h or x y w h."""

    raw = os.environ.get('FRBOT_PROJECTOR_REGION', '')
    s = str(raw or '').strip()
    if not s:
        return None
    for sep in (',', ' '):
        if sep in s:
            parts = [p for p in s.replace(',', ' ').split() if p]
            if len(parts) != 4:
                return None
            try:
                x, y, w, h = (int(p, 0) for p in parts)
                return int(x), int(y), int(w), int(h)
            except Exception:
                return None
    return None


def _crop_rgb(rgb: bytes, width: int, height: int, roi: Roi) -> bytes:
    if width <= 0 or height <= 0:
        return b''
    if roi.width <= 0 or roi.height <= 0:
        return b''
    if roi.x < 0 or roi.y < 0:
        return b''
    if (roi.x + roi.width) > width or (roi.y + roi.height) > height:
        return b''
    row_stride = width * 3
    out = bytearray(roi.width * roi.height * 3)
    out_row_stride = roi.width * 3
    for row in range(roi.height):
        src_start = ((roi.y + row) * row_stride) + (roi.x * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = rgb[src_start:src_end]
    return bytes(out)


def _dxcam_grab_rgb_with_warmup(
    cam: Any,
    *,
    region: tuple[int, int, int, int] | None,
    attempts: int,
    sleep_s: float,
) -> tuple[bytes, int, int, dict[str, object], list[dict[str, object]]]:
    """Grab a non-empty dxcam frame with a small bounded warm-up.

    Rationale: some systems return None/empty frames immediately after `dxcam.create()`.
    This keeps strict behavior (still hard-fails if empty persists) but avoids flakiness.
    """

    attempts_i = max(1, int(attempts))
    sleep_s_f = max(0.0, float(sleep_s))

    traces: list[dict[str, object]] = []
    last_meta: dict[str, object] = {'reason': 'not_attempted'}

    for i in range(attempts_i):
        try:
            frame = cam.grab() if region is None else cam.grab(region=region)
        except Exception as exc:
            last_meta = {'reason': 'grab_exception', 'error': f'{type(exc).__name__}: {exc}'}
            traces.append({'i': int(i), 'ok': False, 'meta': dict(last_meta)})
            if sleep_s_f:
                try:
                    from runtime.pacing import wait_until_ns

                    wait_until_ns(int(time.monotonic_ns() + int(float(sleep_s_f) * 1_000_000_000)))
                except Exception:
                    pass
            continue

        rgb, w, h, meta = _to_rgb_bytes(frame)
        last_meta = dict(meta)
        ok = bool(rgb and int(w) > 0 and int(h) > 0)
        traces.append({'i': int(i), 'ok': bool(ok), 'w': int(w), 'h': int(h), 'meta': dict(meta)})
        if ok:
            return rgb, int(w), int(h), dict(meta), traces
        if sleep_s_f:
            try:
                from runtime.pacing import wait_until_ns

                wait_until_ns(int(time.monotonic_ns() + int(float(sleep_s_f) * 1_000_000_000)))
            except Exception:
                pass

    return b'', 0, 0, dict(last_meta), traces


class MeldProjectorMinimapRealCapture(CaptureAdapter):
    """REAL capture from OBS Projector (second monitor) via dxcam.

    This does NOT capture the Tibia window directly. Instead, it captures a screen/output
    (the monitor where OBS Projector is shown), and then applies ROIs in that frame space.

    Defaults:
    - output index: FRBOT_PROJECTOR_OUTPUT_IDX (preferred). For backwards compat, FRBOT_MELD_OUTPUT_IDX is also accepted.
    - region: full output, unless FRBOT_PROJECTOR_REGION is provided

    Safety:
    - By default, still requires the bound HWND to be foreground.
      Disable only for diagnostics with FRBOT_PROJECTOR_REQUIRE_FOREGROUND=0.
    """

    # Public/backend identity: user-facing name should not mention "meld".
    name = 'obs-projector'

    def __init__(
        self,
        minimap_roi: Roi,
        *,
        binding: WindowBindingAdapter,
        dxcam_cam: Any | None = None,
        output_idx: int | None = None,
    ) -> None:
        try:
            import dxcam  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: dxcam') from exc

        self._dxcam = dxcam
        self._cam: Any | None = dxcam_cam

        self._binding = binding
        self._minimap_roi = minimap_roi

        desired_idx: int | None = int(output_idx) if output_idx is not None else None
        if desired_idx is None:
            desired_idx = _env_int('FRBOT_PROJECTOR_OUTPUT_IDX')
        if desired_idx is None:
            desired_idx = _env_int('FRBOT_MELD_OUTPUT_IDX')
        if desired_idx is None and _env_bool('FRBOT_TRY_ALL_OUTPUTS', False):
            best_idx, best_cam = self._probe_best_output(max_outputs=int(_env_int('FRBOT_MAX_OUTPUTS') or 6))
            if best_idx is not None:
                desired_idx = int(best_idx)
            if best_cam is not None:
                self._cam = best_cam
        self._output_idx = int(desired_idx or 0)
        self._baseline_done = False

    def _probe_best_output(self, *, max_outputs: int = 6) -> tuple[int | None, Any | None]:
        best_idx: int | None = None
        best_std = -1.0
        best_cam: Any | None = None

        for idx in range(max(1, int(max_outputs))):
            cam: Any | None = None
            try:
                with _suppress_output():
                    cam = self._dxcam.create(output_idx=int(idx))
            except Exception:
                continue

            try:
                frame = cam.grab()
                rgb, w, h, _meta = _to_rgb_bytes(frame)
                if not rgb or int(w) <= 0 or int(h) <= 0:
                    continue
                _mean, std, all_zero = _sample_luma_stats(rgb, width=int(w), height=int(h))
                if (not all_zero) and float(std) > float(best_std):
                    best_std = float(std)
                    best_idx = int(idx)
                    # Replace previous best cam.
                    if best_cam is not None:
                        try:
                            stop = getattr(best_cam, 'stop', None)
                            if callable(stop):
                                stop()
                        except Exception:
                            pass
                    best_cam = cam
                    cam = None
            except Exception:
                continue
            finally:
                if cam is not None:
                    try:
                        stop = getattr(cam, 'stop', None)
                        if callable(stop):
                            stop()
                    except Exception:
                        pass

        return best_idx, best_cam

    def _ensure(self) -> Any:
        if self._cam is None:
            with _suppress_output():
                self._cam = self._dxcam.create(output_idx=int(self._output_idx))
        if not self._baseline_done:
            # Dump a baseline frame to help ROI picking.
            try:
                warmup_attempts = int(_env_int('FRBOT_DXCAM_WARMUP_GRABS') or 3)
                warmup_sleep_ms = int(_env_int('FRBOT_DXCAM_WARMUP_SLEEP_MS') or 50)
                rgb, w, h, _meta, _trace = _dxcam_grab_rgb_with_warmup(
                    self._cam,
                    region=None,
                    attempts=warmup_attempts,
                    sleep_s=float(warmup_sleep_ms) / 1000.0,
                )
                if rgb and int(w) > 0 and int(h) > 0:
                    ts = time.monotonic_ns()
                    f = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts), digest_hex='', rgb=rgb)
                    from diagnostics.frame_dump import dump_frame_ppm

                    # Required evidence name (preferred) + legacy name (compat).
                    dump_frame_ppm(f, Path('diagnostics/obs_projector_full.ppm'))
                    dump_frame_ppm(f, Path('diagnostics/projector_full.ppm'))
            except Exception:
                pass
            self._baseline_done = True
        return self._cam

    def _assert_foreground(self) -> tuple[int, int]:
        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, 'hwnd', 0) or 0)
        fg = 0
        try:
            fg = int(w32.get_foreground_window())
        except Exception:
            fg = 0

        require = _env_bool('FRBOT_PROJECTOR_REQUIRE_FOREGROUND', True)
        if not require:
            return hwnd, fg

        if hwnd <= 0 or not w32.is_window(hwnd):
            _hard_stop('window_hwnd_invalid', details={'hwnd': hwnd, 'foreground_hwnd': fg, 'dpi_awareness': w32.get_dpi_awareness_status()})
        if fg != hwnd:
            _hard_stop('window_not_foreground', details={'hwnd': hwnd, 'foreground_hwnd': fg})
        if not w32.is_window_visible(hwnd):
            _hard_stop('window_not_visible', details={'hwnd': hwnd, 'foreground_hwnd': fg})
        if w32.is_window_minimized(hwnd):
            _hard_stop('window_minimized', details={'hwnd': hwnd, 'foreground_hwnd': fg})
        return hwnd, fg

    def verify(self) -> VerificationResult:
        try:
            ok_mon, why = _dxcam_has_monitors(self._dxcam)
            if not ok_mon:
                return VerificationResult(ok=False, reason=why or 'no_monitors')

            # Safety check (optional).
            try:
                self._assert_foreground()
            except PreflightFailed as exc:
                return VerificationResult(ok=False, reason=str(getattr(exc, 'args', [''])[0] or 'window_not_foreground'))

            cam = self._ensure()

            warmup_attempts = int(_env_int('FRBOT_DXCAM_WARMUP_GRABS') or 3)
            warmup_sleep_ms = int(_env_int('FRBOT_DXCAM_WARMUP_SLEEP_MS') or 50)

            region = _parse_region_env()
            if region is None:
                region_box = None
            else:
                x, y, w, h = region
                region_box = (int(x), int(y), int(x) + int(w), int(y) + int(h))

            rgb, w2, h2, _meta, _trace = _dxcam_grab_rgb_with_warmup(
                cam,
                region=region_box,
                attempts=warmup_attempts,
                sleep_s=float(warmup_sleep_ms) / 1000.0,
            )
            if not rgb or int(w2) <= 0 or int(h2) <= 0:
                return VerificationResult(ok=False, reason='frame_empty')

            _mean, std, all_zero = _sample_luma_stats(rgb, width=int(w2), height=int(h2))
            if all_zero or std <= 5.0:
                return VerificationResult(ok=False, reason='obs_projector_invalid_capture')

            mm = _crop_rgb(rgb, int(w2), int(h2), self._minimap_roi)
            if not mm:
                return VerificationResult(ok=False, reason='minimap_not_detected')

            return VerificationResult(ok=True)
        except ImportError as exc:  # pragma: no cover
            return VerificationResult(ok=False, reason=str(exc))
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        hwnd, fg = self._assert_foreground()
        cam = self._ensure()

        region = _parse_region_env()
        if region is None:
            region_box = None
            region_desc: object = None
        else:
            x, y, w, h = region
            region_box = (int(x), int(y), int(x) + int(w), int(y) + int(h))
            region_desc = [int(x), int(y), int(w), int(h)]

        ts_ns = time.monotonic_ns()
        # Keep a tiny warm-up here too: a single empty frame should not brick the run.
        rgb, w2, h2, _meta, _trace = _dxcam_grab_rgb_with_warmup(
            cam,
            region=region_box,
            attempts=int(_env_int('FRBOT_DXCAM_GRAB_ATTEMPTS') or 5),
            sleep_s=float(int(_env_int('FRBOT_DXCAM_GRAB_SLEEP_MS') or 20)) / 1000.0,
        )
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

        if not rgb or int(w2) <= 0 or int(h2) <= 0:
            _hard_stop(
                'frame_empty',
                details={
                    'hwnd': hwnd,
                    'foreground_hwnd': fg,
                    'output_idx': int(self._output_idx),
                    'region': region_desc,
                    'attempts': list(_trace),
                },
            )

        _mean, std, all_zero = _sample_luma_stats(rgb, width=int(w2), height=int(h2))
        if all_zero or std <= 2.0:
            failing = Frame(width=int(w2), height=int(h2), monotonic_ts_ns=int(ts_ns), digest_hex=str(digest), rgb=rgb)
            # Re-grab for evidence.
            try:
                frame2 = cam.grab() if region is None else cam.grab(region=(int(x), int(y), int(x) + int(w), int(y) + int(h)))
                rgb2, w3, h3, _m2 = _to_rgb_bytes(frame2)
                ts2 = time.monotonic_ns()
                dig2 = hashlib.sha256(rgb2).hexdigest() if rgb2 else ''
                after = Frame(width=int(w3), height=int(h3), monotonic_ts_ns=int(ts2), digest_hex=str(dig2), rgb=rgb2)
            except Exception:
                after = None

            reason = 'obs_projector_invalid_capture'
            dump_pair(gate='capture', before=failing, after=after, reason=reason)
            _hard_stop(
                reason,
                details={
                    'hwnd': hwnd,
                    'foreground_hwnd': fg,
                    'output_idx': int(self._output_idx),
                    'region': region_desc,
                    'frame_size': [int(w2), int(h2)],
                    'std_luma': float(std),
                    'all_zero': bool(all_zero),
                },
            )

        minimap_rgb = _crop_rgb(rgb, int(w2), int(h2), self._minimap_roi)
        minimap_detected = bool(minimap_rgb)
        minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ''

        return Frame(
            width=int(w2),
            height=int(h2),
            monotonic_ts_ns=int(ts_ns),
            digest_hex=str(digest),
            rgb=rgb,
            minimap_detected=bool(minimap_detected),
            minimap_rgb=minimap_rgb,
            minimap_width=int(self._minimap_roi.width),
            minimap_height=int(self._minimap_roi.height),
            minimap_digest_hex=str(minimap_digest),
        )
