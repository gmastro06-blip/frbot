from __future__ import annotations

from contracts.errors import PreflightFailed
from contracts.capture import CaptureStatus
from contracts.input import InputStatus


def require_verified_capture(status: CaptureStatus) -> None:
    if not status.verified:
        raise PreflightFailed('capture not verified')


def require_verified_input(status: InputStatus) -> None:
    if not status.verified:
        raise PreflightFailed('input not verified')
