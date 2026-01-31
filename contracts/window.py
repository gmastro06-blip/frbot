from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .verification import VerificationResult


@dataclass(frozen=True, slots=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, int(self.right) - int(self.left))

    @property
    def height(self) -> int:
        return max(0, int(self.bottom) - int(self.top))


@dataclass(frozen=True, slots=True)
class WindowBindingStatus:
    backend: str
    verified: bool
    hwnd: int
    rect: WindowRect


class WindowBindingAdapter(Protocol):
    name: str

    def verify(self) -> VerificationResult:
        """Return ok=True only if binding is verified functional."""

    def snapshot(self) -> WindowBindingStatus:
        """Return current binding snapshot (must be stable when verified)."""

    def assert_bound(self) -> None:
        """Raise a RuntimeError if the binding is lost (hwnd invalid, not foreground, rect mismatch, etc)."""
