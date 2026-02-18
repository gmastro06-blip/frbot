from __future__ import annotations

import json
from pathlib import Path

import pytest

from healing_entrypoint import run_healing_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'hp_bar': {'x': 2, 'y': 2, 'width': 60, 'height': 6},
            'mp_bar': {'x': 2, 'y': 10, 'width': 60, 'height': 6},
            # numeric ROIs are 1-row (required by decoder)
            'hp_text': {'x': 2, 'y': 18, 'width': 4, 'height': 1},
            'mp_text': {'x': 2, 'y': 20, 'width': 4, 'height': 1},
            'heal_cooldown': {'x': 2, 'y': 22, 'width': 6, 'height': 3},
            'heal_feedback': {'x': 10, 'y': 22, 'width': 6, 'height': 3},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def test_healing_entrypoint_success_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')

    monkeypatch.setenv('FRBOT_HEALING_MAX_TICKS', '10')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_HEAL_KEY', 'F1')
    monkeypatch.setenv('FRBOT_HEAL_HP_THRESHOLD', '0.80')
    monkeypatch.setenv('FRBOT_HEAL_HP_INCREASE_MIN', '0.05')

    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '40')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_HEAL_BEHAVIOR', 'normal')

    assert run_healing_only() == 0


def test_healing_entrypoint_abort_hp_mp_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Missing HP/MP ROIs -> preflight abort.
    cfg = {'rois': {'heal_cooldown': {'x': 2, 'y': 2, 'width': 6, 'height': 3}}}
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')

    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', str(p))
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')

    assert run_healing_only() == 1


def test_healing_entrypoint_abort_unstable_heal_unverified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')

    monkeypatch.setenv('FRBOT_HEALING_MAX_TICKS', '10')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_HEAL_KEY', 'F1')
    monkeypatch.setenv('FRBOT_HEAL_HP_THRESHOLD', '0.90')
    monkeypatch.setenv('FRBOT_HEAL_HP_INCREASE_MIN', '0.05')
    monkeypatch.setenv('FRBOT_MAX_ATTEMPTS_PER_HEAL', '2')

    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '40')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')
    # Deterministic flags:
    # - No evidence after cast (behavior=no_effect, no cooldown visible).
    monkeypatch.setenv('MOCK_HEAL_EVIDENCE', 'none')
    monkeypatch.setenv('MOCK_HEAL_COOLDOWN', 'none')
    monkeypatch.setenv('FRBOT_MOCK_HEAL_BEHAVIOR', 'no_effect')  # Heal has no effect
    # Explicitly disable allow_no_evidence to ensure test fails when no evidence
    monkeypatch.setenv('FRBOT_HEAL_ALLOW_NO_EVIDENCE', '0')

    assert run_healing_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'heal_unverified' in fatal

    # Guardrail: attempts do not exceed max.
    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"attempts_used":2' in runtime_log


def test_healing_entrypoint_respects_max_ticks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')

    monkeypatch.setenv('FRBOT_HEALING_MAX_TICKS', '2')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_HEAL_KEY', 'F1')
    monkeypatch.setenv('FRBOT_HEAL_HP_THRESHOLD', '0.90')

    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '40')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')
    # Permanent cooldown: must abort without casting.
    monkeypatch.setenv('MOCK_HEAL_EVIDENCE', 'ok')
    monkeypatch.setenv('MOCK_HEAL_COOLDOWN', 'permanent')

    assert run_healing_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'heal_on_cooldown' in fatal
