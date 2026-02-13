from __future__ import annotations

import ctypes
import sys

if sys.platform == 'win32':
    from ctypes import wintypes
else:  # pragma: no cover
    wintypes = None  # type: ignore[assignment]
from dataclasses import dataclass

from contracts.window import WindowRect


@dataclass(frozen=True, slots=True)
class GdiCaptureResult:
    ok: bool
    reason: str
    width: int = 0
    height: int = 0
    # BGRA bytes (len == width*height*4) when ok
    bgra: bytes = b''
    last_error: int | None = None
    method: str = 'PrintWindow'


def capture_client_bgra(hwnd: int, rect: WindowRect) -> GdiCaptureResult:
    """Diagnostic-only capture of a HWND client region via GDI PrintWindow.

    Strictly targets the provided HWND. Never falls back to other methods.

    Notes:
    - PrintWindow can fail (returns 0) for some clients (e.g., protected surfaces).
    - Caller must ensure hwnd is foreground/visible if that's an invariant.
    """

    if sys.platform != 'win32':
        return GdiCaptureResult(ok=False, reason='gdi_non_windows')

    width = int(rect.width)
    height = int(rect.height)
    if width <= 0 or height <= 0:
        return GdiCaptureResult(ok=False, reason='gdi_invalid_rect')

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

    hwnd_h = wintypes.HWND(int(hwnd))

    hdc_window = user32.GetWindowDC(hwnd_h)
    if not hdc_window:
        return GdiCaptureResult(ok=False, reason='gdi_GetWindowDC_failed', last_error=ctypes.get_last_error())

    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    if not hdc_mem:
        user32.ReleaseDC(hwnd_h, hdc_window)
        return GdiCaptureResult(ok=False, reason='gdi_CreateCompatibleDC_failed', last_error=ctypes.get_last_error())

    hbm = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    if not hbm:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd_h, hdc_window)
        return GdiCaptureResult(ok=False, reason='gdi_CreateCompatibleBitmap_failed', last_error=ctypes.get_last_error())

    old = gdi32.SelectObject(hdc_mem, hbm)

    try:
        PW_CLIENTONLY = 0x00000001
        ok = bool(user32.PrintWindow(hwnd_h, hdc_mem, PW_CLIENTONLY))
        if not ok:
            return GdiCaptureResult(ok=False, reason='gdi_PrintWindow_failed', last_error=ctypes.get_last_error())

        # Request 32-bit BGRA top-down DIB.
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wintypes.DWORD),
                ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]

        BI_RGB = 0
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = width * height * 4

        buf = (ctypes.c_ubyte * (width * height * 4))()
        scanlines = gdi32.GetDIBits(hdc_mem, hbm, 0, height, ctypes.byref(buf), ctypes.byref(bmi), 0)
        if int(scanlines) != int(height):
            return GdiCaptureResult(ok=False, reason='gdi_GetDIBits_failed', last_error=ctypes.get_last_error())

        return GdiCaptureResult(ok=True, reason='ok', width=width, height=height, bgra=bytes(buf))
    finally:
        try:
            if old:
                gdi32.SelectObject(hdc_mem, old)
        except Exception:
            pass
        try:
            gdi32.DeleteObject(hbm)
        except Exception:
            pass
        try:
            gdi32.DeleteDC(hdc_mem)
        except Exception:
            pass
        try:
            user32.ReleaseDC(hwnd_h, hdc_window)
        except Exception:
            pass
