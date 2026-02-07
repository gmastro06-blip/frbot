from __future__ import annotations

import os

from adapters.windows import win32 as w32
from contracts.errors import PreflightFailed


def capture_source() -> str:
    """Return capture source selector.

    Allowed values:
    - 'client': capture directly from the game client window
    - 'obs': capture exclusively from OBS Projector window
    - 'obs_source': capture directly from OBS source identity (no HWND/foreground)

    Default: 'obs_source' (REAL-safe by default; decouples capture from HWND focus)
    """

    v = (os.environ.get('FRBOT_CAPTURE_SOURCE', '') or '').strip().lower()
    if not v:
        return 'obs_source'
    if v == 'obs_source':
        return 'obs_source'
    return 'obs' if v == 'obs' else 'client'


def obs_projector_title() -> str:
    return (os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or '').strip()


def resolve_obs_projector_hwnd() -> tuple[int, str]:
    """Resolve OBS projector HWND without assuming monitor.

    Selection rules (strict, monitor-agnostic):
    - title contains searched_title (case-insensitive)
    - window is visible
    - window is NOT minimized
    - client rect is valid (width/height > 0)

    On failure raises PreflightFailed('obs_projector_not_detected') with rich
    diagnostics suitable for fatal evidence.
    """

    searched = obs_projector_title()
    if not searched:
        exc = PreflightFailed('obs_projector_not_detected')
        setattr(
            exc,
            'details',
            {
                'reason': 'obs_projector_not_detected',
                'searched_title': '',
                'windows_seen': [],
                'monitors_seen': [],
                'hint': 'Ensure OBS Projector window is open and not minimized',
            },
        )
        raise exc

    searched_norm = str(searched).strip()
    searched_lower = searched_norm.lower()

    try:
        windows_seen, monitors_seen = w32.list_visible_windows_diagnostic()
    except Exception:
        windows_seen, monitors_seen = [], []

    try:
        fg_hwnd = int(w32.get_foreground_window() or 0)
    except Exception:
        fg_hwnd = 0
    try:
        fg_title = str(w32.get_window_text(int(fg_hwnd))) if int(fg_hwnd) > 0 else ''
    except Exception:
        fg_title = ''

    # Best-effort resolve Tibia/input HWND for diagnostics (never required foreground).
    tibia_title = (os.environ.get('FRBOT_WINDOW_TITLE', '') or '').strip()
    tibia_hwnd = 0
    raw_tibia_hwnd = (os.environ.get('FRBOT_WINDOW_HWND', '') or '').strip()
    if raw_tibia_hwnd:
        try:
            tibia_hwnd = int(raw_tibia_hwnd, 0)
        except Exception:
            tibia_hwnd = 0
    if tibia_hwnd <= 0 and tibia_title:
        try:
            match = w32.find_window_by_title_substring(str(tibia_title))
            if match is not None:
                tibia_hwnd = int(match.hwnd)
        except Exception:
            tibia_hwnd = 0

    projector_candidates: list[dict[str, object]] = []

    selected_hwnd = 0
    selected_title = ''

    # EnumWindows order is z-order; pick the topmost acceptable candidate.
    for w in windows_seen:
        t = str(getattr(w, 'title', '') or '').strip()
        if not t:
            continue
        if searched_lower not in t.lower():
            continue

        hwnd = int(getattr(w, 'hwnd', 0) or 0)
        visible = bool(getattr(w, 'visible', True))
        minimized = bool(getattr(w, 'minimized', False))
        reason_reject: str | None = None

        if not visible:
            reason_reject = 'invisible'
        elif minimized:
            reason_reject = 'minimized'
        else:
            try:
                rect = w32.get_client_rect_in_screen(int(hwnd))
                if int(rect.width) <= 0 or int(rect.height) <= 0:
                    reason_reject = 'rect_invalid'
            except Exception:
                reason_reject = 'rect_invalid'

        projector_candidates.append(
            {
                'hwnd': hex(int(hwnd)),
                'title': t,
                'z_order': int(getattr(w, 'z_order', -1)),
                'is_visible': bool(visible),
                'is_minimized': bool(minimized),
                'reject_reason': reason_reject,
            }
        )

        if reason_reject is None and int(selected_hwnd) <= 0:
            selected_hwnd = int(hwnd)
            selected_title = t

    if int(selected_hwnd) <= 0:
        exc = PreflightFailed('obs_projector_not_detected')
        setattr(
            exc,
            'details',
            {
                'reason': 'obs_projector_not_detected',
                'searched_title': searched_norm,
                'windows_seen': [w32.window_diag_to_dict(w) for w in windows_seen],
                'monitors_seen': [w32.monitor_info_to_dict(m) for m in monitors_seen],
                'projector_candidates': projector_candidates,
                'tibia_hwnd': hex(int(tibia_hwnd)) if int(tibia_hwnd) > 0 else '0x0',
                'tibia_title': str(tibia_title),
                'foreground_hwnd': hex(int(fg_hwnd)),
                'foreground_title': str(fg_title),
                'hint': 'Ensure OBS Projector window is open and not minimized',
            },
        )
        raise exc

    hwnd = int(selected_hwnd)
    title = str(selected_title)

    try:
        if not w32.is_window(int(hwnd)):
            exc = PreflightFailed('window_hwnd_invalid')
            setattr(exc, 'details', {'hwnd': hex(int(hwnd)), 'title': title})
            raise exc
    except PreflightFailed:
        raise
    except Exception:
        pass

    try:
        if not w32.is_window_visible(hwnd):
            exc = PreflightFailed('window_not_visible')
            setattr(exc, 'details', {'hwnd': hex(int(hwnd)), 'title': title})
            raise exc
    except PreflightFailed:
        raise
    except Exception:
        pass

    try:
        if w32.is_window_minimized(hwnd):
            exc = PreflightFailed('window_minimized')
            setattr(exc, 'details', {'hwnd': hex(int(hwnd)), 'title': title})
            raise exc
    except PreflightFailed:
        raise
    except Exception:
        pass

    # Rect must be valid for ROI cropping.
    try:
        rect = w32.get_client_rect_in_screen(int(hwnd))
        if int(rect.width) <= 0 or int(rect.height) <= 0:
            raise RuntimeError('hwnd_rect_invalid')
    except Exception as rect_exc:
        exc = PreflightFailed('hwnd_rect_invalid')
        setattr(exc, 'details', {'hwnd': hex(int(hwnd)), 'title': title, 'error': f'{type(rect_exc).__name__}: {rect_exc}'})
        raise exc

    return hwnd, title


def enforce_obs_projector_foreground(*, hwnd: int, expected_title: str) -> None:
    """Strict guard: OBS projector must be foreground; never steal focus."""

    fg = 0
    fg_title = ''
    try:
        fg = int(w32.get_foreground_window() or 0)
    except Exception:
        fg = 0
    try:
        fg_title = str(w32.get_window_text(int(fg))) if int(fg) > 0 else ''
    except Exception:
        fg_title = ''

    if int(fg) != int(hwnd):
        exc = PreflightFailed('obs_projector_foreground_mismatch')
        setattr(
            exc,
            'details',
            {
                'reason': 'obs_projector_foreground_mismatch',
                'expected_foreground': 'OBS_PROJECTOR',
                'projector_hwnd': hex(int(hwnd)),
                'foreground_hwnd': hex(int(fg)),
                'foreground_title': str(fg_title),
                'hint': 'Click OBS projector window and rerun',
            },
        )
        raise exc


def resolve_input_hwnd(*, hwnd: int, title_substring: str) -> int:
    """Resolve an input HWND without requiring foreground.

    This is intentionally weaker than Win32WindowBinding.verify(): in OBS capture
    mode, capture and input windows are different.
    """

    h = int(hwnd)
    if h > 0:
        try:
            if not w32.is_window(h):
                raise RuntimeError('hwnd_invalid')
            if not w32.is_window_visible(h):
                raise RuntimeError('hwnd_invalid')
            if w32.is_window_minimized(h):
                raise RuntimeError('hwnd_invalid')
        except Exception:
            # Provided HWND can go stale; fall back to title-based resolve below.
            h = 0
        else:
            return h

    needle = (title_substring or '').strip()
    if not needle:
        return 0

    try:
        match = w32.find_window_by_title_substring(needle)
    except Exception:
        match = None
    if match is None:
        return 0
    mh = int(match.hwnd)
    if mh <= 0:
        return 0
    try:
        if not w32.is_window(mh):
            return 0
        if not w32.is_window_visible(mh):
            return 0
        if w32.is_window_minimized(mh):
            return 0
    except Exception:
        return 0
    return mh
