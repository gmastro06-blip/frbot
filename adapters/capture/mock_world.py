from __future__ import annotations

from contracts.capture import CaptureAdapter, Frame
from contracts.verification import VerificationResult

from adapters.mock_world import MockWorld


class MockWorldCapture(CaptureAdapter):
	name = 'mock-world'

	def __init__(self, world: MockWorld, verified: bool) -> None:
		self._world = world
		self._verified = verified

	def verify(self) -> VerificationResult:
		if not self._verified:
			return VerificationResult(ok=False, reason='mock capture not verified')
		# Objective check: frame includes rgb and digest + minimap digest.
		f = self._world.frame()
		if not f.rgb or not f.digest_hex:
			return VerificationResult(ok=False, reason='mock capture missing rgb/digest')
		if not f.minimap_detected or not f.minimap_digest_hex:
			return VerificationResult(ok=False, reason='mock capture missing minimap evidence')
		return VerificationResult(ok=True)

	def grab(self) -> Frame:
		return self._world.frame()
