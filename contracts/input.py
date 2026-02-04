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

    def click(self, x: int, y: int) -> None:
        """Perform an input action. Must only run after successful preflight."""
