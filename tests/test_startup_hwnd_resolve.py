from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch


def _reset_artifacts() -> None:
    Path('diagnostics').mkdir(parents=True, exist_ok=True)
    (Path('diagnostics') / 'fatal.log').unlink(missing_ok=True)
    (Path('diagnostics') / 'window_diagnostics.json').unlink(missing_ok=True)


def test_invalid_hwnd_env_auto_resolves_by_title(monkeypatch: MonkeyPatch) -> None:
    from runtime import startup_guards

    _reset_artifacts()

    startup_guards._PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE = False
    startup_guards._PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED = False

    # Force guard path.
    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_SOURCE', 'obs_source')
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'Tibia_Fuente')

    # Invalid explicit HWND -> must resolve via title.
    monkeypatch.setenv('FRBOT_WINDOW_HWND', 'not_a_number')
    monkeypatch.setenv('FRBOT_WINDOW_TITLE', 'Tibia - Onniwabanshu')

    # Pretend to be on Windows.
    monkeypatch.setattr(startup_guards, 'sys', type('S', (), {'platform': 'win32'})())

    class _Match:
        hwnd = 0x123
        title = 'Tibia - Onniwabanshu'

    # Stub all Win32 calls used by guards.
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_exact', lambda t: _Match())
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_substring', lambda t: None)
    monkeypatch.setattr(startup_guards.w32, 'list_visible_windows_diagnostic', lambda: ([], []))
    monkeypatch.setattr(startup_guards.w32, 'window_diag_to_dict', lambda w: {})
    monkeypatch.setattr(startup_guards.w32, 'is_window', lambda hwnd: True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_visible', lambda hwnd: True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_minimized', lambda hwnd: False)
    monkeypatch.setattr(startup_guards.w32, 'get_foreground_window', lambda: 0x123)
    monkeypatch.setattr(startup_guards.w32, 'get_window_text', lambda hwnd: 'Tibia - Onniwabanshu')
    monkeypatch.setattr(startup_guards.w32, 'get_dpi_awareness_status', lambda: {})

    startup_guards.enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)

    # Should persist resolved HWND for downstream preflight.
    assert int(str(os.environ.get('FRBOT_WINDOW_HWND') or '0'), 0) == 0x123

    wd = Path('diagnostics') / 'window_diagnostics.json'
    assert wd.exists()
    rec = json.loads(wd.read_text(encoding='utf-8', errors='replace') or '{}')
    assert rec.get('resolved_hwnd') == hex(0x123)


def test_invalid_hwnd_and_no_matching_title_hard_stops_with_candidates(monkeypatch: MonkeyPatch) -> None:
    from runtime import startup_guards

    _reset_artifacts()

    startup_guards._PROD_EMERGENCY_REAL_GUARDS_PASSED_ONCE = False
    startup_guards._PROD_EMERGENCY_REAL_HWND_SELF_HEAL_USED = False

    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_SOURCE', 'obs_source')
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'Tibia_Fuente')

    monkeypatch.setenv('FRBOT_WINDOW_HWND', '0xNOPE')
    monkeypatch.setenv('FRBOT_WINDOW_TITLE', 'DefinitelyNotATibiaWindow')

    monkeypatch.setattr(startup_guards, 'sys', type('S', (), {'platform': 'win32'})())

    class _Win:
        pass

    dummy = _Win()

    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_exact', lambda t: None)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_substring', lambda t: None)
    monkeypatch.setattr(startup_guards.w32, 'list_visible_windows_diagnostic', lambda: ([dummy], []))
    monkeypatch.setattr(
        startup_guards.w32,
        'window_diag_to_dict',
        lambda w: {'hwnd': '0x999', 'title': 'Some Other Window', 'is_visible': True, 'is_minimized': False, 'z_order': 0, 'pid': 1, 'rect': {'left': 0, 'top': 0, 'right': 1, 'bottom': 1}, 'monitor': {'device': None, 'primary': None}},
    )
    monkeypatch.setattr(startup_guards.w32, 'is_window', lambda hwnd: False)
    monkeypatch.setattr(startup_guards.w32, 'is_window_visible', lambda hwnd: False)
    monkeypatch.setattr(startup_guards.w32, 'is_window_minimized', lambda hwnd: False)
    monkeypatch.setattr(startup_guards.w32, 'get_foreground_window', lambda: 0)
    monkeypatch.setattr(startup_guards.w32, 'get_window_text', lambda hwnd: '')
    monkeypatch.setattr(startup_guards.w32, 'get_dpi_awareness_status', lambda: {})

    with pytest.raises(Exception):
        startup_guards.enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)

    fatal = Path('diagnostics') / 'fatal.log'
    assert fatal.exists()
    rec = json.loads(fatal.read_text(encoding='utf-8', errors='replace') or '{}')
    assert rec.get('reason') == 'window_hwnd_invalid'
    details = rec.get('details') or {}
    assert details.get('expected_title') == 'DefinitelyNotATibiaWindow'
    assert isinstance(details.get('found_windows'), list)
    assert details.get('found_windows'), 'expected at least one candidate window'
