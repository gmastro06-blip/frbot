from __future__ import annotations

import ctypes
from ctypes import wintypes

from adapters.windows.win32 import is_window
from contracts.input import InputAdapter
from contracts.verification import VerificationResult


user32 = ctypes.WinDLL('user32', use_last_error=True)

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27


def _vk_for_key(key: str) -> int:
    k = (key or '').strip().lower()
    if k == 'up':
        return VK_UP
    if k == 'down':
        return VK_DOWN
    if k == 'left':
        return VK_LEFT
    if k == 'right':
        return VK_RIGHT
    raise KeyError(key)


class Win32HwndKeyboard(InputAdapter):
    """Keyboard adapter that targets a specific HWND via PostMessage.

    This avoids relying on global "foreground" dispatch (reduces risk of sending input to the wrong UI).
    """

    name = 'win32-hwnd'

    def __init__(self, hwnd: int) -> None:
        self._hwnd = int(hwnd)

    def verify(self) -> VerificationResult:
        if self._hwnd <= 0 or not is_window(self._hwnd):
            return VerificationResult(ok=False, reason='window_binding_lost')
        return VerificationResult(ok=True)

    def press_noop(self) -> None:
        # Deterministic no-op: do nothing.
        return

    def press_key(self, key: str) -> None:
        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        vk = _vk_for_key(key)
        hwnd = wintypes.HWND(self._hwnd)

        # PostMessage returns nonzero on success.
        if not user32.PostMessageW(hwnd, WM_KEYDOWN, wintypes.WPARAM(vk), wintypes.LPARAM(0)):
            raise RuntimeError('window_binding_lost')
        if not user32.PostMessageW(hwnd, WM_KEYUP, wintypes.WPARAM(vk), wintypes.LPARAM(0)):
            raise RuntimeError('window_binding_lost')

    def click(self, x: int, y: int) -> None:
        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        # Coordinates are expected in *client* pixels.
        cx = int(max(0, min(65535, int(x))))
        cy = int(max(0, min(65535, int(y))))
        lparam = (cy << 16) | cx

        hwnd = wintypes.HWND(self._hwnd)
        if not user32.PostMessageW(hwnd, WM_LBUTTONDOWN, wintypes.WPARAM(MK_LBUTTON), wintypes.LPARAM(lparam)):
            raise RuntimeError('window_binding_lost')
        if not user32.PostMessageW(hwnd, WM_LBUTTONUP, wintypes.WPARAM(0), wintypes.LPARAM(lparam)):
            raise RuntimeError('window_binding_lost')
