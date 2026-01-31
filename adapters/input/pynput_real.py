from __future__ import annotations

import os
from typing import TYPE_CHECKING

from contracts.input import InputAdapter
from contracts.verification import VerificationResult

if TYPE_CHECKING:
    from pynput.keyboard import Key


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

    def _noop_key(self) -> Key:
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

    def press_key(self, key: str) -> None:
        raw = key.strip().lower()
        if not raw:
            raise ValueError('key must be non-empty')

        special_map = {
            'shift': self._Key.shift,
            'shift_l': self._Key.shift_l,
            'shift_r': self._Key.shift_r,
            'ctrl': self._Key.ctrl,
            'ctrl_l': self._Key.ctrl_l,
            'ctrl_r': self._Key.ctrl_r,
            'alt': self._Key.alt,
            'alt_l': self._Key.alt_l,
            'alt_r': self._Key.alt_r,
            'enter': self._Key.enter,
            'return': self._Key.enter,
            'space': self._Key.space,
            'tab': self._Key.tab,
            'esc': self._Key.esc,
            'escape': self._Key.esc,
            'up': self._Key.up,
            'down': self._Key.down,
            'left': self._Key.left,
            'right': self._Key.right,
        }
        if raw in special_map:
            resolved = special_map[raw]
            self._controller.press(resolved)
            self._controller.release(resolved)
            return

        if raw.startswith('f') and raw[1:].isdigit():
            n = int(raw[1:])
            if 1 <= n <= 12:
                resolved = getattr(self._Key, f'f{n}')
                self._controller.press(resolved)
                self._controller.release(resolved)
                return
            raise ValueError('only F1..F12 supported')

        if len(raw) == 1:
            # controller accepts chars for alnum and punctuation.
            self._controller.press(raw)
            self._controller.release(raw)
            return

        raise ValueError(f'unsupported key: {key!r}')

    def click(self, x: int, y: int) -> None:
        # Keyboard-only backend by contract.
        raise NotImplementedError('real input backend is keyboard-only (no mouse)')
