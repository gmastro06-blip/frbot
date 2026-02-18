from __future__ import annotations

import ctypes
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, cast

from adapters.windows.win32 import get_dpi_awareness_status, is_window, is_window_minimized, is_window_visible
from contracts.input import InputAdapter
from contracts.verification import VerificationResult

_ctypes_wintypes: Any = None
try:
    from ctypes import wintypes as _ctypes_wintypes
except Exception:  # pragma: no cover
    _ctypes_wintypes = None


_IS_WINDOWS = sys.platform == 'win32'

if _IS_WINDOWS and _ctypes_wintypes is not None:
    wintypes = _ctypes_wintypes
else:  # pragma: no cover
    # CI runs on Linux in mock mode and imports this module for preflights.
    # Provide a minimal wintypes shim so ctypes.Structure definitions below
    # don't crash during import. Any actual Win32 calls must still be guarded
    # by `user32 is not None` / `_IS_WINDOWS`.
    _ptr_sized_int = ctypes.c_int64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_int32

    class _RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

    wintypes = cast(Any, SimpleNamespace(
        BOOL=ctypes.c_int,
        BYTE=ctypes.c_ubyte,
        DWORD=ctypes.c_uint32,
        HANDLE=ctypes.c_void_p,
        HDC=ctypes.c_void_p,
        HMONITOR=ctypes.c_void_p,
        HWND=ctypes.c_void_p,
        LONG=ctypes.c_long,
        LPARAM=_ptr_sized_int,
        RECT=_RECT,
        UINT=ctypes.c_uint,
        ULONG_PTR=ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32,
        WCHAR=ctypes.c_wchar,
        WORD=ctypes.c_uint16,
    ))

# NOTE: This module is imported by mock-mode preflights in CI on Linux.
# Keep it import-safe cross-platform by never loading Win32 DLLs at import time
# unless running on Windows.
try:
    # NOTE: `Any` is intentional: on non-Windows we keep these as None, but mypy
    # should not treat every call site as Optional.
    user32 = cast(Any, ctypes.WinDLL('user32', use_last_error=True) if _IS_WINDOWS else None)
    kernel32 = cast(Any, ctypes.WinDLL('kernel32', use_last_error=True) if _IS_WINDOWS else None)
except Exception:
    user32 = cast(Any, None)
    kernel32 = cast(Any, None)

# Ensure the process is DPI-aware as early as possible.
# Without this, Win32 APIs like ClientToScreen may return scaled (virtual)
# coordinates while SendInput expects physical pixels, causing clicks to land
# outside the intended window.
try:
    get_dpi_awareness_status()
except Exception:
    # Best-effort only: some environments/Windows builds may not support or
    # permit the DPI-awareness query. Keep import-time non-fatal.
    pass


if hasattr(wintypes, 'ULONG_PTR'):
    ULONG_PTR = wintypes.ULONG_PTR
else:
    # Older Python/wintypes builds may not define ULONG_PTR.
    ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

# SendInput constants/structs (for games that ignore WM_KEY* messages).
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

# System key messages (Alt held).
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_RBUTTON = 0x0002
MK_SHIFT = 0x0004

VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27

MAPVK_VK_TO_VSC = 0

VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_TAB = 0x09

VK_PRIOR = 0x21  # PageUp
VK_NEXT = 0x22  # PageDown
VK_END = 0x23
VK_HOME = 0x24
VK_INSERT = 0x2D
VK_DELETE = 0x2E

VK_F1 = 0x70

VK_NUMPAD0 = 0x60

VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1

VK_MENU = 0x12  # Alt


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name, '') or '').strip().lower()
    return v in {'1', 'true', 'yes', 'on'}


def _try_set_foreground(hwnd: int, *, retry_ms: int = 250) -> bool:
    """Best-effort attempt to bring a HWND to the foreground.

    This is intentionally conservative: callers must gate this behind an explicit
    env flag (prod_emergency default forbids focus stealing).
    """

    if user32 is None:
        return False

    try:
        if int(hwnd) <= 0:
            return False

        # Prototypes (best-effort; keep failures non-fatal).
        try:
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindowAsync.restype = wintypes.BOOL
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.IsIconic.restype = wintypes.BOOL
            user32.BringWindowToTop.argtypes = [wintypes.HWND]
            user32.BringWindowToTop.restype = wintypes.BOOL
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            user32.AttachThreadInput.restype = wintypes.BOOL
            if kernel32 is not None:
                kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        except Exception:
            # Best-effort signature setup: tolerate missing exports or blocked
            # calls (older Windows builds, restricted environments).
            pass

        h = wintypes.HWND(int(hwnd))

        try:
            fg = int(user32.GetForegroundWindow() or 0)
        except Exception:
            fg = 0

        fg_tid = 0
        tgt_tid = 0
        try:
            pid_tmp = wintypes.DWORD(0)
            if fg:
                fg_tid = int(user32.GetWindowThreadProcessId(wintypes.HWND(int(fg)), ctypes.byref(pid_tmp)) or 0)
            pid_tmp2 = wintypes.DWORD(0)
            tgt_tid = int(user32.GetWindowThreadProcessId(h, ctypes.byref(pid_tmp2)) or 0)
        except Exception:
            fg_tid = 0
            tgt_tid = 0

        attached = False
        try:
            if fg_tid and tgt_tid and fg_tid != tgt_tid:
                try:
                    attached = bool(user32.AttachThreadInput(wintypes.DWORD(int(fg_tid)), wintypes.DWORD(int(tgt_tid)), wintypes.BOOL(True)))
                except Exception:
                    attached = False

            # Only restore if minimized; avoid toggling maximized->normal.
            try:
                if bool(user32.IsIconic(h)):
                    # SW_RESTORE = 9
                    user32.ShowWindowAsync(h, 9)
            except Exception:
                # Best-effort: ignore restore failures.
                pass
            try:
                user32.BringWindowToTop(h)
            except Exception:
                # Best-effort: ignore z-order failures.
                pass
            try:
                user32.SetForegroundWindow(h)
            except Exception:
                # Best-effort: ignore foreground failures.
                pass
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(wintypes.DWORD(int(fg_tid)), wintypes.DWORD(int(tgt_tid)), wintypes.BOOL(False))
                except Exception:
                    # Best-effort cleanup: detaching input threads may fail in
                    # restricted environments; ignore.
                    pass

        # Verify.
        try:
            fg2 = int(user32.GetForegroundWindow() or 0)
        except Exception:
            fg2 = 0
        return int(fg2) == int(hwnd)
    except Exception:
        return False


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('mi', _MOUSEINPUT),
        ('ki', _KEYBDINPUT),
        ('hi', _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ('union',)
    _fields_ = [
        ('type', wintypes.DWORD),
        ('union', _INPUT_UNION),
    ]


def _ensure_sendinput_signature() -> None:
    try:
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
    except Exception:
        return


def _sendinput_key(vk: int, *, extended: bool, use_scancode: bool = True) -> None:
    _ensure_sendinput_signature()

    sc = 0
    if bool(use_scancode):
        # Prefer scan codes for games that ignore WM_KEY* and VK-only injection.
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0

    flags_down = int(KEYEVENTF_SCANCODE) if bool(use_scancode) else 0
    flags_up = int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP) if bool(use_scancode) else int(KEYEVENTF_KEYUP)
    if bool(extended):
        flags_down |= int(KEYEVENTF_EXTENDEDKEY)
        flags_up |= int(KEYEVENTF_EXTENDEDKEY)

    wvk_down = 0 if bool(use_scancode) else int(vk)
    wvk_up = 0 if bool(use_scancode) else int(vk)

    inp_down = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=wvk_down, wScan=sc, dwFlags=flags_down, time=0, dwExtraInfo=0)))
    inp_up = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=wvk_up, wScan=sc, dwFlags=flags_up, time=0, dwExtraInfo=0)))
    arr = (_INPUT * 2)(inp_down, inp_up)
    sent = int(user32.SendInput(2, arr, ctypes.sizeof(_INPUT)))
    if sent != 2:
        err = ctypes.get_last_error()
        raise RuntimeError(f'input_failed: sent={sent} winerr={err}')


def _sendinput_key_event(vk: int, *, keyup: bool, extended: bool, use_scancode: bool = True) -> None:
    _ensure_sendinput_signature()

    sc = 0
    if bool(use_scancode):
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0

    flags = int(KEYEVENTF_SCANCODE) if bool(use_scancode) else 0
    if bool(keyup):
        flags |= int(KEYEVENTF_KEYUP)
    if bool(extended):
        flags |= int(KEYEVENTF_EXTENDEDKEY)

    wvk = 0 if bool(use_scancode) else int(vk)
    inp = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=wvk, wScan=sc, dwFlags=int(flags), time=0, dwExtraInfo=0)))
    arr = (_INPUT * 1)(inp)
    sent = int(user32.SendInput(1, arr, ctypes.sizeof(_INPUT)))
    if sent != 1:
        err = ctypes.get_last_error()
        raise RuntimeError(f'input_failed: sent={sent} winerr={err}')


def _client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(pt)):
        err = ctypes.get_last_error()
        raise RuntimeError(f'client_to_screen_failed: winerr={err}')
    return int(pt.x), int(pt.y)


def _get_cursor_pos_screen() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        err = ctypes.get_last_error()
        raise RuntimeError(f'get_cursor_pos_failed: winerr={err}')
    return int(pt.x), int(pt.y)


def _screen_to_client(hwnd: int, x_screen: int, y_screen: int) -> tuple[int, int]:
    pt = wintypes.POINT(int(x_screen), int(y_screen))
    if not user32.ScreenToClient(wintypes.HWND(int(hwnd)), ctypes.byref(pt)):
        err = ctypes.get_last_error()
        raise RuntimeError(f'screen_to_client_failed: winerr={err}')
    return int(pt.x), int(pt.y)


def _get_client_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        err = ctypes.get_last_error()
        raise RuntimeError(f'get_client_rect_failed: winerr={err}')
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    return max(1, w), max(1, h)


def _sendinput_mouse_click(*, x_screen: int, y_screen: int, right: bool) -> None:
    _ensure_sendinput_signature()

    # Convert to absolute coordinates in [0, 65535].
    try:
        if user32 is None:
            raise RuntimeError('input_failed: user32_unavailable')
        sm_cxscreen = int(user32.GetSystemMetrics(0))
        sm_cyscreen = int(user32.GetSystemMetrics(1))
    except Exception:
        sm_cxscreen = 0
        sm_cyscreen = 0
    if sm_cxscreen <= 1 or sm_cyscreen <= 1:
        raise RuntimeError('input_failed: screen_metrics_unavailable')

    x = int(max(0, min(sm_cxscreen - 1, int(x_screen))))
    y = int(max(0, min(sm_cyscreen - 1, int(y_screen))))
    dx = int(x * 65535 / (sm_cxscreen - 1))
    dy = int(y * 65535 / (sm_cyscreen - 1))

    move = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    if bool(right):
        down_flag = MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_RIGHTUP
    else:
        down_flag = MOUSEEVENTF_LEFTDOWN
        up_flag = MOUSEEVENTF_LEFTUP
    down = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(down_flag | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    up = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(up_flag | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    arr = (_INPUT * 3)(move, down, up)
    sent = int(user32.SendInput(3, arr, ctypes.sizeof(_INPUT)))
    if sent != 3:
        err = ctypes.get_last_error()
        raise RuntimeError(f'input_failed: sent={sent} winerr={err}')


def _sendinput_mouse_click_inputs(*, x_screen: int, y_screen: int, right: bool) -> tuple[_INPUT, _INPUT, _INPUT]:
    # Same behavior as _sendinput_mouse_click, but returns the INPUT structs for batching.
    _ensure_sendinput_signature()

    try:
        if user32 is None:
            raise RuntimeError('input_failed: user32_unavailable')
        sm_cxscreen = int(user32.GetSystemMetrics(0))
        sm_cyscreen = int(user32.GetSystemMetrics(1))
    except Exception:
        sm_cxscreen = 0
        sm_cyscreen = 0
    if sm_cxscreen <= 1 or sm_cyscreen <= 1:
        raise RuntimeError('input_failed: screen_metrics_unavailable')

    x = int(max(0, min(sm_cxscreen - 1, int(x_screen))))
    y = int(max(0, min(sm_cyscreen - 1, int(y_screen))))
    dx = int(x * 65535 / (sm_cxscreen - 1))
    dy = int(y * 65535 / (sm_cyscreen - 1))

    move = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    if bool(right):
        down_flag = MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_RIGHTUP
    else:
        down_flag = MOUSEEVENTF_LEFTDOWN
        up_flag = MOUSEEVENTF_LEFTUP
    down = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(down_flag | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    up = _INPUT(type=INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=int(up_flag | MOUSEEVENTF_ABSOLUTE), time=0, dwExtraInfo=0)))
    return move, down, up


def _sendinput_batch(inputs: list[_INPUT]) -> None:
    _ensure_sendinput_signature()
    if not inputs:
        return
    arr_t = _INPUT * int(len(inputs))
    arr = arr_t(*inputs)
    sent = int(user32.SendInput(int(len(inputs)), arr, ctypes.sizeof(_INPUT)))
    if sent != int(len(inputs)):
        err = ctypes.get_last_error()
        raise RuntimeError(f'input_failed: sent={sent} expected={len(inputs)} winerr={err}')


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

    if k in {'shift', 'lshift'}:
        return VK_LSHIFT
    if k in {'rshift'}:
        return VK_RSHIFT
    if k in {'alt', 'menu', 'lalt'}:
        return VK_MENU
    if k == 'tab':
        return VK_TAB

    # Navigation keys.
    if k in {'pgup', 'pageup', 'repag'}:
        return VK_PRIOR
    if k in {'pgdn', 'pagedown', 'avpag'}:
        return VK_NEXT
    if k == 'home':
        return VK_HOME
    if k == 'end':
        return VK_END
    if k in {'ins', 'insert'}:
        return VK_INSERT
    if k in {'del', 'delete', 'supr'}:
        return VK_DELETE

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
        if user32 is None or kernel32 is None:
            raise RuntimeError('win32_unavailable')
        self._hwnd = int(hwnd)
        self._last_focus_attempt_ms: int = 0
        self._last_combo_press_ms: int = 0

    def verify(self) -> VerificationResult:
        try:
            if self._hwnd <= 0 or not is_window(self._hwnd):
                return VerificationResult(ok=False, reason='window_binding_lost')
        except Exception:
            return VerificationResult(ok=False, reason='win32_unavailable')
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

    def _ensure_focus_if_needed(self) -> bool:
        """Ensure window is foreground before input.

        Returns True if window is now foreground, False otherwise.
        Uses throttling to avoid spamming SetForegroundWindow.
        """
        hwnd_i = int(self._hwnd)
        fg = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
        if fg == hwnd_i:
            return True

        # Throttling: only try focus if enough time passed since last attempt
        now = int(time.time() * 1000)  # ms
        last = getattr(self, '_last_focus_attempt_ms', 0)
        throttle_ms = int(os.environ.get('FRBOT_FOCUS_THROTTLE_MS', '500'))
        if (now - last) < throttle_ms:
            return False

        self._last_focus_attempt_ms = now

        # Try to bring window to foreground
        if user32 is not None:
            _result = user32.SetForegroundWindow(wintypes.HWND(hwnd_i))    
            fg2 = int(user32.GetForegroundWindow() or 0)
            success = (fg2 == hwnd_i)
            import logging
            logger = logging.getLogger(__name__)
            logger.info(
                "[INPUT_FOCUS] hwnd=%d, method=SetForegroundWindow, attempted=%s, success=%s, fg_before=%d, fg_after=%d, throttle_ms=%d",
                hwnd_i, True, success, fg, fg2, throttle_ms
            )
            return success
        return False

    def get_window_readiness(self) -> dict[str, Any]:
        """Get window readiness status for input.

        Returns dict with:
        - hwnd: window handle
        - is_minimized: True if minimized
        - is_visible: True if visible
        - is_foreground: True if foreground window
        - ready_for_input: True if ready for SendInput/PostMessage
        """
        import logging
        logger = logging.getLogger(__name__)

        hwnd_i = int(self._hwnd)
        readiness = {
            'hwnd': hwnd_i,
            'is_minimized': False,
            'is_visible': False,
            'is_foreground': False,
            'ready_for_input': False,
        }

        if user32 is None:
            logger.warning("[INPUT_READINESS] user32 not available")
            return readiness

        # Check minimized (IsIconic)
        try:
            readiness['is_minimized'] = bool(user32.IsIconic(wintypes.HWND(hwnd_i)))
        except Exception:
            pass

        # Check visible (IsWindowVisible)
        try:
            readiness['is_visible'] = bool(user32.IsWindowVisible(wintypes.HWND(hwnd_i)))
        except Exception:
            pass

        # Check foreground
        try:
            fg = int(user32.GetForegroundWindow() or 0)
            readiness['is_foreground'] = (fg == hwnd_i)
        except Exception:
            pass

        # Ready for input if visible and not minimized (PostMessage can work)
        # For SendInput, must be foreground
        readiness['ready_for_input'] = readiness['is_visible'] and not readiness['is_minimized']

        logger.info(
            "[INPUT_READINESS] hwnd=%d, minimized=%s, visible=%s, foreground=%s, ready=%s",
            hwnd_i, readiness['is_minimized'], readiness['is_visible'],
            readiness['is_foreground'], readiness['ready_for_input']
        )

        return readiness

    def ensure_window_ready_for_input(self) -> bool:
        """Ensure window is ready for input.

        If minimized, attempts to restore. Then checks foreground status.
        Returns True if ready for input, False otherwise.
        """
        import logging
        logger = logging.getLogger(__name__)

        readiness = self.get_window_readiness()

        # Already ready?
        if readiness['ready_for_input']:
            return True

        # If minimized, try to restore
        if readiness['is_minimized']:
            logger.info("[INPUT_READINESS] Window minimized, attempting SW_RESTORE")
            if user32 is not None:
                try:
                    # SW_RESTORE = 9
                    user32.ShowWindow(wintypes.HWND(self._hwnd), 9)
                    # Re-check status
                    readiness = self.get_window_readiness()
                except Exception as e:
                    logger.warning("[INPUT_READINESS] ShowWindow failed: %s", e)

        # Try to bring to foreground
        if not readiness['is_foreground']:
            success = self._ensure_focus_if_needed()
            if not success:
                logger.warning(
                    "[INPUT_READINESS] Window not ready for input: hwnd=%d, minimized=%s, visible=%s, foreground=%s",
                    readiness['hwnd'], readiness['is_minimized'], readiness['is_visible'], readiness['is_foreground']
                )
                return False

        return True

    def press_key(self, key: str) -> None:
        # Certification invariant: never send keys to an invalid/invisible HWND.
        self.assert_bound()

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()

        # Log selected method
        import logging
        logger = logging.getLogger(__name__)
        logger.info("[INPUT_METHOD] selected=%s, requires_foreground=%s", method, method in {'sendinput', 'sendinput_vk'})

        if method in {'sendinput', 'sendinput_vk'}:
            # Safety: SendInput is global; only allow when the intended window is foreground.
            hwnd_i = int(self._hwnd)
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            vk = _vk_for_key(key)
            extended = bool(int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE})
            _sendinput_key(int(vk), extended=extended, use_scancode=(method == 'sendinput'))
            return

        vk = _vk_for_key(key)
        hwnd = wintypes.HWND(self._hwnd)

        # Build lParam with scan code and proper transition flags.
        # Many game clients ignore WM_KEY* messages with lParam=0.
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0
        extended_bit = 1 if int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE} else 0
        lparam_down = wintypes.LPARAM(1 | (sc << 16) | (extended_bit << 24))
        # Previous key state (bit 30) + transition state (bit 31).
        lparam_up = wintypes.LPARAM(1 | (sc << 16) | (extended_bit << 24) | (1 << 30) | (1 << 31))

        # PostMessage returns nonzero on success.
        if not user32.PostMessageW(hwnd, WM_KEYDOWN, wintypes.WPARAM(vk), lparam_down):
            raise RuntimeError('window_binding_lost')
        if not user32.PostMessageW(hwnd, WM_KEYUP, wintypes.WPARAM(vk), lparam_up):
            raise RuntimeError('window_binding_lost')

    def key_down(self, key: str) -> None:
        self.assert_bound()

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()
        if method in {'sendinput', 'sendinput_vk'}:
            hwnd_i = int(self._hwnd)
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            vk = _vk_for_key(key)
            extended = bool(int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE})
            _sendinput_key_event(int(vk), keyup=False, extended=extended, use_scancode=(method == 'sendinput'))
            return

        vk = _vk_for_key(key)
        hwnd = wintypes.HWND(self._hwnd)
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0
        extended_bit = 1 if int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE} else 0
        lparam_down = wintypes.LPARAM(1 | (sc << 16) | (extended_bit << 24))
        if not user32.PostMessageW(hwnd, WM_KEYDOWN, wintypes.WPARAM(vk), lparam_down):
            raise RuntimeError('window_binding_lost')

    def key_up(self, key: str) -> None:
        self.assert_bound()

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()
        if method in {'sendinput', 'sendinput_vk'}:
            hwnd_i = int(self._hwnd)
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            vk = _vk_for_key(key)
            extended = bool(int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE})
            _sendinput_key_event(int(vk), keyup=True, extended=extended, use_scancode=(method == 'sendinput'))
            return

        vk = _vk_for_key(key)
        hwnd = wintypes.HWND(self._hwnd)
        try:
            sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
        except Exception:
            sc = 0
        extended_bit = 1 if int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE} else 0
        lparam_up = wintypes.LPARAM(1 | (sc << 16) | (extended_bit << 24) | (1 << 30) | (1 << 31))
        if not user32.PostMessageW(hwnd, WM_KEYUP, wintypes.WPARAM(vk), lparam_up):
            raise RuntimeError('window_binding_lost')

    def press_combo(self, keys: list[str]) -> None:
        """Press a key combo as a single logical action.

        Intended for certification-safe quick-loot (Alt+Q).

        Default behavior uses SendInput for reliability with games, but that
        requires the intended window to be foreground.

        If FRBOT_COMBO_METHOD=postmessage, uses HWND-targeted WM_SYSKEY* / WM_KEY*
        messages to avoid foreground requirements.
        """

        self.assert_bound()

        if not keys:
            raise RuntimeError('invalid_combo')

        norm = [str(k or '').strip().lower() for k in keys]
        norm = [k for k in norm if k]
        if len(norm) < 2:
            raise RuntimeError('invalid_combo')

        # Supported modifiers.
        mod_map = {
            'alt': 'alt',
            'menu': 'alt',
            'shift': 'shift',
            'control': 'ctrl',
            'ctrl': 'ctrl',
        }

        mods: list[str] = []
        main: str | None = None
        for k in norm:
            if k in mod_map:
                mk = str(mod_map[k])
                if mk not in mods:
                    mods.append(mk)
                continue
            if main is None:
                main = str(k)
        if main is None:
            raise RuntimeError('invalid_combo')

        combo_method = (os.environ.get('FRBOT_COMBO_METHOD', '') or '').strip().lower() or 'sendinput'
        allow_background = _env_flag('FRBOT_ALLOW_BACKGROUND_INPUT')

        def _press_alt_main_via_postmessage() -> None:
            hwnd_i = int(self._hwnd)
            hwnd = wintypes.HWND(hwnd_i)

            def _lparam(vk: int, *, keyup: bool, is_sys: bool) -> int:
                try:
                    sc = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
                except Exception:
                    sc = 0
                extended_bit = 1 if int(vk) in {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE} else 0
                lp = 1 | (sc << 16) | (extended_bit << 24)
                # For KEYUP: previous state + transition state.
                if keyup:
                    lp |= (1 << 30) | (1 << 31)
                # For WM_SYSKEY* with Alt pressed, Windows also sets bit 29.
                if is_sys:
                    lp |= (1 << 29)
                return int(lp)

            def _post(msg: int, vk: int, *, keyup: bool, is_sys: bool) -> None:
                lp = _lparam(int(vk), keyup=bool(keyup), is_sys=bool(is_sys))
                ok = bool(user32.PostMessageW(hwnd, int(msg), wintypes.WPARAM(int(vk)), lp))
                if not ok:
                    raise RuntimeError('window_binding_lost')

            # Currently only supports Alt + <main> reliably.
            if 'alt' not in mods:
                raise RuntimeError('invalid_combo')

            vk_alt = int(_vk_for_key('alt'))
            vk_main = int(_vk_for_key(main))

            # Alt down (system key).
            _post(WM_SYSKEYDOWN, vk_alt, keyup=False, is_sys=True)
            # Main key down/up as system keys.
            _post(WM_SYSKEYDOWN, vk_main, keyup=False, is_sys=True)
            _post(WM_SYSKEYUP, vk_main, keyup=True, is_sys=True)
            # Alt up.
            _post(WM_SYSKEYUP, vk_alt, keyup=True, is_sys=True)
            return

        # HWND-targeted PostMessage path (no foreground requirement).
        if combo_method in {'postmessage', 'hwnd', 'postmessage_syskey'}:
            _press_alt_main_via_postmessage()
            return

        # Hybrid: try SendInput (with best-effort foregrounding if explicitly allowed),
        # then fall back to PostMessage syskey.
        if combo_method in {'hybrid', 'sendinput_then_postmessage', 'sendinput_fallback_postmessage'}:
            hwnd_i = int(self._hwnd)
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i and allow_background:
                _try_set_foreground(int(hwnd_i), retry_ms=300)
                fg = int(user32.GetForegroundWindow() or 0)
            if fg == hwnd_i:
                combo_method = 'sendinput'
            else:
                _press_alt_main_via_postmessage()
                return

        # Safety: SendInput is global; only allow when the intended window is foreground.
        hwnd_i = int(self._hwnd)
        fg = int(user32.GetForegroundWindow() or 0)
        if fg != hwnd_i:
            if allow_background:
                _try_set_foreground(int(hwnd_i), retry_ms=300)
            else:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
            fg2 = int(user32.GetForegroundWindow() or 0)
            if fg2 != hwnd_i:
                raise RuntimeError('window_not_foreground')

        def _sc(vk: int) -> int:
            try:
                return int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC)) & 0xFF
            except Exception:
                return 0

        seq: list[_INPUT] = []
        try:
            # Modifiers down.
            for m in mods:
                vk = int(_vk_for_key(m))
                sc = _sc(vk)
                seq.append(
                    _INPUT(
                        type=INPUT_KEYBOARD,
                        union=_INPUT_UNION(
                            ki=_KEYBDINPUT(wVk=0, wScan=int(sc), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)
                        ),
                    )
                )

            # Main key down/up.
            vk_main = int(_vk_for_key(main))
            sc_main = _sc(vk_main)
            seq.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUT_UNION(
                        ki=_KEYBDINPUT(wVk=0, wScan=int(sc_main), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)
                    ),
                )
            )
            seq.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUT_UNION(
                        ki=_KEYBDINPUT(
                            wVk=0,
                            wScan=int(sc_main),
                            dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
                            time=0,
                            dwExtraInfo=0,
                        )
                    ),
                )
            )

            # Modifiers up (reverse order).
            for m in reversed(mods):
                vk = int(_vk_for_key(m))
                sc = _sc(vk)
                seq.append(
                    _INPUT(
                        type=INPUT_KEYBOARD,
                        union=_INPUT_UNION(
                            ki=_KEYBDINPUT(
                                wVk=0,
                                wScan=int(sc),
                                dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
                                time=0,
                                dwExtraInfo=0,
                            )
                        ),
                    )
                )

            _sendinput_batch(seq)
        except Exception:
            # Best-effort fallback: ensure modifiers are released.
            try:
                for m in reversed(mods):
                    _sendinput_key_event(int(_vk_for_key(m)), keyup=True, extended=False, use_scancode=True)
            finally:
                raise

    def click(self, x: int, y: int) -> None:
        return self._click_impl(int(x), int(y), right=False)

    def click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
        """Click using coordinates in capture-frame space.

        Useful when capture comes from OBS source identity and frame coords don't
        match the actual HWND client size (scaling/cropping).
        """
        if int(frame_w) <= 0 or int(frame_h) <= 0:
            return self.click(int(x), int(y))
        cw, ch = _get_client_size(int(self._hwnd))
        cx = int(round((float(x) / float(frame_w)) * float(cw)))
        cy = int(round((float(y) / float(frame_h)) * float(ch)))
        return self.click(int(cx), int(cy))

    def right_click(self, x: int, y: int) -> None:
        return self._click_impl(int(x), int(y), right=True)

    def right_click_cursor(self) -> None:
        """Right-click at current cursor position (single logical action).

        Useful when the operator pre-positions the mouse over a corpse and we
        want to avoid any coordinate mapping/mouse movement.
        """

        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()
        hwnd_i = int(self._hwnd)

        if method in {'sendinput', 'sendinput_vk'}:
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            xs, ys = _get_cursor_pos_screen()
            _sendinput_mouse_click(x_screen=int(xs), y_screen=int(ys), right=True)
            return

        # PostMessage fallback: translate screen->client and use HWND-targeted click.
        xs, ys = _get_cursor_pos_screen()
        cx, cy = _screen_to_client(hwnd_i, int(xs), int(ys))
        return self._click_impl(int(cx), int(cy), right=True)

    def click_cursor(self) -> None:
        """Left-click at current cursor position (single logical action).

        Useful for operator-assisted flows where the human pre-positions the
        mouse over an on-screen control and the bot must still emit exactly
        one input.
        """

        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()
        hwnd_i = int(self._hwnd)

        if method in {'sendinput', 'sendinput_vk'}:
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            xs, ys = _get_cursor_pos_screen()
            _sendinput_mouse_click(x_screen=int(xs), y_screen=int(ys), right=False)
            return

        # PostMessage fallback: translate screen->client and use HWND-targeted click.
        xs, ys = _get_cursor_pos_screen()
        cx, cy = _screen_to_client(hwnd_i, int(xs), int(ys))
        return self._click_impl(int(cx), int(cy), right=False)

    def right_click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
        if int(frame_w) <= 0 or int(frame_h) <= 0:
            return self.right_click(int(x), int(y))
        cw, ch = _get_client_size(int(self._hwnd))
        cx = int(round((float(x) / float(frame_w)) * float(cw)))
        cy = int(round((float(y) / float(frame_h)) * float(ch)))
        return self.right_click(int(cx), int(cy))

    def _click_impl(self, x: int, y: int, *, right: bool) -> None:
        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower()
        if method in {'sendinput', 'sendinput_vk'}:
            # Safety: SendInput is global; only allow when the intended window is foreground.
            hwnd_i = int(self._hwnd)
            fg = int(user32.GetForegroundWindow() or 0)
            if fg != hwnd_i:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
                fg2 = int(user32.GetForegroundWindow() or 0)
                if fg2 != hwnd_i:
                    raise RuntimeError('window_not_foreground')

            xs, ys = _client_to_screen(hwnd_i, int(x), int(y))
            _sendinput_mouse_click(x_screen=int(xs), y_screen=int(ys), right=bool(right))
            return

        # PostMessage path (HWND-targeted): coordinates are *client* pixels.
        cx = int(max(0, min(65535, int(x))))
        cy = int(max(0, min(65535, int(y))))
        lparam = (cy << 16) | cx

        hwnd = wintypes.HWND(self._hwnd)
        if bool(right):
            if not user32.PostMessageW(hwnd, WM_RBUTTONDOWN, wintypes.WPARAM(MK_RBUTTON), wintypes.LPARAM(lparam)):
                raise RuntimeError('window_binding_lost')
            if not user32.PostMessageW(hwnd, WM_RBUTTONUP, wintypes.WPARAM(0), wintypes.LPARAM(lparam)):
                raise RuntimeError('window_binding_lost')
            return

        if not user32.PostMessageW(hwnd, WM_LBUTTONDOWN, wintypes.WPARAM(MK_LBUTTON), wintypes.LPARAM(lparam)):
            raise RuntimeError('window_binding_lost')
        if not user32.PostMessageW(hwnd, WM_LBUTTONUP, wintypes.WPARAM(0), wintypes.LPARAM(lparam)):
            raise RuntimeError('window_binding_lost')

    def shift_right_click_frame(self, x: int, y: int, *, frame_w: int, frame_h: int) -> None:
        """Perform Shift + right-click as a single logical action.

        Uses SendInput for reliable modifier state in games; enforces foreground safety.
        Coordinates are in capture-frame space.
        """

        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        if int(frame_w) <= 0 or int(frame_h) <= 0:
            raise RuntimeError('invalid_frame_size')

        hwnd_i = int(self._hwnd)
        fg = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
        if fg != hwnd_i:
            if user32 is not None:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
            fg2 = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
            if fg2 != hwnd_i:
                raise RuntimeError('window_not_foreground')

        # Map capture-frame coords -> client coords.
        cw, ch = _get_client_size(int(self._hwnd))

        coord_space = (os.environ.get('FRBOT_FRAME_COORD_SPACE', '') or '').strip().lower()
        if coord_space == 'screen':
            # Treat (x,y) as absolute screen pixels. This is useful when the
            # capture frame is a display-capture (full monitor) rather than a
            # window/client capture.
            xs = int(x)
            ys = int(y)

            # Clamp to the window client rect to avoid clicking on chrome.
            try:
                tlx, tly = _client_to_screen(hwnd_i, 0, 0)
                brx, bry = _client_to_screen(hwnd_i, max(0, int(cw) - 1), max(0, int(ch) - 1))
                xs = int(max(int(tlx), min(int(xs), int(brx))))
                ys = int(max(int(tly), min(int(ys), int(bry))))
            except Exception:
                # Best-effort clamp: client-to-screen may fail for transient
                # windows; fall back to raw screen coords.
                pass
        else:
            cx = int(round((float(x) / float(frame_w)) * float(cw)))
            cy = int(round((float(y) / float(frame_h)) * float(ch)))

            # Client -> screen coords for SendInput.
            xs, ys = _client_to_screen(hwnd_i, int(cx), int(cy))

        # Build a single SendInput batch:
        #   Shift down -> mouse move/down/up -> Shift up
        try:
            vk_shift = int(_vk_for_key('shift'))
            sc_shift = 0
            try:
                if user32 is not None:
                    sc_shift = int(user32.MapVirtualKeyW(int(vk_shift), MAPVK_VK_TO_VSC)) & 0xFF
            except Exception:
                sc_shift = 0
            down_key = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_shift), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)))
            up_key = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_shift), dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP), time=0, dwExtraInfo=0)))

            move, down, up = _sendinput_mouse_click_inputs(x_screen=int(xs), y_screen=int(ys), right=True)
            _sendinput_batch([down_key, move, down, up, up_key])
        except Exception:
            # As a fallback, try separate calls while ensuring Shift is released.
            try:
                _sendinput_key_event(int(_vk_for_key('shift')), keyup=False, extended=False, use_scancode=True)
                _sendinput_mouse_click(x_screen=int(xs), y_screen=int(ys), right=True)
            finally:
                _sendinput_key_event(int(_vk_for_key('shift')), keyup=True, extended=False, use_scancode=True)

    def shift_right_click_cursor(self) -> None:
        """Perform Shift + right-click at current cursor position.

        This is a single logical action (modifier down -> click -> modifier up)
        but avoids any coordinate mapping.
        """

        if self._hwnd <= 0 or not is_window(self._hwnd):
            raise RuntimeError('window_binding_lost')

        hwnd_i = int(self._hwnd)
        fg = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
        if fg != hwnd_i:
            if user32 is not None:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
            fg2 = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
            if fg2 != hwnd_i:
                raise RuntimeError('window_not_foreground')
        xs, ys = _get_cursor_pos_screen()

        # Prefer SendInput to keep modifier state reliable for games.
        try:
            vk_shift = int(_vk_for_key('shift'))
            sc_shift = 0
            try:
                sc_shift = int(user32.MapVirtualKeyW(int(vk_shift), MAPVK_VK_TO_VSC)) & 0xFF
            except Exception:
                sc_shift = 0

            down_key = _INPUT(
                type=INPUT_KEYBOARD,
                union=_INPUT_UNION(
                    ki=_KEYBDINPUT(wVk=0, wScan=int(sc_shift), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)
                ),
            )
            up_key = _INPUT(
                type=INPUT_KEYBOARD,
                union=_INPUT_UNION(
                    ki=_KEYBDINPUT(
                        wVk=0,
                        wScan=int(sc_shift),
                        dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
                        time=0,
                        dwExtraInfo=0,
                    )
                ),
            )

            move, down, up = _sendinput_mouse_click_inputs(x_screen=int(xs), y_screen=int(ys), right=True)
            _sendinput_batch([down_key, move, down, up, up_key])
            return
        except Exception:
            # Fallback: ensure Shift released.
            try:
                _sendinput_key_event(int(_vk_for_key('shift')), keyup=False, extended=False, use_scancode=True)
                _sendinput_mouse_click(x_screen=int(xs), y_screen=int(ys), right=True)
            finally:
                _sendinput_key_event(int(_vk_for_key('shift')), keyup=True, extended=False, use_scancode=True)

    def alt_press_key(self, key: str) -> None:
        """Press Alt+<key> as a single logical action.

        Uses SendInput (global) and enforces foreground safety.
        """

        self.assert_bound()

        hwnd_i = int(self._hwnd)
        fg = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
        if fg != hwnd_i:
            if user32 is not None:
                user32.SetForegroundWindow(wintypes.HWND(hwnd_i))
            fg2 = int(user32.GetForegroundWindow() or 0) if user32 is not None else 0
            if fg2 != hwnd_i:
                raise RuntimeError('window_not_foreground')

        try:
            vk_alt = int(_vk_for_key('alt'))
            vk_key = int(_vk_for_key(str(key)))

            sc_alt = 0
            sc_key = 0
            try:
                if user32 is not None:
                    sc_alt = int(user32.MapVirtualKeyW(int(vk_alt), MAPVK_VK_TO_VSC)) & 0xFF
            except Exception:
                sc_alt = 0
            try:
                if user32 is not None:
                    sc_key = int(user32.MapVirtualKeyW(int(vk_key), MAPVK_VK_TO_VSC)) & 0xFF
            except Exception:
                sc_key = 0

            alt_down = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_alt), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)))
            key_down = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_key), dwFlags=int(KEYEVENTF_SCANCODE), time=0, dwExtraInfo=0)))
            key_up = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_key), dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP), time=0, dwExtraInfo=0)))
            alt_up = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=int(sc_alt), dwFlags=int(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP), time=0, dwExtraInfo=0)))

            _sendinput_batch([alt_down, key_down, key_up, alt_up])
        except Exception:
            # Best-effort fallback ensuring Alt is released.
            try:
                _sendinput_key_event(int(_vk_for_key('alt')), keyup=False, extended=False, use_scancode=True)
                _sendinput_key(int(_vk_for_key(str(key))), extended=False, use_scancode=True)
            finally:
                _sendinput_key_event(int(_vk_for_key('alt')), keyup=True, extended=False, use_scancode=True)
