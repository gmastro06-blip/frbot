from __future__ import annotations

from contracts.capture import CaptureAdapter, Frame
from contracts.verification import VerificationResult

from adapters.mock_world import MockWorld


class MockWorldAnyCapture(CaptureAdapter):
	name = 'mock-world-any'

	def __init__(self, world: MockWorld, verified: bool) -> None:
		self._world = world
		self._verified = verified

	def verify(self) -> VerificationResult:
		if not self._verified:
			return VerificationResult(ok=False, reason='mock capture not verified')
		f = self._world.frame()
		if not f.rgb or not f.digest_hex:
			return VerificationResult(ok=False, reason='mock capture missing rgb/digest')
		return VerificationResult(ok=True)

	def grab(self) -> Frame:
		return self._world.frame()
