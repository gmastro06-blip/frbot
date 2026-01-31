from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .verification import VerificationResult


@dataclass(frozen=True, slots=True)
class Frame:
    width: int
    height: int
    monotonic_ts_ns: int
    digest_hex: str


@dataclass(frozen=True, slots=True)
class CaptureStatus:
    backend: str
    verified: bool


class CaptureAdapter(Protocol):
    name: str

    def verify(self) -> VerificationResult:
        """Return ok=True only if capture is verified functional."""

    def grab(self) -> Frame:
        """Grab a frame. Must only be called after successful preflight."""
