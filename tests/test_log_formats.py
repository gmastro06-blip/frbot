from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.runner import run
from targeting_entrypoint import run_targeting_only


def _write_rois(tmp_path: Path, rois: dict[str, dict[str, int]]) -> str:
	path = tmp_path / 'rois.json'
	path.write_text(json.dumps({'rois': rois}), encoding='utf-8')
	return str(path)


def test_runtime_log_is_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)

	# Minimal mock runtime success path (writes runtime.log).
	rois = {
		'minimap': {'x': 2, 'y': 2, 'width': 80, 'height': 80},
	}

	monkeypatch.setenv('FRBOT_MODE', 'mock')
	monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, rois))
	monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
	monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')	
	monkeypatch.setenv('FRBOT_SESSION_SECONDS', '0.05')
	monkeypatch.setenv('FRBOT_TICK_HZ', '100')

	assert run() == 0

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
