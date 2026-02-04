from __future__ import annotations

import ctypes
from ctypes import wintypes

from adapters.windows.win32 import is_window, is_window_minimized, is_window_visible
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

MAPVK_VK_TO_VSC = 0

VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_TAB = 0x09

VK_F1 = 0x70

VK_NUMPAD0 = 0x60


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

    if k in {'enter', 'return'}:
        return VK_RETURN
    if k in {'esc', 'escape'}:
        return VK_ESCAPE
    if k == 'space':
        return VK_SPACE
    if k == 'tab':
        return VK_TAB

    # Function keys.
    if k.startswith('f') and k[1:].isdigit():
        n = int(k[1:])
        if 1 <= n <= 24:
            return int(VK_F1 + (n - 1))

    # Numpad keys.
    if k.startswith('numpad') and k[len('numpad') :].isdigit():
        n = int(k[len('numpad') :])
        if 0 <= n <= 9:
            return int(VK_NUMPAD0 + n)

    # Single ASCII alnum keys (A-Z, 0-9). Virtual-key codes match ASCII.
    if len(k) == 1 and k.isalnum():
        c = k.upper()
        return ord(c)

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

    def assert_bound(self, hwnd: int | None = None) -> None:
        expected = int(self._hwnd)
        if hwnd is not None and int(hwnd) != expected:
            raise RuntimeError('window_binding_lost')
        if expected <= 0 or not is_window(expected):
            raise RuntimeError('window_binding_lost')
        if not is_window_visible(expected):
            raise RuntimeError('window_binding_lost')
        if is_window_minimized(expected):
            raise RuntimeError('window_binding_lost')

    def press_noop(self) -> None:
        # Deterministic no-op: do nothing.
        return

    def press_key(self, key: str) -> None:
        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        vk = _vk_for_key(key)
        hwnd = wintypes.HWND(self._hwnd)

        # Build lParam with scan code and proper transition flags.
        # Many game clients ignore WM_KEY* messages with lParam=0.
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0
        extended = 1 if int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT} else 0
        lparam_down = wintypes.LPARAM(1 | (sc << 16) | (extended << 24))
        # Previous key state (bit 30) + transition state (bit 31).
        lparam_up = wintypes.LPARAM(1 | (sc << 16) | (extended << 24) | (1 << 30) | (1 << 31))

        # PostMessage returns nonzero on success.
        if not user32.PostMessageW(hwnd, WM_KEYDOWN, wintypes.WPARAM(vk), lparam_down):
            raise RuntimeError('window_binding_lost')
        if not user32.PostMessageW(hwnd, WM_KEYUP, wintypes.WPARAM(vk), lparam_up):
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
