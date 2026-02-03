from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from adapters.windows import win32 as w32
from contracts.errors import PreflightFailed
from diagnostics.fatal import write_fatal
from runtime.env import parse_window_hwnd_env


@dataclass(frozen=True, slots=True)
class StartupGuardDetails:
    hwnd: int
    foreground_hwnd: int
    title_substring: str
    title_now: str
    visible: bool
    minimized: bool
    platform: str
    dpi_awareness: dict[str, object]


def _env_str(name: str) -> str:
    return str(os.environ.get(name) or '').strip()


def _profile() -> str:
    return (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()


def _mode() -> str:
    return (os.environ.get('FRBOT_MODE', '') or '').strip().lower()


def _capture_source() -> str:
    v = (os.environ.get('FRBOT_CAPTURE_SOURCE', '') or '').strip().lower()
    return 'obs' if v == 'obs' else 'client'


def _obs_projector_title() -> str:
    return str(os.environ.get('FRBOT_OBS_PROJECTOR_TITLE') or '').strip()


def _found_titles() -> list[str]:
    try:
        wins = w32.list_top_level_windows(title_substring='', visible_only=True)
    except Exception:
        return []
    out: list[str] = []
    for wi in wins:
        t = str(getattr(wi, 'title', '') or '').strip()
        if t:
            out.append(t)
    return out


def _collect_details(*, hwnd: int, title_substring: str) -> StartupGuardDetails:
    try:
        fg = int(w32.get_foreground_window())
    except Exception:
        fg = 0
    try:
        title_now = str(w32.get_window_text(int(hwnd))) if int(hwnd) > 0 else ''
    except Exception:
        title_now = ''
    try:
        visible = bool(w32.is_window_visible(int(hwnd))) if int(hwnd) > 0 else False
    except Exception:
        visible = False
    try:
        minimized = bool(w32.is_window_minimized(int(hwnd))) if int(hwnd) > 0 else False
    except Exception:
        minimized = False
    try:
        dpi = w32.get_dpi_awareness_status()
    except Exception:
        dpi = {}

    return StartupGuardDetails(
        hwnd=int(hwnd),
        foreground_hwnd=int(fg),
        title_substring=str(title_substring or ''),
        title_now=str(title_now or ''),
        visible=bool(visible),
        minimized=bool(minimized),
        platform=str(sys.platform),
        dpi_awareness=dict(dpi),
    )


def enforce_prod_emergency_real_startup_guards(*, write_fatal_on_fail: bool) -> None:
    """Enforce non-negotiable startup guards for PROD-EMERGENCY REAL.

    Requirements:
    - Windows-only
    - FRBOT_PROFILE=prod_emergency (enforced by caller)
    - FRBOT_MODE must be 'real'
        - Capture source selector:
            - FRBOT_CAPTURE_SOURCE=client (default): must provide FRBOT_WINDOW_HWND or FRBOT_WINDOW_TITLE
            - FRBOT_CAPTURE_SOURCE=obs: must provide FRBOT_OBS_PROJECTOR_TITLE
        - The selected capture HWND must exist, be visible, not minimized, and be foreground
    - No focus stealing: we never attempt to activate/focus a window
    """

    if _profile() != 'prod_emergency':
        return

    if sys.platform != 'win32':
        exc = PreflightFailed('unsupported_platform')
        if write_fatal_on_fail:
            write_fatal('unsupported_platform', exc, details={'platform': str(sys.platform)})
        raise exc

    if _mode() != 'real':
        exc = PreflightFailed('invalid_mode')
        details: dict[str, object] = {'mode': _mode(), 'required': 'real'}
        setattr(exc, 'details', details)
        if write_fatal_on_fail:
            write_fatal('invalid_mode', exc, details=details)
        raise exc

    # OBS capture mode: enforce OBS projector window instead of Tibia client.
    if _capture_source() == 'obs':
        expected_title = _obs_projector_title()
        if not expected_title:
            exc = PreflightFailed('obs_projector_not_found')
            details = {'expected_title': '', 'found_titles': []}
            setattr(exc, 'details', details)
            if write_fatal_on_fail:
                write_fatal('obs_projector_not_found', exc, details=details)
            raise exc

        try:
            match = w32.find_window_by_title_substring(str(expected_title))
        except Exception:
            match = None
        if match is None:
            exc = PreflightFailed('obs_projector_not_found')
            details = {'expected_title': str(expected_title), 'found_titles': _found_titles()}
            setattr(exc, 'details', details)
            if write_fatal_on_fail:
                write_fatal('obs_projector_not_found', exc, details=details)
            raise exc

        hwnd = int(match.hwnd)
        title_substring = str(expected_title)

        info = _collect_details(hwnd=int(hwnd), title_substring=title_substring)
        d = info.__dict__

        if not w32.is_window(int(hwnd)):
            pf = PreflightFailed('window_hwnd_invalid')
            setattr(pf, 'details', d)
            if write_fatal_on_fail:
                write_fatal('window_hwnd_invalid', pf, details=d)
            raise pf

        if not info.visible:
            pf = PreflightFailed('window_not_visible')
            setattr(pf, 'details', d)
            if write_fatal_on_fail:
                write_fatal('window_not_visible', pf, details=d)
            raise pf

        if info.minimized:
            pf = PreflightFailed('window_minimized')
            setattr(pf, 'details', d)
            if write_fatal_on_fail:
                write_fatal('window_minimized', pf, details=d)
            raise pf

        if int(info.foreground_hwnd) != int(hwnd):
            pf = PreflightFailed('obs_projector_foreground_mismatch')
            setattr(pf, 'details', d)
            if write_fatal_on_fail:
                write_fatal('obs_projector_foreground_mismatch', pf, details=d)
            raise pf

        return

    raw_hwnd = _env_str('FRBOT_WINDOW_HWND')
    title_substring = _env_str('FRBOT_WINDOW_TITLE')
    if not raw_hwnd and not title_substring:
        exc = PreflightFailed('window_selector_missing')
        details = {'required': ['FRBOT_WINDOW_HWND', 'FRBOT_WINDOW_TITLE']}
        setattr(exc, 'details', details)
        if write_fatal_on_fail:
            write_fatal('window_selector_missing', exc, details=details)
        raise exc

    hwnd = 0
    if raw_hwnd:
        try:
            hwnd = int(parse_window_hwnd_env('FRBOT_WINDOW_HWND'))
        except PreflightFailed as err_exc:
            d0 = {'hwnd': raw_hwnd}
            setattr(err_exc, 'details', d0)
            if write_fatal_on_fail:
                write_fatal('window_hwnd_invalid', err_exc, details=d0)
            raise
    else:
        # Resolve hwnd from title substring deterministically.
        try:
            match = w32.find_window_by_title_substring(str(title_substring))
        except Exception:
            match = None
        if match is None:
            pf = PreflightFailed('window_not_found')
            d0 = {'title_substring': str(title_substring)}
            setattr(pf, 'details', d0)
            if write_fatal_on_fail:
                write_fatal('window_not_found', pf, details=d0)
            raise pf
        hwnd = int(match.hwnd)

    info = _collect_details(hwnd=int(hwnd), title_substring=title_substring)
    d = info.__dict__

    if not w32.is_window(int(hwnd)):
        pf = PreflightFailed('window_hwnd_invalid')
        setattr(pf, 'details', d)
        if write_fatal_on_fail:
            write_fatal('window_hwnd_invalid', pf, details=d)
        raise pf

    # Title check is additive (must still match when provided).
    if title_substring.strip() and title_substring.strip().lower() not in (info.title_now or '').lower():
        pf = PreflightFailed('window_title_mismatch')
        setattr(pf, 'details', d)
        if write_fatal_on_fail:
            write_fatal('window_title_mismatch', pf, details=d)
        raise pf

    if not info.visible:
        pf = PreflightFailed('window_not_visible')
        setattr(pf, 'details', d)
        if write_fatal_on_fail:
            write_fatal('window_not_visible', pf, details=d)
        raise pf

    if info.minimized:
        pf = PreflightFailed('window_minimized')
        setattr(pf, 'details', d)
        if write_fatal_on_fail:
            write_fatal('window_minimized', pf, details=d)
        raise pf

    if int(info.foreground_hwnd) != int(hwnd):
        pf = PreflightFailed('window_not_foreground')
        setattr(pf, 'details', d)
        if write_fatal_on_fail:
            write_fatal('window_not_foreground', pf, details=d)
        raise pf
