from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

from contracts.window import WindowRect


user32 = ctypes.WinDLL('user32', use_last_error=True)


@dataclass(frozen=True, slots=True)
class HwndMatch:
    hwnd: int
    title: str


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def get_window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def get_client_rect_in_screen(hwnd: int) -> WindowRect:
    # client rect (0,0,w,h)
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise RuntimeError('GetClientRect failed')

    # Convert client origin to screen coords
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        raise RuntimeError('ClientToScreen failed')

    left = int(pt.x)
    top = int(pt.y)
    right = left + int(rect.right - rect.left)
    bottom = top + int(rect.bottom - rect.top)
    return WindowRect(left=left, top=top, right=right, bottom=bottom)


def find_window_by_title_substring(substr: str) -> Optional[HwndMatch]:
    needle = (substr or '').strip().lower()
    if not needle:
        return None

    best: Optional[HwndMatch] = None

    def on_enum(hwnd: int) -> bool:
        nonlocal best
        title = get_window_text(hwnd)
        if not title:
            return True
        if needle in title.lower():
            best = HwndMatch(hwnd=int(hwnd), title=title)
            return False
        return True

    def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> wintypes.BOOL:
        try:
            return wintypes.BOOL(on_enum(int(hwnd)))
        except Exception:
            return wintypes.BOOL(True)

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return best
