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

    def press_noop(self) -> None:
        """Emit a configurable no-op key press used for verification."""

    def press_key(self, key: str) -> None:
        """Press/release a single key (e.g. 'F1', 'a', '1')."""

    def click(self, x: int, y: int) -> None:
        """Perform an input action. Must only run after successful preflight."""
