from __future__ import annotations

import time
import hashlib
from typing import Any

from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.verification import VerificationResult


class MssRealCapture(CaptureAdapter):
    name = 'mss'

    def __init__(self, minimap_roi: Roi, *, region: dict[str, int]) -> None:
        try:
            import mss  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: mss') from exc

        self._mss_mod = mss
        self._sct: Any | None = None
        self._minimap_roi = minimap_roi
        self._region = dict(region)

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

    def _ensure(self) -> Any:
        if self._sct is None:
            self._sct = self._mss_mod.mss()
        return self._sct

    def verify(self) -> VerificationResult:
        try:
            sct = self._ensure()
            now_ns = time.monotonic_ns()
            img = sct.grab(self._region)
            ts_ns = time.monotonic_ns()

            w = int(getattr(img, 'width', 0) or 0)
            h = int(getattr(img, 'height', 0) or 0)
            if w <= 0 or h <= 0:
                return VerificationResult(ok=False, reason='captured frame has invalid resolution')

            # Objective "not empty": ensure pixel buffer exists and is non-zero length.
            rgb = getattr(img, 'rgb', None)
            if rgb is None:
                return VerificationResult(ok=False, reason='captured frame has no rgb buffer')
            if len(rgb) == 0:
                return VerificationResult(ok=False, reason='captured frame rgb buffer is empty')


            minimap_rgb = self._crop_rgb(rgb, w, h, self._minimap_roi)
            if not minimap_rgb:
                return VerificationResult(ok=False, reason='minimap_not_detected')

            digest = hashlib.sha256(rgb).hexdigest()
            if len(digest) != 64:
                return VerificationResult(ok=False, reason='captured frame digest invalid')


            minimap_digest = hashlib.sha256(minimap_rgb).hexdigest()
            if len(minimap_digest) != 64:
                return VerificationResult(ok=False, reason='minimap digest invalid')

            # Objective recency: capture timestamp should be close to now.
            if (ts_ns - now_ns) < 0 or (ts_ns - now_ns) > 1_000_000_000:
                return VerificationResult(ok=False, reason='capture timestamp not recent')

            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        sct = self._ensure()
        img = sct.grab(self._region)
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
