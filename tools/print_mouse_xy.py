from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Point:
    x: int
    y: int


def _hard_stop(reason: str, *, details: dict[str, Any] | None = None, exit_code: int = 2) -> int:
    payload: dict[str, Any] = {"ok": False, "reason": str(reason)}
    if details:
        payload["details"] = details
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return int(exit_code)


def _try_import_win32() -> tuple[Any, Any]:
    if sys.platform != 'win32':
        return None, None
    try:
        import ctypes.wintypes as wintypes
        return ctypes, wintypes
    except Exception:
        return None, None


def _parse_hwnd(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    except Exception:
        return None


def _find_hwnd_by_title_substring(title_substring: str) -> int | None:
    ctypes, wintypes = _try_import_win32()
    if ctypes is None or wintypes is None:
        return None

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindows.argtypes = [wintypes.WNDENUMPROC, wintypes.LPARAM]
    EnumWindows.restype = wintypes.BOOL

    IsWindowVisible = user32.IsWindowVisible
    IsWindowVisible.argtypes = [wintypes.HWND]
    IsWindowVisible.restype = wintypes.BOOL

    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    GetWindowTextW.restype = ctypes.c_int

    wanted = title_substring.strip().lower()
    if not wanted:
        return None

    found: list[int] = []

    @wintypes.WNDENUMPROC
    def _cb(hwnd, lparam) -> int:  # type: ignore[no-untyped-def]
        try:
            if not IsWindowVisible(hwnd):
                return 1
            buf = ctypes.create_unicode_buffer(512)
            n = int(GetWindowTextW(hwnd, buf, 512))
            if n <= 0:
                return 1
            title = str(buf.value)
            if wanted in title.lower():
                found.append(int(hwnd))
                return 0
            return 1
        except Exception:
            return 1

    EnumWindows(_cb, 0)
    return found[0] if found else None


def _get_cursor_pos() -> Point | None:
    ctypes_module, wintypes_module = _try_import_win32()
    if ctypes_module is None or wintypes_module is None:
        return None

    user32 = ctypes_module.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes_module.LONG), ("y", wintypes_module.LONG)]

    pt = _POINT()
    ok = bool(user32.GetCursorPos(ctypes_module.byref(pt)))
    if not ok:
        return None
    return Point(int(pt.x), int(pt.y))


def _screen_to_client(hwnd: int, pt: Point) -> Point | None:
    ctypes_module, wintypes_module = _try_import_win32()
    if ctypes_module is None or wintypes_module is None:
        return None

    user32 = ctypes_module.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes_module.LONG), ("y", wintypes_module.LONG)]

    cpt = _POINT(int(pt.x), int(pt.y))
    ok = bool(user32.ScreenToClient(wintypes_module.HWND(hwnd), ctypes_module.byref(cpt)))
    if not ok:
        return None
    return Point(int(cpt.x), int(cpt.y))


def _get_client_size(hwnd: int) -> tuple[int, int] | None:
    ctypes_module, wintypes_module = _try_import_win32()
    if ctypes_module is None or wintypes_module is None:
        return None

    user32 = ctypes_module.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [("left", wintypes_module.LONG), ("top", wintypes_module.LONG), ("right", wintypes_module.LONG), ("bottom", wintypes_module.LONG)]

    r = _RECT()
    ok = bool(user32.GetClientRect(wintypes_module.HWND(hwnd), ctypes_module.byref(r)))
    if not ok:
        return None
    w = int(r.right - r.left)
    h = int(r.bottom - r.top)
    if w <= 0 or h <= 0:
        return None
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print current mouse coordinates (screen + optional window client coords)")
    ap.add_argument("--hwnd", default="", help="Target window HWND (decimal or hex like 0x00123456)")
    ap.add_argument("--title", default="", help="Target window title substring (used if --hwnd not provided)")
    ap.add_argument("--interval-ms", type=int, default=200, help="Polling interval when not --once")
    ap.add_argument("--once", action="store_true", help="Print once and exit")
    ap.add_argument("--json", action="store_true", help="Emit JSON per sample")
    args = ap.parse_args(argv)

    if sys.platform != 'win32':
        return _hard_stop('unsupported_platform', details={'platform': str(sys.platform), 'need': 'Windows'})

    ctypes, _wintypes = _try_import_win32()
    if ctypes is None:
        return _hard_stop("unsupported_platform", details={"need": "Windows + ctypes"})

    hwnd: int | None = _parse_hwnd(str(args.hwnd))
    if hwnd is None and str(args.title).strip():
        hwnd = _find_hwnd_by_title_substring(str(args.title))

    def _sample() -> dict[str, Any]:
        screen = _get_cursor_pos()
        out: dict[str, Any] = {"ok": True}
        if screen is None:
            out["ok"] = False
            out["reason"] = "get_cursor_failed"
            return out

        out["screen"] = {"x": screen.x, "y": screen.y}

        if hwnd is not None:
            client = _screen_to_client(hwnd, screen)
            size = _get_client_size(hwnd)
            if client is not None:
                out["client"] = {"x": client.x, "y": client.y}
            if size is not None:
                out["client_size"] = {"w": int(size[0]), "h": int(size[1])}
                if client is not None:
                    out["client_in_bounds"] = bool(0 <= client.x < size[0] and 0 <= client.y < size[1])
            out["hwnd"] = hex(int(hwnd))

        return out

    if args.once:
        payload = _sample()
        if args.json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            if not payload.get("ok"):
                sys.stdout.write(str(payload) + "\n")
                return 1
            s = payload.get("screen")
            c = payload.get("client")
            cs = payload.get("client_size")
            ib = payload.get("client_in_bounds")
            if s is not None:
                sys.stdout.write(f"screen=({s['x']},{s['y']})")
            if c is not None:
                sys.stdout.write(f" client=({c['x']},{c['y']})")
            if cs is not None:
                sys.stdout.write(f" client_size=({cs['w']}x{cs['h']})")
            if ib is not None:
                sys.stdout.write(f" in_bounds={ib}")
            if payload.get("hwnd"):
                sys.stdout.write(f" hwnd={payload['hwnd']}")
            sys.stdout.write("\n")
        return 0

    try:
        while True:
            payload = _sample()
            if args.json:
                sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            else:
                if not payload.get("ok"):
                    sys.stdout.write(str(payload) + "\n")
                else:
                    s = payload.get("screen")
                    c = payload.get("client")
                    cs = payload.get("client_size")
                    ib = payload.get("client_in_bounds")
                    if s is not None:
                        line = f"screen=({s['x']},{s['y']})"
                        if c is not None:
                            line += f" client=({c['x']},{c['y']})"
                        if cs is not None:
                            line += f" client_size=({cs['w']}x{cs['h']})"
                        if ib is not None:
                            line += f" in_bounds={ib}"
                        if payload.get("hwnd"):
                            line += f" hwnd={payload['hwnd']}"
                        sys.stdout.write(line + "\n")
            sys.stdout.flush()
            time.sleep(max(0.01, int(args.interval_ms)) / 1000.0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
