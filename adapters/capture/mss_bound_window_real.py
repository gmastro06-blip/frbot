from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from adapters.capture.gdi_hwnd_diag import capture_client_bgra
from adapters.window.win32 import Win32WindowBinding
from adapters.windows import win32 as w32
from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair

# Reuse luma stats logic used by MELD diagnostics.
from adapters.capture.meld_real import _sample_luma_stats


def _rect_to_region(binding: WindowBindingAdapter) -> dict[str, int]:
    snap = binding.snapshot()
    return {
        'left': int(snap.rect.left),
        'top': int(snap.rect.top),
        'width': int(snap.rect.width),
        'height': int(snap.rect.height),
    }


def _region_in_virtual_screen(sct: Any, region: dict[str, int]) -> bool:
    """Return whether region intersects the virtual desktop bounds.

    mss exposes monitor[0] as the virtual screen on Windows.
    """

    try:
        monitors = getattr(sct, 'monitors', None)
        if not isinstance(monitors, list) or not monitors:
            return True
        m0 = monitors[0]
        if not isinstance(m0, dict):
            return True
        ml = int(m0.get('left', 0))
        mt = int(m0.get('top', 0))
        mw = int(m0.get('width', 0))
        mh = int(m0.get('height', 0))
        if mw <= 0 or mh <= 0:
            return True

        rl = int(region.get('left', 0))
        rt = int(region.get('top', 0))
        rw = int(region.get('width', 0))
        rh = int(region.get('height', 0))
        if rw <= 0 or rh <= 0:
            return False
        rr = rl + rw
        rb = rt + rh

        mr = ml + mw
        mb = mt + mh

        inter_w = max(0, min(rr, mr) - max(rl, ml))
        inter_h = max(0, min(rb, mb) - max(rt, mt))
        return (inter_w > 0) and (inter_h > 0)
    except Exception:
        return True


def _looks_black(rgb: bytes) -> bool:
    """Heuristic: treat frames as black if sampled pixels are all zero.

    This catches common failures where capture is pointed at an offscreen region
    (often due to DPI scaling) and mss returns a mostly-zero buffer.
    """

    if not rgb:
        return True

    # Sample up to ~5000 pixels (triplets) uniformly across the buffer.
    n = len(rgb)
    px = n // 3
    if px <= 0:
        return True
    step = max(1, px // 5000)
    i = 0
    for _ in range(0, px, step):
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        if (r | g | b) != 0:
            return False
        i += 3 * step
        if i + 2 >= n:
            break
    return True


def _entropy_bits_per_byte(sample: bytes) -> float:
    if not sample:
        return 0.0
    # Shannon entropy over byte values.
    counts = [0] * 256
    for b in sample:
        counts[b] += 1
    n = float(len(sample))
    import math

    h = 0.0
    for c in counts:
        if c:
            p = float(c) / n
            h -= p * math.log(p, 2)
    return float(h)


def _sample_bytes(buf: bytes, *, max_len: int = 32768) -> bytes:
    if not buf:
        return b''
    if len(buf) <= max_len:
        return bytes(buf)
    # Uniform-ish downsample.
    step = max(1, len(buf) // max_len)
    return bytes(buf[::step])[:max_len]


def _alpha_info(img: Any, *, width: int, height: int) -> dict[str, object] | None:
    """Return alpha diagnostics if available.

    mss screenshot usually exposes .bgra or .raw as BGRA bytes.
    """

    bgra = getattr(img, 'bgra', None)
    if bgra is None:
        bgra = getattr(img, 'raw', None)
    if bgra is None:
        return None
    try:
        b = bytes(bgra)
    except Exception:
        return {'present': True, 'ok': False, 'reason': 'alpha_buffer_unreadable'}

    expected = int(width) * int(height) * 4
    if expected <= 0:
        return {'present': True, 'ok': False, 'reason': 'alpha_expected_invalid'}
    if len(b) != expected:
        return {'present': True, 'ok': False, 'reason': 'alpha_size_mismatch', 'len': int(len(b)), 'expected': int(expected)}

    # Sample alpha bytes.
    step = max(1, (width * height) // 20000)
    a_min = 255
    a_max = 0
    zeros = 0
    ff = 0
    other = 0
    i = 3
    px = width * height
    for _ in range(0, px, step):
        a = b[i]
        if a < a_min:
            a_min = a
        if a > a_max:
            a_max = a
        if a == 0:
            zeros += 1
        elif a == 255:
            ff += 1
        else:
            other += 1
        i += 4 * step
        if i >= len(b):
            break
    return {
        'present': True,
        'ok': True,
        'min': int(a_min),
        'max': int(a_max),
        'sample_zeros': int(zeros),
        'sample_255': int(ff),
        'sample_other': int(other),
    }


def _rect_dict(r: Any) -> dict[str, int]:
    return {
        'left': int(getattr(r, 'left', 0) or 0),
        'top': int(getattr(r, 'top', 0) or 0),
        'right': int(getattr(r, 'right', 0) or 0),
        'bottom': int(getattr(r, 'bottom', 0) or 0),
        'width': int(getattr(r, 'width', 0) or 0),
        'height': int(getattr(r, 'height', 0) or 0),
    }


def _hard_stop(reason: str, *, details: dict[str, object]) -> None:
    exc = PreflightFailed(reason)
    setattr(exc, 'details', details)
    # Ensure fatal evidence exists even if callers mishandle exceptions.
    write_fatal(reason, exc, details=details)
    raise exc


def _is_placeholder_truthy(v: str) -> bool:
    return (v or '').strip().lower() in {'1', 'true', 'yes', 'on'}


class MssBoundWindowRealCapture(CaptureAdapter):
    """Capture the *currently verified* HWND client rect via mss.

    Important:
    - The capture region is derived from WindowBindingAdapter.snapshot() at grab-time.
    - Callers are responsible for enforcing binding.assert_bound() before grab() when required.
    """

    name = 'mss-hwnd'

    def __init__(self, *, binding: WindowBindingAdapter) -> None:
        try:
            import mss  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: mss') from exc

        self._mss_mod = mss
        self._sct: Any | None = None
        self._binding = binding

        # Defensive: we expect real-mode to use Win32 binding.
        # (Does not change behavior; helps catch accidental wiring.)
        if not isinstance(binding, Win32WindowBinding):
            pass

    def _ensure(self) -> Any:
        if self._sct is None:
            self._sct = self._mss_mod.mss()
        return self._sct

    def verify(self) -> VerificationResult:
        try:
            sct = self._ensure()
            now_ns = time.monotonic_ns()

            region = _rect_to_region(self._binding)
            if not _region_in_virtual_screen(sct, region):
                return VerificationResult(ok=False, reason='capture_region_offscreen')
            img = sct.grab(region)
            ts_ns = time.monotonic_ns()

            w = int(getattr(img, 'width', 0) or 0)
            h = int(getattr(img, 'height', 0) or 0)
            if w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='captured frame has invalid resolution')

            rgb = getattr(img, 'rgb', None)
            if rgb is None or len(rgb) == 0:
                return VerificationResult(ok=False, reason='captured frame has no rgb buffer')

            if os.environ.get('FRBOT_ALLOW_BLACK_FRAMES', '0').strip() not in {'1', 'true', 'yes', 'on'}:
                if _looks_black(bytes(rgb)):
                    return VerificationResult(ok=False, reason='captured_frame_black')

            digest = hashlib.sha256(rgb).hexdigest()
            if len(digest) != 64:
                return VerificationResult(ok=False, reason='captured frame digest invalid')

            if (ts_ns - now_ns) < 0 or (ts_ns - now_ns) > 1_000_000_000:
                return VerificationResult(ok=False, reason='capture timestamp not recent')

            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        sct = self._ensure()

        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, 'hwnd', 0) or 0)

        # Root-cause checks (distinct reasons).
        foreground_hwnd = 0
        try:
            foreground_hwnd = int(w32.get_foreground_window())
        except Exception:
            foreground_hwnd = 0
        if hwnd <= 0 or not w32.is_window(hwnd):
            _hard_stop('window_hwnd_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'dpi_awareness': w32.get_dpi_awareness_status()})
        if foreground_hwnd != hwnd:
            _hard_stop('window_not_foreground', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})
        if not w32.is_window_visible(hwnd):
            _hard_stop('window_not_visible', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})
        if w32.is_window_minimized(hwnd):
            _hard_stop('window_minimized', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})

        pid = 0
        priv_ok = True
        priv_reason = None
        try:
            pid = int(w32.get_window_process_id(hwnd))
            priv_ok, priv_reason = w32.can_query_process(pid)
        except Exception:
            priv_ok, priv_reason = True, None
        if not priv_ok:
            _hard_stop('insufficient_privileges', details={'hwnd': hwnd, 'pid': pid, 'probe': priv_reason})

        # Recompute rect on every grab.
        try:
            rect_client = w32.get_client_rect_in_screen(hwnd)
        except Exception as exc:
            _hard_stop('client_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'error': f'{type(exc).__name__}: {exc}'})

        try:
            rect_window = w32.get_window_rect_in_screen(hwnd)
        except Exception as exc:
            _hard_stop('window_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'error': f'{type(exc).__name__}: {exc}'})

        # Detect desync: client rect should be inside window rect (within a small tolerance).
        if (rect_client.left < (rect_window.left - 2)) or (rect_client.top < (rect_window.top - 2)) or (rect_client.right > (rect_window.right + 2)) or (rect_client.bottom > (rect_window.bottom + 2)):
            _hard_stop(
                'window_rect_out_of_sync',
                details={
                    'hwnd': hwnd,
                    'foreground_hwnd': foreground_hwnd,
                    'rect_client': _rect_dict(rect_client),
                    'rect_window': _rect_dict(rect_window),
                    'dpi_awareness': w32.get_dpi_awareness_status(),
                },
            )

        if rect_client.width <= 0 or rect_client.height <= 0:
            _hard_stop('hwnd_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'rect_client': _rect_dict(rect_client)})

        region = {'left': int(rect_client.left), 'top': int(rect_client.top), 'width': int(rect_client.width), 'height': int(rect_client.height)}
        if not _region_in_virtual_screen(sct, region):
            _hard_stop(
                'hwnd_rect_offscreen',
                details={
                    'hwnd': hwnd,
                    'foreground_hwnd': foreground_hwnd,
                    'region': dict(region),
                },
            )

        img = sct.grab(region)
        ts_ns = time.monotonic_ns()
        w = int(getattr(img, 'width', 0) or 0)
        h = int(getattr(img, 'height', 0) or 0)
        rgb = getattr(img, 'rgb', b'') or b''
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

        alpha = _alpha_info(img, width=w, height=h)
        sample = _sample_bytes(bytes(rgb))
        entropy = _entropy_bits_per_byte(sample)

        alpha_invalid = False
        if isinstance(alpha, dict) and alpha.get('present') is True and alpha.get('ok') is True:
            a_max = alpha.get('max', 255)
            a_max_i = a_max if isinstance(a_max, int) else 255
            if a_max_i == 0:
                alpha_invalid = True
        if isinstance(alpha, dict) and alpha.get('present') is True and alpha.get('ok') is False:
            alpha_invalid = True

        mean, std, all_zero = _sample_luma_stats(bytes(rgb), width=int(w), height=int(h))
        black = _looks_black(bytes(rgb)) or bool(all_zero) or float(std) <= 0.0001
        low_entropy = entropy <= float(os.environ.get('FRBOT_BLACK_ENTROPY_MAX', '0.02') or '0.02')

        if black or alpha_invalid or low_entropy:
            failing = Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex=digest, rgb=bytes(rgb))
            # Immediate re-grab (no input) to provide BEFORE/AFTER evidence without continuing execution.
            img2 = sct.grab(region)
            ts2 = time.monotonic_ns()
            rgb2 = getattr(img2, 'rgb', b'') or b''
            dig2 = hashlib.sha256(rgb2).hexdigest() if rgb2 else ''
            after = Frame(width=int(getattr(img2, 'width', 0) or 0), height=int(getattr(img2, 'height', 0) or 0), monotonic_ts_ns=ts2, digest_hex=dig2, rgb=bytes(rgb2))

            reason = 'capture_invalid'

            details: dict[str, object] = {
                'hwnd': hwnd,
                'foreground_hwnd': foreground_hwnd,
                'region': dict(region),
                'rect_client': _rect_dict(rect_client),
                'rect_window': _rect_dict(rect_window),
                'dpi_awareness': w32.get_dpi_awareness_status(),
                'rgb_all_zero': bool(black),
                'luma_mean': float(mean),
                'luma_std': float(std),
                'entropy_bits_per_byte': float(entropy),
                'alpha_info': alpha,
                'subreasons': {
                    'black': bool(black),
                    'alpha_invalid': bool(alpha_invalid),
                    'entropy_low': bool(low_entropy),
                },
            }

            # Optional diagnostic backend (never a fallback): prove MSS/DXGI blocked.
            if os.environ.get('FRBOT_CAPTURE_DIAG_BACKEND', '').strip().lower() == 'gdi':
                gdi = capture_client_bgra(hwnd, rect_client)
                gdi_details: dict[str, object] = {
                    'backend': 'gdi',
                    'ok': bool(gdi.ok),
                    'reason': str(gdi.reason),
                    'last_error': gdi.last_error,
                    'method': gdi.method,
                }
                if gdi.ok and gdi.bgra:
                    # Convert BGRA -> RGB for metrics.
                    bgra = gdi.bgra
                    rgb_gdi = bytearray(gdi.width * gdi.height * 3)
                    j = 0
                    for i in range(0, len(bgra), 4):
                        b = bgra[i]
                        g = bgra[i + 1]
                        r = bgra[i + 2]
                        rgb_gdi[j] = r
                        rgb_gdi[j + 1] = g
                        rgb_gdi[j + 2] = b
                        j += 3
                    gdi_black = _looks_black(bytes(rgb_gdi))
                    gdi_entropy = _entropy_bits_per_byte(_sample_bytes(bytes(rgb_gdi)))
                    gdi_details.update({'entropy_bits_per_byte': float(gdi_entropy), 'rgb_all_zero': bool(gdi_black)})
                    if (not gdi_black) and black:
                        reason = 'dxgi_capture_blocked_by_client'
                details['diag'] = gdi_details

            if dump_enabled():
                dump_pair(gate='capture', before=failing, after=after, reason=reason)
            _hard_stop('capture_invalid', details=details)

        return Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex=digest, rgb=rgb)


class MssBoundMinimapRealCapture(CaptureAdapter):
    """HWND-bound mss capture with minimap crop evidence."""

    name = 'mss-hwnd-minimap'

    def __init__(self, minimap_roi: Roi, *, binding: WindowBindingAdapter) -> None:
        try:
            import mss  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: mss') from exc

        self._mss_mod = mss
        self._sct: Any | None = None
        self._binding = binding
        self._minimap_roi = minimap_roi

    def _ensure(self) -> Any:
        if self._sct is None:
            self._sct = self._mss_mod.mss()
        return self._sct

    def _crop_rgb(self, rgb: bytes, width: int, height: int, roi: Roi) -> bytes:
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

    def verify(self) -> VerificationResult:
        try:
            sct = self._ensure()
            now_ns = time.monotonic_ns()

            region = _rect_to_region(self._binding)
            if not _region_in_virtual_screen(sct, region):
                return VerificationResult(ok=False, reason='capture_region_offscreen')
            img = sct.grab(region)
            ts_ns = time.monotonic_ns()

            w = int(getattr(img, 'width', 0) or 0)
            h = int(getattr(img, 'height', 0) or 0)
            if w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='captured frame has invalid resolution')

            rgb = getattr(img, 'rgb', None)
            if rgb is None or len(rgb) == 0:
                return VerificationResult(ok=False, reason='captured frame has no rgb buffer')

            if os.environ.get('FRBOT_ALLOW_BLACK_FRAMES', '0').strip() not in {'1', 'true', 'yes', 'on'}:
                if _looks_black(bytes(rgb)):
                    return VerificationResult(ok=False, reason='captured_frame_black')

            minimap_rgb = self._crop_rgb(rgb, w, h, self._minimap_roi)
            if not minimap_rgb:
                return VerificationResult(ok=False, reason='minimap_not_detected')

            digest = hashlib.sha256(rgb).hexdigest()
            if len(digest) != 64:
                return VerificationResult(ok=False, reason='captured frame digest invalid')

            minimap_digest = hashlib.sha256(minimap_rgb).hexdigest()
            if len(minimap_digest) != 64:
                return VerificationResult(ok=False, reason='minimap digest invalid')

            if (ts_ns - now_ns) < 0 or (ts_ns - now_ns) > 1_000_000_000:
                return VerificationResult(ok=False, reason='capture timestamp not recent')

            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        sct = self._ensure()

        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, 'hwnd', 0) or 0)
        foreground_hwnd = 0
        try:
            foreground_hwnd = int(w32.get_foreground_window())
        except Exception:
            foreground_hwnd = 0

        if hwnd <= 0 or not w32.is_window(hwnd):
            _hard_stop('window_hwnd_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'dpi_awareness': w32.get_dpi_awareness_status()})
        if foreground_hwnd != hwnd:
            _hard_stop('window_not_foreground', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})
        if not w32.is_window_visible(hwnd):
            _hard_stop('window_not_visible', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})
        if w32.is_window_minimized(hwnd):
            _hard_stop('window_minimized', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd})

        pid = 0
        priv_ok = True
        priv_reason = None
        try:
            pid = int(w32.get_window_process_id(hwnd))
            priv_ok, priv_reason = w32.can_query_process(pid)
        except Exception:
            priv_ok, priv_reason = True, None
        if not priv_ok:
            _hard_stop('insufficient_privileges', details={'hwnd': hwnd, 'pid': pid, 'probe': priv_reason})

        try:
            rect_client = w32.get_client_rect_in_screen(hwnd)
        except Exception as exc:
            _hard_stop('client_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'error': f'{type(exc).__name__}: {exc}'})

        try:
            rect_window = w32.get_window_rect_in_screen(hwnd)
        except Exception as exc:
            _hard_stop('window_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'error': f'{type(exc).__name__}: {exc}'})

        if (rect_client.left < (rect_window.left - 2)) or (rect_client.top < (rect_window.top - 2)) or (rect_client.right > (rect_window.right + 2)) or (rect_client.bottom > (rect_window.bottom + 2)):
            _hard_stop(
                'window_rect_out_of_sync',
                details={
                    'hwnd': hwnd,
                    'foreground_hwnd': foreground_hwnd,
                    'rect_client': _rect_dict(rect_client),
                    'rect_window': _rect_dict(rect_window),
                    'dpi_awareness': w32.get_dpi_awareness_status(),
                },
            )

        if rect_client.width <= 0 or rect_client.height <= 0:
            _hard_stop('hwnd_rect_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'rect_client': _rect_dict(rect_client)})

        region = {'left': int(rect_client.left), 'top': int(rect_client.top), 'width': int(rect_client.width), 'height': int(rect_client.height)}
        if not _region_in_virtual_screen(sct, region):
            _hard_stop('hwnd_rect_offscreen', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'region': dict(region)})

        img = sct.grab(region)
        ts_ns = time.monotonic_ns()
        w = int(getattr(img, 'width', 0) or 0)
        h = int(getattr(img, 'height', 0) or 0)
        rgb = getattr(img, 'rgb', b'') or b''

        mean, std, all_zero = _sample_luma_stats(bytes(rgb), width=int(w), height=int(h))
        if _looks_black(bytes(rgb)) or bool(all_zero) or float(std) <= 0.0001:
            failing = Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex='', rgb=bytes(rgb))
            if dump_enabled():
                dump_pair(gate='capture', before=failing, after=None, reason='capture_invalid')
            _hard_stop('capture_invalid', details={'hwnd': hwnd, 'foreground_hwnd': foreground_hwnd, 'region': dict(region), 'luma_mean': float(mean), 'luma_std': float(std), 'all_zero': bool(all_zero)})
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

        alpha = _alpha_info(img, width=w, height=h)
        sample = _sample_bytes(bytes(rgb))
        entropy = _entropy_bits_per_byte(sample)

        alpha_invalid = False
        if isinstance(alpha, dict) and alpha.get('present') is True and alpha.get('ok') is True:
            a_max = alpha.get('max', 255)
            a_max_i = a_max if isinstance(a_max, int) else 255
            if a_max_i == 0:
                alpha_invalid = True
        if isinstance(alpha, dict) and alpha.get('present') is True and alpha.get('ok') is False:
            alpha_invalid = True

        black = _looks_black(bytes(rgb))
        low_entropy = entropy <= float(os.environ.get('FRBOT_BLACK_ENTROPY_MAX', '0.02') or '0.02')

        if black or alpha_invalid or low_entropy:
            failing = Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex=digest, rgb=bytes(rgb))
            img2 = sct.grab(region)
            ts2 = time.monotonic_ns()
            rgb2 = getattr(img2, 'rgb', b'') or b''
            dig2 = hashlib.sha256(rgb2).hexdigest() if rgb2 else ''
            after = Frame(width=int(getattr(img2, 'width', 0) or 0), height=int(getattr(img2, 'height', 0) or 0), monotonic_ts_ns=ts2, digest_hex=dig2, rgb=bytes(rgb2))

            reason = 'black_frame_capture'
            details: dict[str, object] = {
                'hwnd': hwnd,
                'foreground_hwnd': foreground_hwnd,
                'region': dict(region),
                'rect_client': _rect_dict(rect_client),
                'rect_window': _rect_dict(rect_window),
                'dpi_awareness': w32.get_dpi_awareness_status(),
                'rgb_all_zero': bool(black),
                'entropy_bits_per_byte': float(entropy),
                'alpha_info': alpha,
                'subreasons': {
                    'black': bool(black),
                    'alpha_invalid': bool(alpha_invalid),
                    'entropy_low': bool(low_entropy),
                },
            }

            if os.environ.get('FRBOT_CAPTURE_DIAG_BACKEND', '').strip().lower() == 'gdi':
                gdi = capture_client_bgra(hwnd, rect_client)
                gdi_details: dict[str, object] = {
                    'backend': 'gdi',
                    'ok': bool(gdi.ok),
                    'reason': str(gdi.reason),
                    'last_error': gdi.last_error,
                    'method': gdi.method,
                }
                if gdi.ok and gdi.bgra:
                    bgra = gdi.bgra
                    rgb_gdi = bytearray(gdi.width * gdi.height * 3)
                    j = 0
                    for i in range(0, len(bgra), 4):
                        b = bgra[i]
                        g = bgra[i + 1]
                        r = bgra[i + 2]
                        rgb_gdi[j] = r
                        rgb_gdi[j + 1] = g
                        rgb_gdi[j + 2] = b
                        j += 3
                    gdi_black = _looks_black(bytes(rgb_gdi))
                    gdi_entropy = _entropy_bits_per_byte(_sample_bytes(bytes(rgb_gdi)))
                    gdi_details.update({'entropy_bits_per_byte': float(gdi_entropy), 'rgb_all_zero': bool(gdi_black)})
                    if (not gdi_black) and black:
                        reason = 'dxgi_capture_blocked_by_client'
                details['diag'] = gdi_details

            dump_pair(gate='capture', before=failing, after=after, reason=reason)
            _hard_stop(reason, details=details)

        minimap_rgb = self._crop_rgb(rgb or b'', w, h, self._minimap_roi) if rgb else b''
        minimap_detected = bool(minimap_rgb)
        minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ''

        return Frame(
            width=w,
            height=h,
            monotonic_ts_ns=ts_ns,
            digest_hex=digest,
            rgb=rgb or b'',
            minimap_detected=minimap_detected,
            minimap_rgb=minimap_rgb,
            minimap_width=int(self._minimap_roi.width),
            minimap_height=int(self._minimap_roi.height),
            minimap_digest_hex=minimap_digest,
        )
