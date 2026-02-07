from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.config_loader import load_rois


def _ctx_with_config_path(*, path: str) -> RuntimeContext:
	cfg = RuntimeConfig(mode='mock', config_path=path)
	return RuntimeContext(
		config=cfg,
		status=RuntimeStatus(state=RuntimeState.INIT),
		telemetry=RuntimeTelemetry(),
	)


def test_load_rois_requires_top_level_rois_key(tmp_path: Path) -> None:
	# Missing the required top-level key must be a canonical failure.
	p = tmp_path / 'bad_rois.json'
	p.write_text(json.dumps({'minimap': {'x': 1, 'y': 2, 'width': 3, 'height': 4}}), encoding='utf-8')

	with pytest.raises(PreflightFailed) as ei:
		load_rois(_ctx_with_config_path(path=str(p)))
	assert str(ei.value) == 'config_invalid_schema'


def test_load_rois_rejects_invalid_roi_entry(tmp_path: Path) -> None:
	# ROI entries must be dicts with x/y/width/height.
	p = tmp_path / 'bad_roi.json'
	p.write_text(json.dumps({'rois': {'minimap': 'not-a-dict'}}), encoding='utf-8')

	with pytest.raises(PreflightFailed) as ei:
		load_rois(_ctx_with_config_path(path=str(p)))
	assert str(ei.value) == 'config_invalid_schema'


def test_prod_emergency_real_rois_rejects_unknown_superset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')

	p = tmp_path / 'rois.json'
	p.write_text(
		json.dumps(
			{
				'frame': {'width': 200, 'height': 150},
				'rois': {
					'minimap': {'x': 0, 'y': 0, 'width': 10, 'height': 10},
					'battle_list': {'x': 0, 'y': 10, 'width': 10, 'height': 10},
					'hp_mp': {'x': 0, 'y': 20, 'width': 10, 'height': 10},
					'target_frame': {'x': 0, 'y': 30, 'width': 10, 'height': 10},
					# Unknown extra ROI must be rejected.
					'not_allowed': {'x': 0, 'y': 40, 'width': 10, 'height': 10},
				},
			},
			indent=2,
		),
		encoding='utf-8',
	)

	cfg = RuntimeConfig(mode='real', config_path=str(p))
	ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

	with pytest.raises(PreflightFailed) as ei:
		load_rois(ctx)
	assert str(ei.value) == 'config_invalid_schema'


def test_prod_emergency_real_rois_allows_combat_basic_superset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')

	p = tmp_path / 'rois.json'
	p.write_text(
		json.dumps(
			{
				'frame': {'width': 200, 'height': 150},
				'rois': {
					'minimap': {'x': 0, 'y': 0, 'width': 10, 'height': 10},
					'battle_list': {'x': 0, 'y': 10, 'width': 10, 'height': 10},
					'hp_mp': {'x': 0, 'y': 20, 'width': 10, 'height': 10},
					'target_frame': {'x': 0, 'y': 30, 'width': 10, 'height': 10},
					'target_hp_bar': {'x': 0, 'y': 40, 'width': 10, 'height': 10},
					'combat_cooldown': {'x': 0, 'y': 50, 'width': 10, 'height': 10},
					'combat_feedback': {'x': 0, 'y': 60, 'width': 10, 'height': 10},
					'inventory_text': {'x': 0, 'y': 70, 'width': 10, 'height': 10},
				},
			},
			indent=2,
		),
		encoding='utf-8',
	)

	cfg = RuntimeConfig(mode='real', config_path=str(p))
	ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

	loaded = load_rois(ctx)
	assert 'combat_cooldown' in loaded.rois
	assert 'target_hp_bar' in loaded.rois
	assert 'inventory_text' in loaded.rois
