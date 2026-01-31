from __future__ import annotations

import time
import hashlib
from typing import Any

from contracts.capture import CaptureAdapter, Frame
from contracts.verification import VerificationResult


class MssRealCapture(CaptureAdapter):
    name = 'mss'

    def __init__(self) -> None:
        try:
            import mss  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: mss') from exc

        self._mss_mod = mss
        self._sct = None

    def _ensure(self):
        if self._sct is None:
            self._sct = self._mss_mod.mss()
        return self._sct

    def verify(self) -> VerificationResult:
        try:
            sct = self._ensure()
            monitors = getattr(sct, 'monitors', None)
            if not monitors or len(monitors) < 2:
                return VerificationResult(ok=False, reason='no monitors available')

            monitor = monitors[1]
            now_ns = time.monotonic_ns()
            img = sct.grab(monitor)
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

            digest = hashlib.sha256(rgb).hexdigest()
            if len(digest) != 64:
                return VerificationResult(ok=False, reason='captured frame digest invalid')

            # Objective recency: capture timestamp should be close to now.
            if (ts_ns - now_ns) < 0 or (ts_ns - now_ns) > 1_000_000_000:
                return VerificationResult(ok=False, reason='capture timestamp not recent')

            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'capture verify failed: {type(exc).__name__}: {exc}')

    def grab(self) -> Frame:
        sct = self._ensure()
        monitors = sct.monitors
        monitor = monitors[1]
        img = sct.grab(monitor)
        ts_ns = time.monotonic_ns()
        w = int(getattr(img, 'width', 0) or 0)
        h = int(getattr(img, 'height', 0) or 0)
        rgb = getattr(img, 'rgb', b'')
        digest = hashlib.sha256(rgb).hexdigest() if rgb else ''
        return Frame(width=w, height=h, monotonic_ts_ns=ts_ns, digest_hex=digest)
