from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

from contracts.window import WindowRect


_IS_WINDOWS = os.name == 'nt'
user32 = ctypes.WinDLL('user32', use_last_error=True) if _IS_WINDOWS else None
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True) if _IS_WINDOWS else None

_DPI_AWARENESS_DONE = False
_DPI_AWARENESS_RESULT: dict[str, object] = {'attempted': False, 'mode': None, 'ok': False, 'error': None}


def _best_effort_enable_dpi_awareness() -> None:
    """Best-effort enable DPI awareness for correct screen coordinates.

    If the Python process is DPI-unaware, Win32 APIs like ClientToScreen may
    return scaled (virtual) coordinates. mss expects physical pixel coordinates,
    so a mismatch can produce captures of the wrong screen region (often black).

    This helper never raises.
    """

    global _DPI_AWARENESS_DONE
    if _DPI_AWARENESS_DONE:
        return
    _DPI_AWARENESS_DONE = True

    _DPI_AWARENESS_RESULT['attempted'] = True

    if user32 is None:
        return

    # Prefer PER_MONITOR_AWARE_V2 when available.
    try:
        fn = getattr(user32, 'SetProcessDpiAwarenessContext', None)
        if fn is not None:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = wintypes.BOOL
            ok = bool(fn(ctypes.c_void_p(-4)))
            _DPI_AWARENESS_RESULT.update({'mode': 'per_monitor_v2', 'ok': ok, 'error': None})
            return
    except Exception:
        _DPI_AWARENESS_RESULT.update({'mode': 'per_monitor_v2', 'ok': False, 'error': 'SetProcessDpiAwarenessContext failed'})
        pass

    # Fallback: shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE=2)
    try:
        shcore = ctypes.WinDLL('shcore', use_last_error=True)
        fn2 = getattr(shcore, 'SetProcessDpiAwareness', None)
        if fn2 is not None:
            fn2.argtypes = [ctypes.c_int]
            fn2.restype = ctypes.c_int
            rc = int(fn2(2))
            _DPI_AWARENESS_RESULT.update({'mode': 'per_monitor', 'ok': rc == 0, 'error': None if rc == 0 else f'SetProcessDpiAwareness rc={rc}'})
            return
    except Exception:
        _DPI_AWARENESS_RESULT.update({'mode': 'per_monitor', 'ok': False, 'error': 'SetProcessDpiAwareness failed'})
        pass

    # Last resort: user32.SetProcessDPIAware (system DPI aware).
    try:
        fn3 = getattr(user32, 'SetProcessDPIAware', None)
        if fn3 is not None:
            fn3.argtypes = []
            fn3.restype = wintypes.BOOL
            ok = bool(fn3())
            _DPI_AWARENESS_RESULT.update({'mode': 'system', 'ok': ok, 'error': None})
    except Exception:
        _DPI_AWARENESS_RESULT.update({'mode': 'system', 'ok': False, 'error': 'SetProcessDPIAware failed'})
        pass


def get_dpi_awareness_status() -> dict[str, object]:
    # Ensure we attempted once if on Windows.
    if _IS_WINDOWS:
        _best_effort_enable_dpi_awareness()
    return dict(_DPI_AWARENESS_RESULT)


def get_window_rect_in_screen(hwnd: int) -> WindowRect:
    u32 = _require_windows()
    rect = wintypes.RECT()
    if not u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise RuntimeError('GetWindowRect failed')
    return WindowRect(left=int(rect.left), top=int(rect.top), right=int(rect.right), bottom=int(rect.bottom))


def get_window_process_id(hwnd: int) -> int:
    u32 = _require_windows()
    pid = wintypes.DWORD(0)
    u32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value)


def can_query_process(pid: int) -> tuple[bool, str | None]:
    if not _IS_WINDOWS:
        return True, None
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(int(pid)))
        if not handle:
            err = ctypes.get_last_error()
            return False, f'OpenProcess denied (err={err})'
        try:
            return True, None
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        return False, f'OpenProcess probe failed: {type(exc).__name__}: {exc}'


@dataclass(frozen=True, slots=True)
class HwndMatch:
    hwnd: int
    title: str


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    visible: bool
    minimized: bool


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    device: str
    rect: WindowRect
    primary: bool


@dataclass(frozen=True, slots=True)
class WindowDiagnosticInfo:
    hwnd: int
    title: str
    pid: int
    visible: bool
    minimized: bool
    rect: WindowRect
    monitor_device: str | None
    monitor_primary: bool | None
    z_order: int


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

if user32 is not None:
    # Ensure EnumWindows signature is known to ctypes.
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL


def _require_windows() -> ctypes.WinDLL:
    if user32 is None:
        raise RuntimeError('win32_unavailable')
    _best_effort_enable_dpi_awareness()
    return user32


def get_window_text(hwnd: int) -> str:
    u32 = _require_windows()
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def is_window(hwnd: int) -> bool:
    u32 = _require_windows()
    return bool(u32.IsWindow(wintypes.HWND(hwnd)))


def is_window_visible(hwnd: int) -> bool:
    if user32 is None:
        return False
    try:
        return bool(user32.IsWindowVisible(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def is_window_minimized(hwnd: int) -> bool:
    if user32 is None:
        return False
    try:
        return bool(user32.IsIconic(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def get_foreground_window() -> int:
    u32 = _require_windows()
    return int(u32.GetForegroundWindow())


def _get_current_thread_id() -> int:
    if kernel32 is None:
        return 0
    try:
        fn = kernel32.GetCurrentThreadId
        fn.argtypes = []
        fn.restype = wintypes.DWORD
        return int(fn())
    except Exception:
        return 0


def _get_window_thread_id(hwnd: int) -> int:
    if user32 is None:
        return 0
    try:
        pid = wintypes.DWORD(0)
        # Return value is the thread id.
        tid = int(user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid)))
        return int(tid)
    except Exception:
        return 0


def _is_foreground(hwnd: int) -> bool:
    try:
        return int(get_foreground_window()) == int(hwnd)
    except Exception:
        return False


def try_focus_window(hwnd: int, *, timeout_s: float = 0.0) -> bool:
    """Best-effort attempt to bring a window to the foreground.

    Windows may deny focus changes depending on foreground lock rules.
    This helper never raises; it returns whether the window ended up foreground.
    """

    if user32 is None:
        return False

    target_hwnd = int(hwnd)
    if target_hwnd <= 0:
        return False
    try:
        if not is_window(target_hwnd):
            return False
    except Exception:
        return False

    deadline = time.monotonic() + max(0.0, float(timeout_s))

    try:
        target = wintypes.HWND(target_hwnd)
        switch_to_this_window = getattr(user32, 'SwitchToThisWindow', None)
        if switch_to_this_window is not None:
            switch_to_this_window.argtypes = [wintypes.HWND, wintypes.BOOL]
            switch_to_this_window.restype = None

        attach_thread_input = getattr(user32, 'AttachThreadInput', None)
        if attach_thread_input is not None:
            attach_thread_input.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            attach_thread_input.restype = wintypes.BOOL

        while True:
            if _is_foreground(target_hwnd):
                return True

            fg_hwnd = 0
            try:
                fg_hwnd = int(get_foreground_window())
            except Exception:
                fg_hwnd = 0

            attached = False
            cur_tid = _get_current_thread_id()
            fg_tid = _get_window_thread_id(fg_hwnd) if fg_hwnd else 0
            try:
                if attach_thread_input is not None and cur_tid and fg_tid and cur_tid != fg_tid:
                    attached = bool(attach_thread_input(wintypes.DWORD(cur_tid), wintypes.DWORD(fg_tid), wintypes.BOOL(True)))

                # Restore only if minimized (fullscreen apps can minimize when focus is forced).
                try:
                    if is_window_minimized(target_hwnd):
                        # SW_RESTORE=9
                        user32.ShowWindow(target, 9)
                except Exception:
                    pass

                # Common best-effort sequence.
                try:
                    user32.BringWindowToTop(target)
                except Exception:
                    pass
                try:
                    user32.SetActiveWindow(target)
                except Exception:
                    pass
                try:
                    user32.SetForegroundWindow(target)
                except Exception:
                    pass
                try:
                    user32.SetFocus(target)
                except Exception:
                    pass
                try:
                    if switch_to_this_window is not None:
                        switch_to_this_window(target, wintypes.BOOL(True))
                except Exception:
                    pass
            finally:
                try:
                    if attached and attach_thread_input is not None and cur_tid and fg_tid:
                        attach_thread_input(wintypes.DWORD(cur_tid), wintypes.DWORD(fg_tid), wintypes.BOOL(False))
                except Exception:
                    pass

            if _is_foreground(target_hwnd):
                return True
            if time.monotonic() >= deadline:
                return False
            # PROD constraint: avoid time.sleep in preflight/runner paths.
            try:
                from runtime.pacing import wait_until_ns

                wait_until_ns(int(time.monotonic_ns() + 50_000_000))
            except Exception:
                # Worst case: tight spin until deadline.
                pass
    except Exception:
        return False


def get_client_rect_in_screen(hwnd: int) -> WindowRect:
    u32 = _require_windows()
    # client rect (0,0,w,h)
    rect = wintypes.RECT()
    if not u32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise RuntimeError('GetClientRect failed')

    # Convert client origin to screen coords
    pt = wintypes.POINT(0, 0)
    if not u32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        raise RuntimeError('ClientToScreen failed')

    left = int(pt.x)
    top = int(pt.y)
    right = left + int(rect.right - rect.left)
    bottom = top + int(rect.bottom - rect.top)
    return WindowRect(left=left, top=top, right=right, bottom=bottom)


def find_window_by_title_substring(substr: str) -> Optional[HwndMatch]:
    u32 = _require_windows()
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

    def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        try:
            # ctypes callback must return a Python bool/int, not a ctype instance.
            return bool(on_enum(int(hwnd)))
        except Exception:
            return True

    u32.EnumWindows(EnumWindowsProc(_cb), 0)
    return best


def find_window_by_title_exact(title: str) -> Optional[HwndMatch]:
    """Find a top-level window by exact title.

    Deterministic:
    - Returns the first match in EnumWindows (z-order) order.
    - Tries case-sensitive exact match first; if none, falls back to case-insensitive exact.
    """

    u32 = _require_windows()
    needle = (title or '').strip()
    if not needle:
        return None

    best: Optional[HwndMatch] = None

    def on_enum(hwnd: int) -> bool:
        nonlocal best
        t = get_window_text(hwnd)
        if not t:
            return True
        if t == needle:
            best = HwndMatch(hwnd=int(hwnd), title=t)
            return False
        return True

    def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        try:
            return bool(on_enum(int(hwnd)))
        except Exception:
            return True

    u32.EnumWindows(EnumWindowsProc(_cb), 0)
    if best is not None:
        return best

    needle_cf = needle.casefold()

    def on_enum_ci(hwnd: int) -> bool:
        nonlocal best
        t = get_window_text(hwnd)
        if not t:
            return True
        if t.strip().casefold() == needle_cf:
            best = HwndMatch(hwnd=int(hwnd), title=t)
            return False
        return True

    def _cb_ci(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        try:
            return bool(on_enum_ci(int(hwnd)))
        except Exception:
            return True

    u32.EnumWindows(EnumWindowsProc(_cb_ci), 0)
    return best


def list_monitors() -> list[MonitorInfo]:
    """Enumerate display monitors and their virtual-screen rectangles."""

    u32 = _require_windows()

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('rcMonitor', wintypes.RECT),
            ('rcWork', wintypes.RECT),
            ('dwFlags', wintypes.DWORD),
            ('szDevice', wintypes.WCHAR * 32),
        ]

    MONITORINFOF_PRIMARY = 0x00000001

    out: list[MonitorInfo] = []

    MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    # Ensure signature for EnumDisplayMonitors/GetMonitorInfoW.
    u32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), MonitorEnumProc, wintypes.LPARAM]
    u32.EnumDisplayMonitors.restype = wintypes.BOOL
    u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    u32.GetMonitorInfoW.restype = wintypes.BOOL

    def _cb(hmon: wintypes.HMONITOR, hdc: wintypes.HDC, lprc: object, lparam: wintypes.LPARAM) -> bool:
        try:
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if not u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return True
            rect = WindowRect(left=int(mi.rcMonitor.left), top=int(mi.rcMonitor.top), right=int(mi.rcMonitor.right), bottom=int(mi.rcMonitor.bottom))
            dev = str(mi.szDevice)
            primary = bool(int(mi.dwFlags) & int(MONITORINFOF_PRIMARY))
            out.append(MonitorInfo(device=dev, rect=rect, primary=primary))
            return True
        except Exception:
            return True

    u32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    return out


def monitor_for_point(x: int, y: int, monitors: list[MonitorInfo] | None = None) -> MonitorInfo | None:
    mons = monitors if monitors is not None else list_monitors()
    for m in mons:
        r = m.rect
        if int(x) >= int(r.left) and int(x) < int(r.right) and int(y) >= int(r.top) and int(y) < int(r.bottom):
            return m
    return None


def primary_monitor(monitors: list[MonitorInfo] | None = None) -> MonitorInfo | None:
    mons = monitors if monitors is not None else list_monitors()
    for m in mons:
        if bool(m.primary):
            return m
    return mons[0] if mons else None


def list_top_level_windows(*, title_substring: str = '', visible_only: bool = True) -> list[WindowInfo]:
    """Enumerate top-level windows.

    Intended for diagnostics/tools, not hot paths.
    """

    u32 = _require_windows()
    needle = (title_substring or '').strip().lower()

    out: list[WindowInfo] = []

    def on_enum(hwnd: int) -> bool:
        title = get_window_text(hwnd)
        if not title:
            return True
        if needle and needle not in title.lower():
            return True

        visible = is_window_visible(hwnd)
        minimized = is_window_minimized(hwnd)
        if visible_only and not visible:
            return True

        try:
            pid = get_window_process_id(hwnd)
        except Exception:
            pid = 0

        out.append(WindowInfo(hwnd=int(hwnd), title=title, pid=int(pid), visible=bool(visible), minimized=bool(minimized)))
        return True

    def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        try:
            return bool(on_enum(int(hwnd)))
        except Exception:
            return True

    u32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def list_visible_windows_diagnostic() -> tuple[list[WindowDiagnosticInfo], list[MonitorInfo]]:
    """Enumerate visible top-level windows with monitor + z-order.

    Captures:
    - EnumWindows order as z-order (0 = topmost)
    - GetWindowRect (screen coords)
    - Visible/minimized flags
    - Monitor device + primary (inferred by rect center)
    """

    u32 = _require_windows()
    monitors = list_monitors()

    out: list[WindowDiagnosticInfo] = []

    z = 0

    def on_enum(hwnd: int) -> bool:
        nonlocal z

        title = get_window_text(hwnd)
        if not title:
            return True

        visible = is_window_visible(hwnd)
        minimized = is_window_minimized(hwnd)
        if not visible:
            return True

        try:
            pid = get_window_process_id(hwnd)
        except Exception:
            pid = 0

        try:
            rect = get_window_rect_in_screen(hwnd)
        except Exception:
            rect = WindowRect(left=0, top=0, right=0, bottom=0)

        cx = int(rect.left) + int(rect.width // 2)
        cy = int(rect.top) + int(rect.height // 2)
        mon = monitor_for_point(cx, cy, monitors)
        mon_dev = str(mon.device) if mon is not None else None
        mon_primary = bool(mon.primary) if mon is not None else None

        out.append(
            WindowDiagnosticInfo(
                hwnd=int(hwnd),
                title=str(title),
                pid=int(pid),
                visible=bool(visible),
                minimized=bool(minimized),
                rect=rect,
                monitor_device=mon_dev,
                monitor_primary=mon_primary,
                z_order=int(z),
            )
        )
        z += 1
        return True

    def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        try:
            return bool(on_enum(int(hwnd)))
        except Exception:
            return True

    u32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out, monitors


def monitor_info_to_dict(mi: MonitorInfo) -> dict[str, object]:
    r = mi.rect
    return {
        'device': str(mi.device),
        'primary': bool(mi.primary),
        'rect': {'left': int(r.left), 'top': int(r.top), 'right': int(r.right), 'bottom': int(r.bottom)},
    }


def window_diag_to_dict(w: WindowDiagnosticInfo) -> dict[str, object]:
    r = w.rect
    return {
        'hwnd': hex(int(w.hwnd)),
        'title': str(w.title),
        'pid': int(w.pid),
        'is_visible': bool(w.visible),
        'is_minimized': bool(w.minimized),
        'rect': {'left': int(r.left), 'top': int(r.top), 'right': int(r.right), 'bottom': int(r.bottom)},
        'monitor': {'device': w.monitor_device, 'primary': w.monitor_primary},
        'z_order': int(w.z_order),
    }
