from __future__ import annotations

import hashlib
import time
from typing import Any

from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter


def _rect_to_region(binding: WindowBindingAdapter) -> dict[str, int]:
    snap = binding.snapshot()
    return {
        'left': int(snap.rect.left),
        'top': int(snap.rect.top),
        'width': int(snap.rect.width),
        'height': int(snap.rect.height),
    }


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
            img = sct.grab(region)
            ts_ns = time.monotonic_ns()

            w = int(getattr(img, 'width', 0) or 0)
            h = int(getattr(img, 'height', 0) or 0)
            if w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='captured frame has invalid resolution')

            rgb = getattr(img, 'rgb', None)
            if rgb is None or len(rgb) == 0:
                return VerificationResult(ok=False, reason='captured frame has no rgb buffer')

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
        region = _rect_to_region(self._binding)
        img = sct.grab(region)
        ts_ns = time.monotonic_ns()
        w = int(getattr(img, 'width', 0) or 0)
        h = int(getattr(img, 'height', 0) or 0)
        rgb = getattr(img, 'rgb', b'')
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''
        return Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex=digest, rgb=rgb or b'')


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
            img = sct.grab(region)
            ts_ns = time.monotonic_ns()

            w = int(getattr(img, 'width', 0) or 0)
            h = int(getattr(img, 'height', 0) or 0)
            if w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='captured frame has invalid resolution')

            rgb = getattr(img, 'rgb', None)
            if rgb is None or len(rgb) == 0:
                return VerificationResult(ok=False, reason='captured frame has no rgb buffer')

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
        region = _rect_to_region(self._binding)
        img = sct.grab(region)
        ts_ns = time.monotonic_ns()
        w = int(getattr(img, 'width', 0) or 0)
        h = int(getattr(img, 'height', 0) or 0)
        rgb = getattr(img, 'rgb', b'')
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''

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
