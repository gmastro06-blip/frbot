from __future__ import annotations

import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from combat_entrypoint import run_combat_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # Target lock verification
            'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
            'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
            # Combat evidence/cooldown
            'target_hp_bar': {'x': 2, 'y': 95, 'width': 80, 'height': 6},
            'combat_cooldown': {'x': 2, 'y': 104, 'width': 6, 'height': 3},
            'combat_feedback': {'x': 10, 'y': 104, 'width': 6, 'height': 3},
            # Reuse healing HP/MP readers (semantic)
            'hp_bar': {'x': 2, 'y': 112, 'width': 60, 'height': 6},
            'mp_bar': {'x': 2, 'y': 120, 'width': 60, 'height': 6},
            'hp_text': {'x': 2, 'y': 128, 'width': 4, 'height': 1},
            'mp_text': {'x': 2, 'y': 130, 'width': 4, 'height': 1},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _base_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'combat')
    monkeypatch.setenv('FRBOT_COMBAT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_COMBAT', '1')

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_COMBAT_MAX_TICKS', '10')

    # Target starts locked in mock via preselected row 0.
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')

    # Attack key.
    monkeypatch.setenv('FRBOT_ATTACK_KEY', 'F2')

    # Guardrails.
    monkeypatch.setenv('FRBOT_MAX_ATTEMPTS_PER_TARGET', '2')
    monkeypatch.setenv('FRBOT_MAX_TIME_MS_PER_TARGET', '2500')

    # HP/MP mock values.
    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')

    # Target HP.
    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_CURRENT', '100')
    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_MAX', '100')


def test_combat_entrypoint_abort_no_evidence(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'false')
    monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'false')
    monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'false')
    monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'false')

    assert run_combat_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'combat_unverified_attack' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # No spam: unverified attack aborts immediately.
    assert 'inputs_sent=1' in runtime_log
    assert 'inputs_sent=2' not in runtime_log


def test_combat_entrypoint_abort_target_not_locked(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # This scenario no longer exists as a distinct gate requirement.
    # Keep a minimal sanity check that combat still aborts if target is not locked.
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')

    assert run_combat_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'combat_target_not_locked' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_combat_entrypoint_abort_permanent_cooldown(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'true')
    monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'true')
    monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'true')
    monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'true')

    assert run_combat_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'combat_on_cooldown' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert 'inputs_sent=1' not in runtime_log


def test_combat_entrypoint_success_damage_ok(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'true')
    monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'false')
    monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'false')
    monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'false')

    assert run_combat_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # Exactly 1 input on success.
    assert 'inputs_sent=1' in runtime_log
    assert 'inputs_sent=2' not in runtime_log


def test_combat_entrypoint_abort_active_cooldown_no_input(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # Cooldown visible and active, but preflight passes because cooldown is observable.
    monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'true')
    monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'true')
    monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'true')
    monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'true')

    assert run_combat_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'combat_on_cooldown' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert 'inputs_sent=1' not in runtime_log
