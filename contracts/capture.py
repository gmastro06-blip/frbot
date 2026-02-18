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
    # Raw RGB bytes (len == width * height * 3) when available.
    # Core engine must not interpret pixels; runtime/evidence may.
    rgb: bytes = b""

    # Cavebot-only movement evidence.
    minimap_detected: bool = False
    # Raw RGB bytes for the minimap crop (len == minimap_width * minimap_height * 3).
    minimap_rgb: bytes = b""
    minimap_width: int = 0
    minimap_height: int = 0
    minimap_digest_hex: str = ""


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
