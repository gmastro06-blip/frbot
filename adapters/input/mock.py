from __future__ import annotations

from contracts.input import InputAdapter
from contracts.verification import VerificationResult


class MockInput(InputAdapter):
    name = 'mock'

    def __init__(self, verified: bool) -> None:
        self._verified = verified

    def verify(self) -> VerificationResult:
        if self._verified:
            return VerificationResult(ok=True)
        return VerificationResult(ok=False, reason='mock input not verified')

    def press_noop(self) -> None:
        return

    def click(self, x: int, y: int) -> None:
        # Deterministic no-op.
        return
