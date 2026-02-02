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
