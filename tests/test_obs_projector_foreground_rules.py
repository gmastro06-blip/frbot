from __future__ import annotations

import json
from pathlib import Path

import pytest


def _set_prod_emergency_obs_source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_SOURCE', 'obs_source')
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'TibiaSource')
    monkeypatch.setenv('FRBOT_WINDOW_TITLE', 'Tibia - Onniwabanshu')
    # Hermetic: developer shells may have a stale FRBOT_WINDOW_HWND.
    monkeypatch.delenv('FRBOT_WINDOW_HWND', raising=False)


def test_obs_source_ignores_projector_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime import startup_guards

    _set_prod_emergency_obs_source_env(monkeypatch)
    monkeypatch.setattr(startup_guards.sys, 'platform', 'win32', raising=False)

    tibia_hwnd = 0x222

    monkeypatch.setattr(startup_guards.w32, 'get_foreground_window', lambda: int(tibia_hwnd), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_exact', lambda _t: None, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_substring', lambda _t: type('W', (), {'hwnd': int(tibia_hwnd), 'title': 'Tibia - Onniwabanshu'})(), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_window_text', lambda hwnd: 'Tibia - Onniwabanshu', raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_visible', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_minimized', lambda hwnd: False, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_dpi_awareness_status', lambda: {}, raising=True)

    # If obs_source startup guards accidentally depend on projector/window geometry,
    # this would raise and fail the test.
    monkeypatch.setattr(startup_guards.w32, 'get_client_rect_in_screen', lambda hwnd: (_ for _ in ()).throw(AssertionError('should not be called')), raising=True)

    # Each test should behave like a fresh process.
    startup_guards._PROD_PROFILE_REAL_GUARDS_PASSED_ONCE = False
    startup_guards._PROD_PROFILE_REAL_HWND_SELF_HEAL_USED = False
    startup_guards.enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)


def test_input_still_requires_hwnd_foreground(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime import startup_guards
    from contracts.errors import PreflightFailed

    monkeypatch.chdir(tmp_path)
    _set_prod_emergency_obs_source_env(monkeypatch)
    monkeypatch.setattr(startup_guards.sys, 'platform', 'win32', raising=False)

    tibia_hwnd = 0x222
    other_hwnd = 0x333

    monkeypatch.setattr(startup_guards.w32, 'get_foreground_window', lambda: int(other_hwnd), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_exact', lambda _t: None, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_substring', lambda _t: type('W', (), {'hwnd': int(tibia_hwnd), 'title': 'Tibia - Onniwabanshu'})(), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_window_text', lambda hwnd: 'Some Other Window' if int(hwnd) == int(other_hwnd) else 'Tibia - Onniwabanshu', raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_visible', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_minimized', lambda hwnd: False, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_dpi_awareness_status', lambda: {}, raising=True)

    # Each test should behave like a fresh process.
    startup_guards._PROD_PROFILE_REAL_GUARDS_PASSED_ONCE = False
    startup_guards._PROD_PROFILE_REAL_HWND_SELF_HEAL_USED = False

    with pytest.raises(PreflightFailed) as ei:
        startup_guards.enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)
    assert str(ei.value) == 'window_not_foreground'

    fatal_path = tmp_path / 'diagnostics' / 'fatal.log'
    payload = json.loads(fatal_path.read_text(encoding='utf-8'))
    assert payload['reason'] == 'window_not_foreground'
    d = payload['details']
    assert d['reason'] == 'window_not_foreground'
    assert d['expected_foreground'] == 'TIBIA'
    assert d['window_hwnd'] == hex(int(tibia_hwnd))
    assert d['foreground_hwnd'] == hex(int(other_hwnd))


def test_obs_source_not_found_writes_required_fatal_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime import startup_guards
    from contracts.errors import PreflightFailed

    monkeypatch.chdir(tmp_path)
    _set_prod_emergency_obs_source_env(monkeypatch)
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', '')
    monkeypatch.setattr(startup_guards.sys, 'platform', 'win32', raising=False)

    tibia_hwnd = 0x222

    monkeypatch.setattr(startup_guards.w32, 'get_foreground_window', lambda: int(tibia_hwnd), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_exact', lambda _t: None, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'find_window_by_title_substring', lambda _t: type('W', (), {'hwnd': int(tibia_hwnd), 'title': 'Tibia - Onniwabanshu'})(), raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_window_text', lambda hwnd: 'Tibia - Onniwabanshu', raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_visible', lambda hwnd: True, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'is_window_minimized', lambda hwnd: False, raising=True)
    monkeypatch.setattr(startup_guards.w32, 'get_dpi_awareness_status', lambda: {}, raising=True)

    # Each test should behave like a fresh process.
    startup_guards._PROD_PROFILE_REAL_GUARDS_PASSED_ONCE = False
    startup_guards._PROD_PROFILE_REAL_HWND_SELF_HEAL_USED = False

    with pytest.raises(PreflightFailed) as ei:
        startup_guards.enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)
    assert str(ei.value) == 'obs_source_not_found'

    fatal_path = tmp_path / 'diagnostics' / 'fatal.log'
    payload = json.loads(fatal_path.read_text(encoding='utf-8'))
    assert payload['reason'] == 'obs_source_not_found'
    assert payload['details'] == {'reason': 'obs_source_not_found', 'obs_source_name': ''}
