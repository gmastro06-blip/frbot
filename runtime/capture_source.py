from __future__ import annotations

import os

from adapters.windows import win32 as w32
from contracts.errors import PreflightFailed


def capture_source() -> str:
    """Return capture source selector.

    Allowed values:
    - 'client': capture directly from the game client window
    - 'obs': capture exclusively from OBS Projector window

    Default: 'client'
    """

    v = (os.environ.get('FRBOT_CAPTURE_SOURCE', '') or '').strip().lower()
    return 'obs' if v == 'obs' else 'client'


def obs_projector_title() -> str:
    return (os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or '').strip()


def resolve_obs_projector_hwnd() -> tuple[int, str]:
    """Resolve OBS projector HWND by title substring.

    Raises PreflightFailed('obs_projector_not_found') with details including
    expected_title and found_titles[] when not found.
    """

    expected = obs_projector_title()
    if not expected:
        exc = PreflightFailed('obs_projector_not_found')
        setattr(exc, 'details', {'expected_title': '', 'found_titles': []})
        raise exc

    match = None
    try:
        match = w32.find_window_by_title_substring(expected)
    except Exception:
        match = None

    if match is None:
        found: list[str] = []
        try:
            wins = w32.list_top_level_windows(title_substring='', visible_only=True)
            for wi in wins:
                t = str(getattr(wi, 'title', '') or '').strip()
                if t:
                    found.append(t)
        except Exception:
            found = []

        exc = PreflightFailed('obs_projector_not_found')
        setattr(exc, 'details', {'expected_title': str(expected), 'found_titles': found})
        raise exc

    hwnd = int(match.hwnd)
    title = str(match.title or '')

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
                'expected_hwnd': hex(int(hwnd)),
                'expected_title': str(expected_title),
                'foreground_hwnd': hex(int(fg)),
                'foreground_title': str(fg_title),
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
    return int(match.hwnd)
