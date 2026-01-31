from __future__ import annotations

from contracts.input import InputAdapter
from contracts.verification import VerificationResult

from adapters.mock_world import MockWorld


class MockWorldInput(InputAdapter):
	name = 'mock-world'

	def __init__(self, world: MockWorld, verified: bool) -> None:
		self._world = world
		self._verified = verified

	def verify(self) -> VerificationResult:
		if self._verified:
			return VerificationResult(ok=True)
		return VerificationResult(ok=False, reason='mock input not verified')

	def press_noop(self) -> None:
		self._world.on_noop()

	def press_key(self, key: str) -> None:
		self._world.on_key(key)

	def click(self, x: int, y: int) -> None:
		self._world.on_click(int(x), int(y))