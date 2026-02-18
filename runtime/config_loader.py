from __future__ import annotations

import hashlib
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
	computed_sha: str = ''


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
	# healing (FULL)
	'hp_bar',
	'mp_bar',	
	'hp_text',
	'mp_text',
	'heal_cooldown',
	'heal_feedback',
	# looting_full/looting_basic semantic evidence
	'inventory_text',
	'chat_loot_area',
	'loot_corpse',
	'loot_container_open',
	'loot_take',
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

	def _compute_roi_config_sha(*, rois_for_sha: dict[str, dict[str, int]], frame_w: int | None, frame_h: int | None) -> str:
		canonical: dict[str, object] = {
			'rois': rois_for_sha,
		}
		if frame_w is not None and frame_h is not None:
			canonical['frame'] = {'width': int(frame_w), 'height': int(frame_h)}
		blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
		return hashlib.sha256(blob).hexdigest()

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
		if keys in ({'rois'}, {'rois', 'certified_manifest'}):
			pass
		elif keys in ({'rois', 'frame'}, {'rois', 'frame', 'certified_manifest'}):
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
		rois_for_sha: dict[str, dict[str, int]] = {}
		for name, roi_raw in rois_node.items():
			if not isinstance(name, str) or not isinstance(roi_raw, dict):
				raise PreflightFailed('config_invalid_schema')
			try:
				x = int(roi_raw['x'])
				y = int(roi_raw['y'])
				w = int(roi_raw['width'])
				h = int(roi_raw['height'])
				rois[name] = Roi(
					name=name,
					x=x,
					y=y,
					width=w,
					height=h,
				)
				rois_for_sha[name] = {'x': x, 'y': y, 'width': w, 'height': h}
			except Exception as exc:
				raise PreflightFailed('config_invalid_schema') from exc

		computed_sha = _compute_roi_config_sha(rois_for_sha=rois_for_sha, frame_w=frame_w, frame_h=frame_h)
		# Best-effort env exposure for logs/manifests.
		try:
			os.environ['FRBOT_ROI_CONFIG_SHA'] = str(computed_sha)
		except Exception:
			pass

		# REAL-mode certification requires a minimal, fixed ROI inventory.
		if mode == 'real':
			for required_roi in _REQUIRED_REAL_ROIS:
				if required_roi not in rois:
					raise PreflightFailed('config_invalid_schema')
			# PROD profiles: allow only an explicit allowlisted superset (audited).
			if profile in {'prod_emergency', 'prod_full', 'prod_real'}:
				keys = set(rois.keys())
				required_keys = set(_REQUIRED_REAL_ROIS)
				if keys != required_keys:
					extra = keys - required_keys
					# prod_real: allow all extra ROIs (full automation)
					allowed_extra = set(_ALLOWED_PROD_FULL_EXTRA_ROIS)
					if profile == 'prod_emergency':
						allowed_extra = set(_ALLOWED_PROD_EMERGENCY_EXTRA_ROIS)
					if not extra.issubset(set(allowed_extra)):
						raise PreflightFailed('config_invalid_schema')

			# PROD-FULL REAL requires a certified manifest with a matching SHA.
			if profile == 'prod_full':
				cert = data.get('certified_manifest')
				roi_sha = None
				if isinstance(cert, dict):
					roi_sha = cert.get('roi_config_sha')
				if not isinstance(roi_sha, str) or roi_sha.strip() != computed_sha:
					raise PreflightFailed('roi_config_not_certified')

		return LoadedConfig(rois=rois, frame_width=frame_w, frame_height=frame_h, computed_sha=str(computed_sha))

	# No config file: allow default ROIs in mock mode only.
	if mode == 'mock':
		return LoadedConfig(rois=_default_mock_rois())

	raise PreflightFailed('config_invalid_schema')
