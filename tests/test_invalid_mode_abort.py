from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.runner import run
from targeting_entrypoint import run_targeting_only


def test_invalid_mode_aborts_canonically_and_no_runtime_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)
	monkeypatch.setenv('FRBOT_MODE', 'nope')
	monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

	assert run() == 1
	assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
	fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
	assert 'invalid_mode' in fatal


def test_invalid_gate_backend_aborts_canonically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)

	# targeting entrypoint uses FRBOT_TARGETING_BACKEND as the RuntimeConfig.mode
	monkeypatch.setenv('FRBOT_MODE', 'targeting')
	monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'nope')

	# Provide a valid ROIs file to ensure we fail due to backend mode only.
	p = tmp_path / 'rois.json'
	p.write_text(
		json.dumps(
			{
				'rois': {
					'battle_list': {'x': 2, 'y': 2, 'width': 10, 'height': 10},
					'target_frame': {'x': 2, 'y': 20, 'width': 10, 'height': 10},
				}
			}
		),
		encoding='utf-8',
	)
	monkeypatch.setenv('FRBOT_CONFIG_PATH', str(p))

	assert run_targeting_only() == 1
	assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
	fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
	assert 'invalid_mode' in fatal
