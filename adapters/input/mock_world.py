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

	def assert_bound(self, hwnd: int | None = None) -> None:
		return

	def press_noop(self) -> None:
		self._world.on_noop()

	def press_key(self, key: str) -> None:
		self._world.on_key(key)

	def key_down(self, key: str) -> None:
		# key_down without a paired key_up has no special meaning in the mock world;
		# treat as a regular key press so held-key and press-key behave identically here.
		self._world.on_key(key)

	def key_up(self, key: str) -> None:
		# Releasing a held key stops movement; no further on_key calls needed.
		return

	def auto_walk_tick(self, key: str) -> None:
		# Called each same-direction tick while key is held (Tibia auto-walks).
		# Mock world simulates this by executing one explicit movement step.
		self._world.on_key(key)

	def click(self, x: int, y: int) -> None:
		self._world.on_click(int(x), int(y))

	def click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
		return self.click(int(x), int(y))

	def right_click(self, x: int, y: int) -> None:
		# Optional extension used by some REAL adapters.
		# Mock world does not distinguish buttons.
		self._world.on_click(int(x), int(y))

	def right_click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
		return self.right_click(int(x), int(y))

	def click_cursor(self) -> None:
		# Mock world has no cursor; use a sentinel click.
		self._world.on_click(-1, -1)

	def shift_right_click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
		# Mock world does not model modifier state; treat as a click.
		return self.right_click_frame(int(x), int(y), frame_w=int(frame_w), frame_h=int(frame_h))