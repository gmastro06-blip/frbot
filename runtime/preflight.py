from __future__ import annotations

import os

from adapters.capture.mock import MockCapture
from adapters.capture.mss_real import MssRealCapture
from adapters.input.mock import MockInput
from adapters.input.pynput_real import PynputRealKeyboard
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState


def preflight(ctx: RuntimeContext):
    """Validate environment + adapters + contracts.

    Invariant: if anything is not verifiable -> abort.
    """
    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        try:
            capture = MssRealCapture()
        except ImportError as exc:
            raise PreflightFailed(str(exc)) from exc

        try:
            input_ = PynputRealKeyboard()
        except ImportError as exc:
            raise PreflightFailed(str(exc)) from exc

        cap_v = capture.verify()
        inp_v = input_.verify()

        ctx.capture = CaptureStatus(backend=capture.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_.name, verified=False)

        if not cap_v.ok:
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        # Round-trip verification: input must cause an observable capture delta.
        before = capture.grab()
        try:
            input_.press_noop()
        except Exception as exc:
            raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc
        after = capture.grab()

        if before.digest_hex == after.digest_hex:
            raise PreflightFailed('input round-trip not observed (no capture delta)')

        ctx.input = InputStatus(backend=input_.name, verified=True)

        ctx.status.state = RuntimeState.READY
        return capture, input_

    # mock mode: deterministic adapters. Verification is explicit.
    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    capture = MockCapture(verified=cap_ok)
    input_ = MockInput(verified=inp_ok)

    verified_capture = bool(capture.verify().ok)
    verified_input = bool(input_.verify().ok)

    ctx.capture = CaptureStatus(backend=capture.name, verified=verified_capture)
    ctx.input = InputStatus(backend=input_.name, verified=verified_input)

    if not verified_capture:
        raise PreflightFailed('capture not verified')
    if not verified_input:
        raise PreflightFailed('input not verified')

    ctx.status.state = RuntimeState.READY
    return capture, input_
