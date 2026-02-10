from __future__ import annotations

import hashlib
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


def _compute_roi_config_sha(*, rois: dict[str, dict[str, int]], frame: dict[str, int] | None) -> str:
	canonical: dict[str, object] = {'rois': rois}
	if frame is not None:
		canonical['frame'] = {'width': int(frame['width']), 'height': int(frame['height'])}
	blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
	return hashlib.sha256(blob).hexdigest()


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


def test_prod_full_real_rois_rejects_unknown_superset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_PROFILE', 'prod_full')

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


def test_prod_full_real_rois_allows_deposit_trade_superset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FRBOT_PROFILE', 'prod_full')

	p = tmp_path / 'rois.json'
	frame = {'width': 200, 'height': 150}
	rois: dict[str, dict[str, int]] = {
		'minimap': {'x': 0, 'y': 0, 'width': 10, 'height': 10},
		'battle_list': {'x': 0, 'y': 10, 'width': 10, 'height': 10},
		'hp_mp': {'x': 0, 'y': 20, 'width': 10, 'height': 10},
		'target_frame': {'x': 0, 'y': 30, 'width': 10, 'height': 10},
		# healing FULL
		'hp_bar': {'x': 0, 'y': 31, 'width': 10, 'height': 10},
		'mp_bar': {'x': 0, 'y': 32, 'width': 10, 'height': 10},
		'hp_text': {'x': 0, 'y': 33, 'width': 10, 'height': 10},
		'mp_text': {'x': 0, 'y': 34, 'width': 10, 'height': 10},
		'heal_cooldown': {'x': 0, 'y': 35, 'width': 10, 'height': 10},
		'heal_feedback': {'x': 0, 'y': 36, 'width': 10, 'height': 10},
		# looting semantic
		'inventory_text': {'x': 0, 'y': 40, 'width': 10, 'height': 10},
		'chat_loot_area': {'x': 0, 'y': 50, 'width': 10, 'height': 10},
		'loot_corpse': {'x': 0, 'y': 60, 'width': 10, 'height': 10},
		'loot_container_open': {'x': 0, 'y': 61, 'width': 10, 'height': 10},
		'loot_take': {'x': 0, 'y': 62, 'width': 10, 'height': 10},
		# deposit
		'depot_container': {'x': 0, 'y': 70, 'width': 10, 'height': 10},
		# trade
		'trade_inventory': {'x': 0, 'y': 80, 'width': 10, 'height': 10},
		'trade_npc': {'x': 0, 'y': 90, 'width': 10, 'height': 10},
		'trade_action': {'x': 0, 'y': 100, 'width': 10, 'height': 10},
	}
	computed_sha = _compute_roi_config_sha(rois=rois, frame=frame)
	p.write_text(
		json.dumps(
			{
				'frame': frame,
				'rois': rois,
				'certified_manifest': {'roi_config_sha': computed_sha},
			},
			indent=2,
		),
		encoding='utf-8',
	)

	cfg = RuntimeConfig(mode='real', config_path=str(p))
	ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

	loaded = load_rois(ctx)
	assert 'inventory_text' in loaded.rois
	assert 'depot_container' in loaded.rois
	assert 'trade_action' in loaded.rois
	assert 'heal_cooldown' in loaded.rois
	assert 'loot_take' in loaded.rois
