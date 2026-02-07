from __future__ import annotations

from dataclasses import dataclass

import time

from adapters.windows.win32 import find_window_by_title_substring, get_client_rect_in_screen, get_window_text, is_window
from contracts.verification import VerificationResult
from contracts.window import WindowBindingAdapter, WindowBindingStatus, WindowRect


@dataclass
class _Bound:
    hwnd: int
    title_substring: str


class Win32WindowBinding(WindowBindingAdapter):
    name = 'win32'

    def __init__(self, *, hwnd: int = 0, title_substring: str = '') -> None:
        self._hwnd = int(hwnd)
        self._title_substring = str(title_substring or '')
        self._bound: _Bound | None = None

    def _resolve(self) -> _Bound:
        hwnd = int(self._hwnd)
        title_sub = str(self._title_substring or '').strip()

        if hwnd > 0:
            try:
                if is_window(hwnd):
                    # If both HWND and title selector are provided, ensure they agree.
                    # HWNDs can be stale or refer to message-only/helper windows.
                    if title_sub:
                        try:
                            title_now = str(get_window_text(hwnd) or '')
                        except Exception:
                            title_now = ''
                        if title_sub.lower() not in title_now.lower():
                            hwnd = 0
                        else:
                            return _Bound(hwnd=hwnd, title_substring=title_sub)
                    else:
                        return _Bound(hwnd=hwnd, title_substring=title_sub)
            except Exception:
                pass

            # If the provided HWND is stale but we have a title selector, re-resolve.
            hwnd = 0

        if hwnd <= 0:
            match = find_window_by_title_substring(title_sub)
            if match is None:
                raise RuntimeError('window_binding_lost')
            hwnd = int(match.hwnd)

        if hwnd <= 0:
            raise RuntimeError('window_binding_lost')
        try:
            if not is_window(hwnd):
                raise RuntimeError('window_binding_lost')
        except Exception:
            raise RuntimeError('window_binding_lost')

        # Do not freeze a rect: window can move/resize; capture must follow.
        return _Bound(hwnd=hwnd, title_substring=title_sub)

    def verify(self) -> VerificationResult:
        try:
            bound = self._resolve()
            self._bound = bound
            return VerificationResult(ok=True)
        except Exception as exc:
            return VerificationResult(ok=False, reason=str(exc) or 'window_binding_lost')

    def snapshot(self) -> WindowBindingStatus:
        if self._bound is None:
            b = self._resolve()
        else:
            b = self._bound
        rect = get_client_rect_in_screen(int(b.hwnd))
        return WindowBindingStatus(backend=self.name, verified=True, hwnd=int(b.hwnd), rect=rect)

    def assert_bound(self) -> None:
        if self._bound is None:
            raise RuntimeError('window_binding_lost')

        title_sub = self._title_substring.strip()
        deadline_ns = time.monotonic_ns() + 1_000_000_000
        last_exc: Exception | None = None

        while True:
            try:
                hwnd_now = int(self._bound.hwnd)

                if not is_window(hwnd_now):
                    if title_sub:
                        self._bound = self._resolve()
                        hwnd_now = int(self._bound.hwnd)
                    else:
                        raise RuntimeError('window_binding_lost')

                if title_sub:
                    title_now = get_window_text(hwnd_now)
                    if title_sub.lower() not in (title_now or '').lower():
                        # Window can be recreated; try re-resolve once.
                        self._bound = self._resolve()
                        hwnd_now = int(self._bound.hwnd)
                        title_now = get_window_text(hwnd_now)
                        if title_sub.lower() not in (title_now or '').lower():
                            raise RuntimeError('window_binding_lost')

                rect_now = get_client_rect_in_screen(hwnd_now)
                if rect_now.width <= 0 or rect_now.height <= 0:
                    raise RuntimeError('window_binding_lost')
                return
            except Exception as exc:
                last_exc = exc
                if time.monotonic_ns() >= deadline_ns:
                    raise RuntimeError('window_binding_lost') from last_exc
                try:
                    from runtime.pacing import wait_until_ns

                    wait_until_ns(int(time.monotonic_ns() + 50_000_000))
                except Exception:
                    # Worst case: tight spin until deadline.
                    pass
