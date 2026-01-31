from __future__ import annotations

import json
from pathlib import Path

import pytest

from targeting_entrypoint import run_targeting_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
            'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def test_targeting_entrypoint_success_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'targeting')
    monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
    monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '10')

    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'normal')

    assert run_targeting_only() == 0


def test_targeting_entrypoint_abort_no_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'targeting')
    monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
    monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '5')

    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', '')

    assert run_targeting_only() == 1


def test_targeting_entrypoint_abort_unstable_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'targeting')
    monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
    monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '10')

    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'no_highlight')

    assert run_targeting_only() == 1


def test_targeting_entrypoint_respects_max_ticks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'targeting')
    monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
    monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '2')

    # Valid battle list exists, but click never produces evidence.
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'no_highlight')

    assert run_targeting_only() == 1
