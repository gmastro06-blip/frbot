from __future__ import annotations

import json
from pathlib import Path

import pytest

from combat_entrypoint import run_combat_only
from targeting_entrypoint import run_targeting_only


def _write_rois(tmp_path: Path, rois: dict[str, dict[str, int]]) -> str:
	path = tmp_path / 'rois.json'
	path.write_text(json.dumps({'rois': rois}), encoding='utf-8')
	return str(path)


def test_runtime_log_is_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)

	# Minimal combat success path (writes runtime.log).
	rois = {
		'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
		'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
		'target_hp_bar': {'x': 2, 'y': 95, 'width': 80, 'height': 6},
		'combat_cooldown': {'x': 2, 'y': 104, 'width': 6, 'height': 3},
		'combat_feedback': {'x': 10, 'y': 104, 'width': 6, 'height': 3},
		'hp_bar': {'x': 2, 'y': 112, 'width': 60, 'height': 6},
		'mp_bar': {'x': 2, 'y': 120, 'width': 60, 'height': 6},
		'hp_text': {'x': 2, 'y': 128, 'width': 4, 'height': 1},
		'mp_text': {'x': 2, 'y': 130, 'width': 4, 'height': 1},
	}

	monkeypatch.setenv('FRBOT_MODE', 'combat')
	monkeypatch.setenv('FRBOT_COMBAT_BACKEND', 'mock')
	monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, rois))
	monkeypatch.setenv('FRBOT_ENABLE_COMBAT', '1')
	monkeypatch.setenv('FRBOT_TICK_HZ', '100')
	monkeypatch.setenv('FRBOT_COMBAT_MAX_TICKS', '2')
	monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
	monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')
	monkeypatch.setenv('FRBOT_ATTACK_KEY', 'F2')
	monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '80')
	monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
	monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
	monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')
	monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_CURRENT', '100')
	monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_MAX', '100')
	monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'true')
	monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'false')
	monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'false')
	monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'false')

	assert run_combat_only() == 0

	runtime_path = tmp_path / 'diagnostics' / 'runtime.log'
	assert runtime_path.exists()
	lines = runtime_path.read_text(encoding='utf-8', errors='replace').splitlines()
	assert lines, 'runtime.log must not be empty'
	for line in lines:
		evt = json.loads(line)
		assert isinstance(evt, dict)
		assert evt.get('gate')
		assert evt.get('event')
		assert evt.get('ts')


def test_fatal_log_is_structured_json_on_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)

	# Force a preflight abort (missing required ROIs) to create fatal.log.
	monkeypatch.setenv('FRBOT_MODE', 'targeting')
	monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
	monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
	monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '1')
	monkeypatch.setenv('FRBOT_TICK_HZ', '100')
	monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
	monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

	assert run_targeting_only() == 1

	fatal_path = tmp_path / 'diagnostics' / 'fatal.log'
	assert fatal_path.exists()
	payload = json.loads(fatal_path.read_text(encoding='utf-8', errors='replace'))
	assert payload.get('level') == 'FATAL'
	assert payload.get('ts')
	assert payload.get('reason')
	# If an exception was recorded, a traceback must be present.
	if payload.get('exc_type') is not None:
		assert isinstance(payload.get('traceback'), list)
		assert payload.get('traceback'), 'traceback must be present when exc_type is set'
