from __future__ import annotations

import os

from contracts.input import InputAdapter
from contracts.verification import VerificationResult


class PynputRealKeyboard(InputAdapter):
    name = 'pynput-keyboard'

    def __init__(self) -> None:
        try:
            from pynput.keyboard import Controller, Key  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ImportError('missing dependency: pynput') from exc

        self._Controller = Controller
        self._Key = Key
        self._controller = self._Controller()

    def _noop_key(self):
        raw = os.environ.get('FRBOT_REAL_NOOP_KEY', 'shift').strip().lower()
        # Only allow a small safe set.
        if raw == 'shift':
            return self._Key.shift
        if raw == 'shift_l':
            return self._Key.shift_l
        if raw == 'shift_r':
            return self._Key.shift_r
        # default safe
        return self._Key.shift

    def verify(self) -> VerificationResult:
        try:
            _ = self._noop_key()
            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=f'input verify failed: {type(exc).__name__}: {exc}')

    def press_noop(self) -> None:
        key = self._noop_key()
        self._controller.press(key)
        self._controller.release(key)

    def click(self, x: int, y: int) -> None:
        # Keyboard-only backend by contract.
        raise NotImplementedError('real input backend is keyboard-only (no mouse)')
