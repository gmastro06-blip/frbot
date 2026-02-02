from __future__ import annotations

import os
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from runtime.env import parse_window_hwnd_env
from targeting_entrypoint import run_targeting_only


def test_parse_window_hwnd_env_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_WINDOW_HWND', '123')
	assert parse_window_hwnd_env('FRBOT_WINDOW_HWND') == 123


def test_parse_window_hwnd_env_hex(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_WINDOW_HWND', '0x10')
	assert parse_window_hwnd_env('FRBOT_WINDOW_HWND') == 16


def test_parse_window_hwnd_env_placeholder_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_WINDOW_HWND', '0xYOURHWND')
	assert parse_window_hwnd_env('FRBOT_WINDOW_HWND') == 0


def test_invalid_hwnd_aborts_canonically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	# End-to-end: invalid hwnd should be a deterministic abort reason (not silent 0, not crash).
	monkeypatch.chdir(tmp_path)

	monkeypatch.setenv('FRBOT_MODE', 'targeting')
	monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
	monkeypatch.setenv('FRBOT_CONFIG_PATH', str(tmp_path / 'rois.json'))
	(tmp_path / 'rois.json').write_text(
		'{"rois":{"battle_list":{"x":2,"y":2,"width":10,"height":10},"target_frame":{"x":2,"y":20,"width":10,"height":10}}}',
		encoding='utf-8',
	)
	monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
	monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '1')
	monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Rat:1:1')

	monkeypatch.setenv('FRBOT_WINDOW_HWND', '0xZZ')

	assert run_targeting_only() == 1
	fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
	assert 'window_hwnd_invalid' in fatal
