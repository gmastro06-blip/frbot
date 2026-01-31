from __future__ import annotations

import time
import hashlib

from contracts.capture import CaptureAdapter, Frame
from contracts.verification import VerificationResult


class MockCapture(CaptureAdapter):
    name = 'mock'

    def __init__(self, verified: bool) -> None:
        self._verified = verified

    def verify(self) -> VerificationResult:
        if self._verified:
            return VerificationResult(ok=True)
        return VerificationResult(ok=False, reason='mock capture not verified')

    def grab(self) -> Frame:
        ts = time.monotonic_ns()
        digest = hashlib.sha256(str(ts).encode('utf-8')).hexdigest()
        return Frame(width=1, height=1, monotonic_ts_ns=ts, digest_hex=digest)
