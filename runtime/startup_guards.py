from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
from dataclasses import asdict, dataclass

from adapters.windows import win32 as w32
from contracts.errors import PreflightFailed
from diagnostics.fatal import write_fatal
from runtime.env import parse_window_hwnd_env


_PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE: bool = False
_PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED: bool = False


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


def _env_int_opt(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except Exception:
        return None


def _profile() -> str:
    return (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()


def _mode() -> str:
    return (os.environ.get('FRBOT_MODE', '') or '').strip().lower()


def _capture_source() -> str:
    v = (os.environ.get('FRBOT_CAPTURE_SOURCE', '') or '').strip().lower()
    if not v:
        return 'obs_source'
    if v == 'obs_source':
        return 'obs_source'
    return 'obs' if v == 'obs' else 'client'


def _obs_source_name() -> str:
    return str(os.environ.get('FRBOT_OBS_SOURCE_NAME') or '').strip()


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


def _list_found_windows() -> list[dict[str, object]]:
    try:
        wins, _mons = w32.list_visible_windows_diagnostic()
    except Exception:
        return []
    out: list[dict[str, object]] = []
    for wi in wins[:80]:
        try:
            out.append(w32.window_diag_to_dict(wi))
        except Exception:
            continue
    return out


def _write_window_diagnostics_json(*, expected_title: str, resolved_hwnd: int, resolved_title: str, found_windows: list[dict[str, object]]) -> None:
    try:
        out = {
            'ts': time.time(),
            'expected_title': str(expected_title or ''),
            'resolved_hwnd': hex(int(resolved_hwnd)) if int(resolved_hwnd) > 0 else None,
            'resolved_title': str(resolved_title or ''),
            'found_windows': list(found_windows),
        }
        p = Path('diagnostics') / 'window_diagnostics.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return


def _ensure_trace_initialized() -> None:
    # PROD-EMERGENCY REAL requires trace to exist even if we hard-stop in startup guards.
    try:
        if _profile() != 'prod_emergency' or _mode() != 'real':
            return
        frames_dir = Path('diagnostics') / 'frames_emergency'
        frames_dir.mkdir(parents=True, exist_ok=True)
        p = frames_dir / 'cavebot_trace.jsonl'
        if not p.exists():
            p.write_text('', encoding='utf-8')
    except Exception:
        return


def _resolve_hwnd_by_title(title: str) -> tuple[int, str]:
    t = str(title or '').strip()
    if not t:
        return 0, ''
    try:
        exact = w32.find_window_by_title_exact(t)
        if exact is not None:
            return int(exact.hwnd), str(exact.title)
    except Exception:
        pass
    try:
        sub = w32.find_window_by_title_substring(t)
        if sub is not None:
            return int(sub.hwnd), str(sub.title)
    except Exception:
        pass
    return 0, ''


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
    - FRBOT_MODE must be 'real' or 'combat_basic' (real-mode gates)
        - Capture source selector:
            - FRBOT_CAPTURE_SOURCE=client (default): must provide FRBOT_WINDOW_HWND or FRBOT_WINDOW_TITLE
            - FRBOT_CAPTURE_SOURCE=obs: must provide FRBOT_OBS_PROJECTOR_TITLE
        - The selected capture HWND must exist, be visible, not minimized, and be foreground
    - No focus stealing: we never attempt to activate/focus a window
    """

    global _PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE
    global _PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED

    _ensure_trace_initialized()

    if _profile() != 'prod_emergency':
        return

    # Contract: foreground is verified at startup only (never during runtime).
    # Multiple preflight entrypoints may call this; make it idempotent per-process.
    if bool(_PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE):
        return

    if sys.platform != 'win32':
        exc = PreflightFailed('unsupported_platform')
        if write_fatal_on_fail:
            write_fatal('unsupported_platform', exc, details={'platform': str(sys.platform)})
        raise exc

    # Allow running independent gates via main.py routing.
    if _mode() not in {'real', 'combat_basic', 'looting_basic', 'looting_full', 'deposit_basic', 'trade_basic', 'targeting', 'healing', 'cavebot'}:
        exc = PreflightFailed('invalid_mode')
        details: dict[str, object] = {
            'mode': _mode(),
            'required': ['real', 'combat_basic', 'looting_basic', 'looting_full', 'deposit_basic', 'trade_basic', 'targeting', 'healing', 'cavebot'],
        }
        setattr(exc, 'details', details)
        if write_fatal_on_fail:
            write_fatal('invalid_mode', exc, details=details)
        raise exc

    # InputAuthority: ALWAYS Tibia HWND/title (strict foreground).
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
    resolved_title = ''
    hwnd_parse_error: str | None = None

    if raw_hwnd:
        try:
            hwnd = int(parse_window_hwnd_env('FRBOT_WINDOW_HWND'))
        except PreflightFailed:
            # Auto-resolve by title when HWND is invalid.
            hwnd = 0
            hwnd_parse_error = 'window_hwnd_invalid'

    # If a parsed HWND is stale (no longer valid), treat as invalid and fall back to title.
    if int(hwnd) > 0:
        try:
            if (not w32.is_window(int(hwnd))) or (not w32.is_window_visible(int(hwnd))) or bool(w32.is_window_minimized(int(hwnd))):
                hwnd = 0
                if hwnd_parse_error is None:
                    hwnd_parse_error = 'window_hwnd_invalid'
        except Exception:
            hwnd = 0
            if hwnd_parse_error is None:
                hwnd_parse_error = 'window_hwnd_invalid'

    if hwnd <= 0 and title_substring:
        hwnd, resolved_title = _resolve_hwnd_by_title(str(title_substring))

    # One-time self-heal: if invalid, re-enumerate and retry resolve once.
    if (hwnd <= 0) and title_substring and (not _PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED):
        _PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED = True
        hwnd2, resolved_title2 = _resolve_hwnd_by_title(str(title_substring))
        if int(hwnd2) > 0:
            hwnd = int(hwnd2)
            resolved_title = str(resolved_title2 or resolved_title)

    # If we resolved a HWND via title (or self-heal), persist it for downstream preflight.
    if int(hwnd) > 0 and (not raw_hwnd or hwnd_parse_error is not None):
        try:
            os.environ['FRBOT_WINDOW_HWND'] = hex(int(hwnd))
        except Exception:
            pass

    found_windows = _list_found_windows()
    info = _collect_details(hwnd=int(hwnd), title_substring=str(title_substring))
    d = asdict(info)
    d.update(
        {
            'expected_title': str(title_substring or ''),
            'resolved_hwnd': hex(int(hwnd)) if int(hwnd) > 0 else None,
            'resolved_title': str(resolved_title or ''),
            'hwnd_raw': str(raw_hwnd or ''),
            'hwnd_parse_error': hwnd_parse_error,
            'found_windows': list(found_windows),
            'capture_source': _capture_source(),
            'obs_source_name': _obs_source_name(),
        }
    )

    _write_window_diagnostics_json(
        expected_title=str(title_substring or ''),
        resolved_hwnd=int(hwnd),
        resolved_title=str(resolved_title or ''),
        found_windows=list(found_windows),
    )

    if int(hwnd) <= 0 or not w32.is_window(int(hwnd)):
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
        # Optional operator-wait for foreground (still no focus stealing).
        retries_opt = _env_int_opt('FRBOT_FOREGROUND_RETRIES')
        retries = int(retries_opt) if retries_opt is not None else 0
        delay_ms = int(
            _env_int_opt('FRBOT_FOREGROUND_DELAY_MS')
            or _env_int_opt('FRBOT_FOREGROUND_RETRY_DELAY_MS')
            or 150
        )

        last_fg = int(info.foreground_hwnd)
        last_title = str(w32.get_window_text(int(last_fg)) or '') if int(last_fg) > 0 else ''

        # Import locally to keep startup dependencies minimal.
        from runtime.pacing import sleep_ms

        for attempt in range(max(0, int(retries)) + 1):
            try:
                fg_now = int(w32.get_foreground_window())
            except Exception:
                fg_now = 0
            last_fg = int(fg_now)
            last_title = str(w32.get_window_text(int(last_fg)) or '') if int(last_fg) > 0 else ''
            if int(fg_now) == int(hwnd):
                # Refresh info for downstream consumers.
                info = _collect_details(hwnd=int(hwnd), title_substring=str(title_substring))
                break
            if attempt < int(retries):
                sleep_ms(max(0.0, float(delay_ms)))

        if int(info.foreground_hwnd) != int(hwnd):
            pf = PreflightFailed('window_not_foreground')
            details_fg = {
                'reason': 'window_not_foreground',
                'expected_foreground': 'TIBIA',
                'window_hwnd': hex(int(hwnd)),
                'foreground_hwnd': hex(int(last_fg)),
                'foreground_title': str(last_title),
                'hint': 'Focus Tibia window and rerun',
                'try_focus': False,
                'retries': int(retries),
                'delay_ms': int(delay_ms),
            }
            setattr(pf, 'details', details_fg)
            if write_fatal_on_fail:
                write_fatal('window_not_foreground', pf, details=details_fg)
            raise pf

    # CaptureAuthority: OBS Source Identity (no HWND/foreground/monitor checks).
    if _capture_source() == 'obs_source':
        src = _obs_source_name()
        if not src:
            pf = PreflightFailed('obs_source_not_found')
            details = {'reason': 'obs_source_not_found', 'obs_source_name': ''}
            setattr(pf, 'details', details)
            if write_fatal_on_fail:
                write_fatal('obs_source_not_found', pf, details=details)
            raise pf
        _PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE = True
        return

    # PROD-EMERGENCY: legacy projector mode is disabled (capture must be OBS source identity).
    if _capture_source() == 'obs':
        pf = PreflightFailed('capture_source_invalid')
        details = {
            'reason': 'capture_source_invalid',
            'capture_source': 'obs',
            'required': 'obs_source',
            'hint': 'Set FRBOT_CAPTURE_SOURCE=obs_source and provide FRBOT_OBS_SOURCE_NAME',
        }
        setattr(pf, 'details', details)
        if write_fatal_on_fail:
            write_fatal('capture_source_invalid', pf, details=details)
        raise pf

    return
