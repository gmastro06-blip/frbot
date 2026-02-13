from __future__ import annotations

import os

from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter, WindowBindingStatus, WindowRect


class MockWindowBinding(WindowBindingAdapter):
    name = 'mock-window'

    def __init__(self) -> None:
        self._hwnd = 1234
        self._rect = WindowRect(left=0, top=0, right=320, bottom=240)

    def verify(self) -> VerificationResult:
        ok = os.environ.get('FRBOT_MOCK_WINDOW_OK', '1') == '1'
        rect_ok = os.environ.get('FRBOT_MOCK_WINDOW_RECT_OK', '1') == '1'

        if not ok or not rect_ok:
            return VerificationResult(ok=False, reason='window_binding_lost')
        return VerificationResult(ok=True)

    def snapshot(self) -> WindowBindingStatus:
        return WindowBindingStatus(backend=self.name, verified=True, hwnd=self._hwnd, rect=self._rect)

    def assert_bound(self) -> None:
        if os.environ.get('FRBOT_MOCK_WINDOW_OK', '1') != '1':
            raise RuntimeError('window_binding_lost')
        if os.environ.get('FRBOT_MOCK_WINDOW_RECT_OK', '1') != '1':
            raise RuntimeError('window_binding_lost')
