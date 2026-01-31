from __future__ import annotations

from dataclasses import dataclass

from adapters.windows.win32 import find_window_by_title_substring, get_client_rect_in_screen, get_foreground_window, get_window_text, is_window
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter, WindowBindingStatus, WindowRect


@dataclass
class _Bound:
    hwnd: int
    rect: WindowRect


class Win32WindowBinding(WindowBindingAdapter):
    name = 'win32'

    def __init__(self, *, hwnd: int = 0, title_substring: str = '') -> None:
        self._hwnd = int(hwnd)
        self._title_substring = str(title_substring or '')
        self._bound: _Bound | None = None

    def _resolve(self) -> _Bound:
        hwnd = int(self._hwnd)
        if hwnd <= 0:
            match = find_window_by_title_substring(self._title_substring)
            if match is None:
                raise RuntimeError('window_binding_lost')
            hwnd = int(match.hwnd)

        if hwnd <= 0 or not is_window(hwnd):
            raise RuntimeError('window_binding_lost')

        rect = get_client_rect_in_screen(hwnd)
        if rect.width <= 0 or rect.height <= 0:
            raise RuntimeError('window_binding_lost')

        return _Bound(hwnd=hwnd, rect=rect)

    def verify(self) -> VerificationResult:
        try:
            bound = self._resolve()
            # Must be foreground at verification time.
            if get_foreground_window() != int(bound.hwnd):
                return VerificationResult(ok=False, reason='window_binding_lost')
            self._bound = bound
            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=str(exc) or 'window_binding_lost')

    def snapshot(self) -> WindowBindingStatus:
        if self._bound is None:
            b = self._resolve()
        else:
            b = self._bound
        return WindowBindingStatus(backend=self.name, verified=True, hwnd=int(b.hwnd), rect=b.rect)

    def assert_bound(self) -> None:
        if self._bound is None:
            raise RuntimeError('window_binding_lost')
        if not is_window(int(self._bound.hwnd)):
            raise RuntimeError('window_binding_lost')
        if get_foreground_window() != int(self._bound.hwnd):
            raise RuntimeError('window_binding_lost')

        if self._title_substring.strip():
            title_now = get_window_text(int(self._bound.hwnd))
            if self._title_substring.strip().lower() not in (title_now or '').lower():
                raise RuntimeError('window_binding_lost')

        rect_now = get_client_rect_in_screen(int(self._bound.hwnd))
        if rect_now != self._bound.rect:
            raise RuntimeError('window_binding_lost')
