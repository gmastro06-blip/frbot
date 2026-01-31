from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext


@dataclass(frozen=True, slots=True)
class LoadedConfig:
	rois: Dict[str, Roi]


def _default_mock_rois() -> Dict[str, Roi]:
	# Fits within a 16x16 mock frame.
	return {
		'minimap': Roi(name='minimap', x=8, y=2, width=8, height=8),
	}


def load_rois(ctx: RuntimeContext) -> LoadedConfig:
	mode = ctx.config.mode.strip().lower()

	# If a config_path is provided, load from there.
	path_raw = ctx.config.config_path.strip()
	if path_raw:
		path = Path(path_raw)
		if not path.exists():
			raise PreflightFailed(f'config_path does not exist: {path_raw!r}')
		try:
			data = json.loads(path.read_text(encoding='utf-8'))
		except Exception as exc:
			raise PreflightFailed(f'failed to read config_path: {type(exc).__name__}: {exc}') from exc

		rois_node = data.get('rois') if isinstance(data, dict) else None
		if not isinstance(rois_node, dict):
			raise PreflightFailed('config missing required object: rois')

		rois: Dict[str, Roi] = {}
		for name, roi_raw in rois_node.items():
			if not isinstance(name, str) or not isinstance(roi_raw, dict):
				continue
			try:
				rois[name] = Roi(
					name=name,
					x=int(roi_raw['x']),
					y=int(roi_raw['y']),
					width=int(roi_raw['width']),
					height=int(roi_raw['height']),
				)
			except Exception as exc:
				raise PreflightFailed(f'invalid roi {name!r}: {type(exc).__name__}: {exc}') from exc

		return LoadedConfig(rois=rois)

	# No config file: allow default ROIs in mock mode only.
	if mode == 'mock':
		return LoadedConfig(rois=_default_mock_rois())

	raise PreflightFailed('real mode requires FRBOT_CONFIG_PATH (or RuntimeConfig.config_path)')
