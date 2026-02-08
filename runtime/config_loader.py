from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext


@dataclass(frozen=True, slots=True)
class LoadedConfig:
	rois: Dict[str, Roi]
	frame_width: int | None = None
	frame_height: int | None = None


_REQUIRED_REAL_ROIS: tuple[str, ...] = (
	'minimap',
	'battle_list',
	'hp_mp',
	'target_frame',
)


_ALLOWED_PROD_EMERGENCY_EXTRA_ROIS: set[str] = {
	# combat_basic: additional ROIs allowed for semantic combat evidence.
	'target_hp_bar',
	'combat_cooldown',
	'combat_feedback',
	# looting_basic: semantic inventory snapshot.
	'inventory_text',
	# looting_basic: secondary semantic loot evidence (pixel-hash), no OCR.
	'chat_loot_area',
	# looting_basic: deterministic click target for the loot gesture.
	'loot_corpse',
	# deposit_basic: depot container semantic evidence.
	'depot_container',
	# trade_basic: strong NPC identity + trade delta evidence.
	'trade_inventory',
	'trade_npc',
	'trade_action',
}


_ALLOWED_PROD_FULL_EXTRA_ROIS: set[str] = {
	# combat_basic evidence
	'target_hp_bar',
	'combat_cooldown',
	'combat_feedback',
	# looting_full/looting_basic semantic evidence
	'inventory_text',
	'chat_loot_area',
	'loot_corpse',
	# deposit/trade semantic evidence
	'depot_container',
	'trade_inventory',
	'trade_npc',
	'trade_action',
}


def _env_bool(name: str, default: bool = False) -> bool:
	raw = os.environ.get(name)
	if raw is None:
		return bool(default)
	return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _default_mock_rois() -> Dict[str, Roi]:
	# Fits within a 16x16 mock frame.
	return {
		'minimap': Roi(name='minimap', x=8, y=2, width=8, height=8),
	}


def load_rois(ctx: RuntimeContext) -> LoadedConfig:
	mode = ctx.config.mode.strip().lower()
	profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()

	# If a config_path is provided, load from there.
	path_raw = ctx.config.config_path.strip()
	if path_raw:
		path = Path(path_raw)
		if not path.exists():
			raise PreflightFailed('config_invalid_schema')
		try:
			data = json.loads(path.read_text(encoding='utf-8'))
		except Exception as exc:
			raise PreflightFailed('config_invalid_schema') from exc

		# Canonical schema (single allowed):
		# {"rois": {"name": {"x": int, "y": int, "width": int, "height": int}, ...}}
		# Optional (OBS-source capture resolution contract):
		# {"frame": {"width": int, "height": int}, "rois": {...}}
		if not isinstance(data, dict):
			raise PreflightFailed('config_invalid_schema')

		frame_w: int | None = None
		frame_h: int | None = None

		keys = set(data.keys())
		if keys == {'rois'}:
			pass
		elif keys == {'rois', 'frame'}:
			frame = data.get('frame')
			if not isinstance(frame, dict):
				raise PreflightFailed('config_invalid_schema')
			try:
				frame_w = int(frame['width'])
				frame_h = int(frame['height'])
			except Exception as exc:
				raise PreflightFailed('config_invalid_schema') from exc
			if frame_w <= 0 or frame_h <= 0:
				raise PreflightFailed('config_invalid_schema')
		else:
			# Eliminate ambiguous configs.
			raise PreflightFailed('config_invalid_schema')

		rois_node = data.get('rois')
		if not isinstance(rois_node, dict):
			raise PreflightFailed('config_invalid_schema')

		rois: Dict[str, Roi] = {}
		for name, roi_raw in rois_node.items():
			if not isinstance(name, str) or not isinstance(roi_raw, dict):
				raise PreflightFailed('config_invalid_schema')
			try:
				rois[name] = Roi(
					name=name,
					x=int(roi_raw['x']),
					y=int(roi_raw['y']),
					width=int(roi_raw['width']),
					height=int(roi_raw['height']),
				)
			except Exception as exc:
				raise PreflightFailed('config_invalid_schema') from exc

		# REAL-mode certification requires a minimal, fixed ROI inventory.
		if mode == 'real':
			for required_roi in _REQUIRED_REAL_ROIS:
				if required_roi not in rois:
					raise PreflightFailed('config_invalid_schema')
			# PROD profiles: allow only an explicit allowlisted superset (audited).
			if profile in {'prod_emergency', 'prod_full'}:
				keys = set(rois.keys())
				required_keys = set(_REQUIRED_REAL_ROIS)
				if keys != required_keys:
					extra = keys - required_keys
					allowed_extra = set(_ALLOWED_PROD_EMERGENCY_EXTRA_ROIS) if profile == 'prod_emergency' else set(_ALLOWED_PROD_FULL_EXTRA_ROIS)
					if not extra.issubset(set(allowed_extra)):
						raise PreflightFailed('config_invalid_schema')

		return LoadedConfig(rois=rois, frame_width=frame_w, frame_height=frame_h)

	# No config file: allow default ROIs in mock mode only.
	if mode == 'mock':
		return LoadedConfig(rois=_default_mock_rois())

	raise PreflightFailed('config_invalid_schema')
