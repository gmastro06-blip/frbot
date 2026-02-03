from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any

from adapters.windows import win32 as w32
from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter
from diagnostics.frame_dump import dump_enabled, dump_frame_ppm, dump_pair


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


def _sample_luma_stats(rgb: bytes, *, width: int, height: int, max_pixels: int = 20000) -> tuple[float, float, bool]:
    """Return (mean_luma, std_luma, all_zero_rgb) using uniform sampling.

    Assumes rgb is 24-bit packed RGB OR BGR; luma weights are approximate but
    black detection relies on variance/non-zero, not exact color.
    """

    if width <= 0 or height <= 0:
        return 0.0, 0.0, True
    expected = width * height * 3
    if len(rgb) != expected or expected <= 0:
        return 0.0, 0.0, True

    px = width * height
    step = max(1, px // max_pixels)

    # Welford online variance
    mean = 0.0
    m2 = 0.0
    n = 0
    all_zero = True

    idx = 0
    for _ in range(0, px, step):
        r = rgb[idx]
        g = rgb[idx + 1]
        b = rgb[idx + 2]
        if (r | g | b) != 0:
            all_zero = False
        # Luma-ish (order-insensitive enough for variance): average channels
        y = (float(r) + float(g) + float(b)) / 3.0
        n += 1
        delta = y - mean
        mean += delta / float(n)
        delta2 = y - mean
        m2 += delta * delta2
        idx += 3 * step
        if idx + 2 >= len(rgb):
            break

    if n <= 1:
        return float(mean), 0.0, bool(all_zero)
    var = m2 / float(n - 1)
    std = var ** 0.5
    return float(mean), float(std), bool(all_zero)


def _to_rgb_bytes(frame: Any) -> tuple[bytes, int, int, dict[str, object]]:
    """Normalize a dxcam frame into packed 24-bit bytes.

    Expected inputs:
    - numpy ndarray shape (h, w, 3) or (h, w, 4)
    - any object with .shape and .tobytes()
    """

    meta: dict[str, object] = {}

    if frame is None:
        return b'', 0, 0, {'reason': 'frame_none'}

    shape = getattr(frame, 'shape', None)
    if not (isinstance(shape, tuple) and len(shape) >= 2):
        return b'', 0, 0, {'reason': 'frame_no_shape', 'type': type(frame).__name__}

    h = int(shape[0])
    w = int(shape[1])
    c = int(shape[2]) if len(shape) >= 3 else 0
    meta.update({'shape': list(shape), 'channels': c})

    if w <= 0 or h <= 0:
        return b'', 0, 0, {'reason': 'frame_invalid_dims', **meta}

    tobytes = getattr(frame, 'tobytes', None)
    if not callable(tobytes):
        return b'', 0, 0, {'reason': 'frame_no_tobytes', **meta}

    raw = bytes(tobytes())
    if c == 3:
        expected = w * h * 3
        if len(raw) != expected:
            return b'', 0, 0, {'reason': 'frame_size_mismatch', 'len': len(raw), 'expected': expected, **meta}
        return raw, w, h, {'reason': 'ok', **meta}

    if c == 4:
        expected = w * h * 4
        if len(raw) != expected:
            return b'', 0, 0, {'reason': 'frame_size_mismatch', 'len': len(raw), 'expected': expected, **meta}

        # Drop alpha -> RGB (assume frame order is BGRA or RGBA; variance detection is robust)
        out = bytearray(w * h * 3)
        j = 0
        for i in range(0, len(raw), 4):
            out[j] = raw[i]
            out[j + 1] = raw[i + 1]
            out[j + 2] = raw[i + 2]
            j += 3
        return bytes(out), w, h, {'reason': 'ok_drop_alpha', **meta}

    return b'', 0, 0, {'reason': 'unsupported_channels', **meta}


def _dxcam_has_monitors(dxcam_mod: Any) -> tuple[bool, str | None]:
    """Return (ok, reason) for monitor availability.

    dxcam exposes output enumeration on some versions via dxcam.output_info().
    This is best-effort only: missing output_info should not hard-fail.
    """

    fn = getattr(dxcam_mod, 'output_info', None)
    if not callable(fn):
        return True, None
    try:
        info = fn()
    except Exception as exc:
        return False, f'dxcam_output_info_failed: {type(exc).__name__}: {exc}'
    if info is None:
        return False, 'dxcam_no_outputs'
    # Accept dict/list forms.
    if isinstance(info, dict) and len(info) == 0:
        return False, 'dxcam_no_outputs'
    if isinstance(info, list) and len(info) == 0:
        return False, 'dxcam_no_outputs'
    return True, None


def _rect_dict(rect: Any) -> dict[str, int]:
    return {
        'left': int(getattr(rect, 'left', 0) or 0),
        'top': int(getattr(rect, 'top', 0) or 0),
        'right': int(getattr(rect, 'right', 0) or 0),
        'bottom': int(getattr(rect, 'bottom', 0) or 0),
        'width': int(getattr(rect, 'width', 0) or 0),
        'height': int(getattr(rect, 'height', 0) or 0),
    }


def _frame_size_dict(w: int, h: int) -> list[int]:
    return [int(w), int(h)]


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


def _dxcam_output_map(dxcam_mod: Any, *, max_outputs: int = 8) -> dict[str, dict[str, object]]:
    r"""Best-effort mapping: display device name (e.g. \\.\DISPLAY1) -> output_idx + size."""

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
        try:
            out[name] = {'output_idx': int(idx), 'width': int(getattr(cam, 'width', 0) or 0), 'height': int(getattr(cam, 'height', 0) or 0)}
        except Exception:
            out[name] = {'output_idx': int(idx), 'width': 0, 'height': 0}
    return out


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _as_int(v: object, default: int = 0) -> int:
    try:
        if isinstance(v, bool):
            return int(default)
        if isinstance(v, int):
            return int(v)
        if isinstance(v, str):
            return int(v.strip() or str(default))
        return int(default)
    except Exception:
        return int(default)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _grab_full_baseline(cam: Any, *, out_path: str | Path = 'diagnostics/capture_full.ppm') -> tuple[int, int]:
    frame = None
    try:
        frame = cam.grab()
    except Exception:
        frame = None
    rgb, w, h, _meta = _to_rgb_bytes(frame)
    if rgb and int(w) > 0 and int(h) > 0:
        ts_ns = time.monotonic_ns()
        f = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts_ns), digest_hex='', rgb=rgb)
        dump_frame_ppm(f, Path(out_path))
    return int(w), int(h)


class MeldBoundWindowRealCapture(CaptureAdapter):
    """REAL capture using DXGI framebuffer capture (dxcam) cropped to HWND client rect.

    Window-only variant (no minimap crop). Useful for diagnostics.
    """

    name = 'meld'

    def __init__(self, *, binding: WindowBindingAdapter) -> None:
        try:
            import dxcam  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: dxcam') from exc

        self._dxcam = dxcam
        self._cam: Any | None = None
        self._binding = binding
        self._output_idx: int | None = None
        self._output_name: str = ''
        self._monitor_device: str = ''
        self._monitor_rect: Any | None = None
        self._baseline_done: bool = False

    def _resolve_output_for_hwnd(self, hwnd: int, rect_client: Any) -> dict[str, object]:
        mons = w32.list_monitors()
        cx = (int(getattr(rect_client, 'left', 0) or 0) + int(getattr(rect_client, 'right', 0) or 0)) // 2
        cy = (int(getattr(rect_client, 'top', 0) or 0) + int(getattr(rect_client, 'bottom', 0) or 0)) // 2
        mon = w32.monitor_for_point(cx, cy, mons) or w32.primary_monitor(mons)
        if mon is None:
            return {'device': '', 'rect': None, 'primary': True}
        return {'device': str(mon.device), 'rect': mon.rect, 'primary': bool(mon.primary)}

    def _ensure(self, *, hwnd: int, rect_client: Any) -> Any:
        target = self._resolve_output_for_hwnd(hwnd, rect_client)
        device = str(target.get('device') or '')
        mon_rect = target.get('rect')

        forced_idx = _env_int('FRBOT_MELD_OUTPUT_IDX')

        output_map = _dxcam_output_map(self._dxcam)
        desired_idx = 0
        if forced_idx is not None:
            desired_idx = int(forced_idx)
        elif device and device in output_map:
            desired_idx = _as_int(output_map[device].get('output_idx', 0), 0)

        if self._cam is None or self._output_idx != int(desired_idx):
            self._cam = self._dxcam.create(output_idx=int(desired_idx))
            self._output_idx = int(desired_idx)
            try:
                o = getattr(self._cam, '_output', None)
                self._output_name = str(getattr(o, 'name', '') or '')
            except Exception:
                self._output_name = ''

        self._monitor_device = device
        self._monitor_rect = mon_rect

        if not self._baseline_done:
            _grab_full_baseline(self._cam, out_path='diagnostics/capture_full.ppm')
            self._baseline_done = True

        return self._cam

    def _window_region_in_output(self, *, rect_client: Any, frame_w: int, frame_h: int) -> tuple[tuple[int, int, int, int], dict[str, object]]:
        # Convert from virtual screen coords to output-local coords by subtracting monitor origin.
        mon_rect = self._monitor_rect
        mon_left = int(getattr(mon_rect, 'left', 0) or 0) if mon_rect is not None else 0
        mon_top = int(getattr(mon_rect, 'top', 0) or 0) if mon_rect is not None else 0

        x = int(getattr(rect_client, 'left', 0) or 0) - mon_left
        y = int(getattr(rect_client, 'top', 0) or 0) - mon_top
        w = int(getattr(rect_client, 'width', 0) or 0)
        h = int(getattr(rect_client, 'height', 0) or 0)

        ok, why = _validate_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))

        clamp = _env_bool('FRBOT_CAPTURE_CLAMP', False)
        clamped = False
        orig = [int(x), int(y), int(w), int(h)]
        if (not ok) and clamp:
            x, y, w, h = _clamp_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))
            clamped = True
            ok, why = _validate_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))

        details: dict[str, object] = {
            'frame_size': _frame_size_dict(int(frame_w), int(frame_h)),
            'requested_region': orig,
            'clamp_enabled': bool(clamp),
            'clamped': bool(clamped),
            'clamped_region': [int(x), int(y), int(w), int(h)] if clamped else None,
            'validation': why or ('ok' if ok else 'invalid'),
            'monitor': {'device': str(self._monitor_device or ''), 'rect': _rect_dict(self._monitor_rect) if self._monitor_rect is not None else None},
            'output': {'output_idx': int(self._output_idx or 0), 'name': str(self._output_name or '')},
        }

        if not ok:
            exc = PreflightFailed('invalid_capture_region')
            setattr(exc, 'details', details)
            raise exc

        # dxcam region is (left, top, right, bottom)
        return (int(x), int(y), int(x) + int(w), int(y) + int(h)), details

    def verify(self) -> VerificationResult:
        try:
            ok_mon, why = _dxcam_has_monitors(self._dxcam)
            if not ok_mon:
                return VerificationResult(ok=False, reason=why or 'no_monitors')

            snap = self._binding.snapshot()
            hwnd = int(getattr(snap, 'hwnd', 0) or 0)
            fg = int(w32.get_foreground_window())
            if hwnd <= 0 or fg != hwnd:
                return VerificationResult(ok=False, reason='window_not_foreground')

            rect_client = w32.get_client_rect_in_screen(hwnd)
            if rect_client.width <= 0 or rect_client.height <= 0:
                return VerificationResult(ok=False, reason='hwnd_rect_invalid')

            cam = self._ensure(hwnd=hwnd, rect_client=rect_client)
            if cam is None:
                return VerificationResult(ok=False, reason='capture_unavailable')

            # Baseline full-frame is already captured by _ensure(). Use real output size for validation.
            fw = int(getattr(cam, 'width', 0) or 0)
            fh = int(getattr(cam, 'height', 0) or 0)
            if fw <= 0 or fh <= 0:
                # Try a full grab as last resort.
                fw, fh = _grab_full_baseline(cam, out_path='diagnostics/capture_full.ppm')
            if fw <= 0 or fh <= 0:
                return VerificationResult(ok=False, reason='frame_empty')

            region, _region_details = self._window_region_in_output(rect_client=rect_client, frame_w=fw, frame_h=fh)
            frame = cam.grab(region=region)
            rgb, w, h, _meta = _to_rgb_bytes(frame)
            if not rgb or w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='frame_empty')

            _mean, std, all_zero = _sample_luma_stats(rgb, width=w, height=h)
            if all_zero or std <= 5.0:
                return VerificationResult(ok=False, reason='capture_black_or_unavailable')

            return VerificationResult(ok=True)
        except ImportError as exc:  # pragma: no cover
            return VerificationResult(ok=False, reason=str(exc))
        except PreflightFailed:
            raise
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, 'hwnd', 0) or 0)
        rect_client = w32.get_client_rect_in_screen(hwnd)
        cam = self._ensure(hwnd=hwnd, rect_client=rect_client)
        fw = int(getattr(cam, 'width', 0) or 0)
        fh = int(getattr(cam, 'height', 0) or 0)
        if fw <= 0 or fh <= 0:
            fw, fh = _grab_full_baseline(cam, out_path='diagnostics/capture_full.ppm')
        region, _region_details = self._window_region_in_output(rect_client=rect_client, frame_w=fw, frame_h=fh)
        frame = cam.grab(region=region)
        ts_ns = time.monotonic_ns()
        rgb, w, h, _meta = _to_rgb_bytes(frame)
        mean, std, all_zero = _sample_luma_stats(rgb, width=int(w), height=int(h))
        if (not rgb) or bool(all_zero) or float(std) <= 0.0001:
            failing = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts_ns), digest_hex='', rgb=rgb)
            if dump_enabled():
                dump_pair(gate='capture', before=failing, after=None, reason='capture_invalid')
            raise PreflightFailed('capture_invalid')
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''
        return Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts_ns), digest_hex=str(digest), rgb=rgb)


class MeldBoundMinimapRealCapture(CaptureAdapter):
    """REAL capture using DXGI framebuffer capture (dxcam) cropped to HWND client rect.

    This remains HWND-strict at the output level:
    - Foreground HWND invariant is enforced by the binding (callers) and in grab().
    - Region is recomputed from the HWND client rect each grab.

    Note: this is an alternative backend; it is not used unless explicitly selected.
    """

    name = 'meld'

    def __init__(self, minimap_roi: Roi, *, binding: WindowBindingAdapter) -> None:
        try:
            import dxcam  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: dxcam') from exc

        self._dxcam = dxcam
        self._cam: Any | None = None
        self._binding = binding
        self._minimap_roi = minimap_roi

        self._output_idx: int | None = None
        self._output_name: str = ''
        self._monitor_device: str = ''
        self._monitor_rect: Any | None = None
        self._baseline_done: bool = False

    def _resolve_output_for_hwnd(self, hwnd: int, rect_client: Any) -> dict[str, object]:
        mons = w32.list_monitors()
        cx = (int(getattr(rect_client, 'left', 0) or 0) + int(getattr(rect_client, 'right', 0) or 0)) // 2
        cy = (int(getattr(rect_client, 'top', 0) or 0) + int(getattr(rect_client, 'bottom', 0) or 0)) // 2
        mon = w32.monitor_for_point(cx, cy, mons) or w32.primary_monitor(mons)
        if mon is None:
            return {'device': '', 'rect': None, 'primary': True}
        return {'device': str(mon.device), 'rect': mon.rect, 'primary': bool(mon.primary)}

    def _ensure(self, *, hwnd: int, rect_client: Any) -> Any:
        target = self._resolve_output_for_hwnd(hwnd, rect_client)
        device = str(target.get('device') or '')
        mon_rect = target.get('rect')

        forced_idx = _env_int('FRBOT_MELD_OUTPUT_IDX')

        output_map = _dxcam_output_map(self._dxcam)
        desired_idx = 0
        if forced_idx is not None:
            desired_idx = int(forced_idx)
        elif device and device in output_map:
            desired_idx = _as_int(output_map[device].get('output_idx', 0), 0)

        if self._cam is None or self._output_idx != int(desired_idx):
            self._cam = self._dxcam.create(output_idx=int(desired_idx))
            self._output_idx = int(desired_idx)
            try:
                o = getattr(self._cam, '_output', None)
                self._output_name = str(getattr(o, 'name', '') or '')
            except Exception:
                self._output_name = ''

        self._monitor_device = device
        self._monitor_rect = mon_rect

        if not self._baseline_done:
            _grab_full_baseline(self._cam, out_path='diagnostics/capture_full.ppm')
            self._baseline_done = True

        return self._cam

    def _window_region_in_output(self, *, rect_client: Any, frame_w: int, frame_h: int) -> tuple[tuple[int, int, int, int], dict[str, object]]:
        mon_rect = self._monitor_rect
        mon_left = int(getattr(mon_rect, 'left', 0) or 0) if mon_rect is not None else 0
        mon_top = int(getattr(mon_rect, 'top', 0) or 0) if mon_rect is not None else 0

        x = int(getattr(rect_client, 'left', 0) or 0) - mon_left
        y = int(getattr(rect_client, 'top', 0) or 0) - mon_top
        w = int(getattr(rect_client, 'width', 0) or 0)
        h = int(getattr(rect_client, 'height', 0) or 0)

        ok, why = _validate_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))

        clamp = _env_bool('FRBOT_CAPTURE_CLAMP', False)
        clamped = False
        orig = [int(x), int(y), int(w), int(h)]
        if (not ok) and clamp:
            x, y, w, h = _clamp_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))
            clamped = True
            ok, why = _validate_region(frame_w=int(frame_w), frame_h=int(frame_h), x=int(x), y=int(y), w=int(w), h=int(h))

        details: dict[str, object] = {
            'frame_size': _frame_size_dict(int(frame_w), int(frame_h)),
            'requested_region': orig,
            'clamp_enabled': bool(clamp),
            'clamped': bool(clamped),
            'clamped_region': [int(x), int(y), int(w), int(h)] if clamped else None,
            'validation': why or ('ok' if ok else 'invalid'),
            'monitor': {'device': str(self._monitor_device or ''), 'rect': _rect_dict(self._monitor_rect) if self._monitor_rect is not None else None},
            'output': {'output_idx': int(self._output_idx or 0), 'name': str(self._output_name or '')},
        }

        if not ok:
            exc = PreflightFailed('invalid_capture_region')
            setattr(exc, 'details', details)
            raise exc

        return (int(x), int(y), int(x) + int(w), int(y) + int(h)), details

    def verify(self) -> VerificationResult:
        try:
            ok_mon, why = _dxcam_has_monitors(self._dxcam)
            if not ok_mon:
                return VerificationResult(ok=False, reason=why or 'no_monitors')

            snap = self._binding.snapshot()
            hwnd = int(getattr(snap, 'hwnd', 0) or 0)
            fg = int(w32.get_foreground_window())
            if hwnd <= 0 or fg != hwnd:
                return VerificationResult(ok=False, reason='window_not_foreground')

            rect_client = w32.get_client_rect_in_screen(hwnd)
            if rect_client.width <= 0 or rect_client.height <= 0:
                return VerificationResult(ok=False, reason='hwnd_rect_invalid')

            cam = self._ensure(hwnd=hwnd, rect_client=rect_client)
            if cam is None:
                return VerificationResult(ok=False, reason='capture_unavailable')

            fw = int(getattr(cam, 'width', 0) or 0)
            fh = int(getattr(cam, 'height', 0) or 0)
            if fw <= 0 or fh <= 0:
                fw, fh = _grab_full_baseline(cam, out_path='diagnostics/capture_full.ppm')
            if fw <= 0 or fh <= 0:
                return VerificationResult(ok=False, reason='frame_empty')

            region, _region_details = self._window_region_in_output(rect_client=rect_client, frame_w=fw, frame_h=fh)
            frame = cam.grab(region=region)
            rgb, w, h, _meta = _to_rgb_bytes(frame)
            if not rgb or w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='frame_empty')

            mean, std, all_zero = _sample_luma_stats(rgb, width=w, height=h)
            if all_zero or std <= 5.0:
                return VerificationResult(ok=False, reason='capture_black_or_unavailable')

            return VerificationResult(ok=True)
        except ImportError as exc:  # pragma: no cover
            return VerificationResult(ok=False, reason=str(exc))
        except PreflightFailed:
            raise
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        snap = self._binding.snapshot()
        hwnd = int(getattr(snap, 'hwnd', 0) or 0)

        rect_client = w32.get_client_rect_in_screen(hwnd)
        cam = self._ensure(hwnd=hwnd, rect_client=rect_client)
        fw = int(getattr(cam, 'width', 0) or 0)
        fh = int(getattr(cam, 'height', 0) or 0)
        if fw <= 0 or fh <= 0:
            fw, fh = _grab_full_baseline(cam, out_path='diagnostics/capture_full.ppm')
        region, _region_details = self._window_region_in_output(rect_client=rect_client, frame_w=fw, frame_h=fh)
        frame = cam.grab(region=region)

        ts_ns = time.monotonic_ns()
        rgb, w, h, _meta = _to_rgb_bytes(frame)
        mean, std, all_zero = _sample_luma_stats(rgb, width=int(w), height=int(h))
        if (not rgb) or bool(all_zero) or float(std) <= 0.0001:
            failing = Frame(width=int(w), height=int(h), monotonic_ts_ns=int(ts_ns), digest_hex='', rgb=rgb)
            if dump_enabled():
                dump_pair(gate='capture', before=failing, after=None, reason='capture_invalid')
            raise PreflightFailed('capture_invalid')
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

        minimap_rgb = _crop_rgb(rgb, w, h, self._minimap_roi) if rgb else b''
        minimap_detected = bool(minimap_rgb)
        minimap_digest = hashlib.sha256(minimap_rgb).hexdigest() if minimap_rgb else ''

        return Frame(
            width=int(w),
            height=int(h),
            monotonic_ts_ns=int(ts_ns),
            digest_hex=str(digest),
            rgb=rgb,
            minimap_detected=minimap_detected,
            minimap_rgb=minimap_rgb,
            minimap_width=int(self._minimap_roi.width),
            minimap_height=int(self._minimap_roi.height),
            minimap_digest_hex=str(minimap_digest),
        )
