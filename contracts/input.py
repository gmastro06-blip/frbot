from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .verification import VerificationResult


@dataclass(frozen=True, slots=True)
class InputStatus:
    backend: str
    verified: bool


class InputAdapter(Protocol):
    name: str

    def verify(self) -> VerificationResult:
        """Return ok=True only if input is verified safe to use."""

    def assert_bound(self, hwnd: int | None = None) -> None:
        """Hard-check that input is still bound to the expected window.

        Implementations that do not target a specific HWND may treat this as a no-op.
        """

    def press_noop(self) -> None:
        """Emit a configurable no-op key press used for verification."""

    def press_key(self, key: str) -> None:
        """Press/release a single key (e.g. 'F1', 'a', '1')."""

    def key_down(self, key: str) -> None:
        """Press and hold a key without releasing it.

        Used for smooth continuous walking when FRBOT_INPUT_METHOD=sendinput.
        Default fallback: full press+release via press_key (step-by-step mode).
        Adapters that support native hold (sendinput/pynput) should override this.
        """
        self.press_key(key)

    def key_up(self, key: str) -> None:
        """Release a previously held key.

        Used to stop continuous walking when direction changes or waypoint is reached.
        Default fallback: no-op (press_key already released the key immediately).
        Adapters that support native hold (sendinput/pynput) should override this.
        """

    def auto_walk_tick(self, key: str) -> None:
        """Called once per tick while a direction key is held and the direction is unchanged.

        In real-input adapters this is a no-op: the OS/game already auto-walks because
        the key is physically held down.  Mock-world adapters override this to simulate
        one step of continuous movement per tick so tests stay deterministic.
        Default: no-op.
        """

    def click(self, x: int, y: int) -> None:
        """Perform an input action. Must only run after successful preflight."""
