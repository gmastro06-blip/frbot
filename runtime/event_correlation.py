from __future__ import annotations

import time
import uuid
from typing import Any

from contracts.window import WindowBindingStatus
from diagnostics.schema import base_context_fields


def new_event(*, gate: str, intent: dict[str, Any]) -> dict[str, Any]:
	# NOTE: Keep payload JSON-serializable.
	return {
		**base_context_fields(),
		'tick_id': str(uuid.uuid4()),
		'gate': str(gate),
		'intent': dict(intent),
		'ts_created_ns': int(time.monotonic_ns()),
	}


def status_to_dict(s: WindowBindingStatus) -> dict[str, Any]:
	return {
		'backend': str(s.backend),
		'verified': bool(s.verified),
		'hwnd': int(s.hwnd),
		'rect': {
			'left': int(s.rect.left),
			'top': int(s.rect.top),
			'right': int(s.rect.right),
			'bottom': int(s.rect.bottom),
		},
	}


def attach_snapshot(event: dict[str, Any], *, stage: str, ts_ns: int, status: WindowBindingStatus) -> None:
	key = str(stage).strip().lower()
	event[f'{key}_ts_ns'] = int(ts_ns)
	event[f'binding_{key}'] = status_to_dict(status)


def validate(event: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
	"""Validate basic capture<->input correlation.

	Contract:
	- monotonic timestamps: before <= input <= after
	- binding hwnd+rect stable across before/input/after
	"""
	try:
		t_before = int(event.get('before_ts_ns') or 0)
		t_input = int(event.get('input_ts_ns') or 0)
		t_after = int(event.get('after_ts_ns') or 0)
		if t_before <= 0 or t_input <= 0 or t_after <= 0:
			return False, 'missing_timestamps', {'before_ts_ns': t_before, 'input_ts_ns': t_input, 'after_ts_ns': t_after}
		if not (t_before <= t_input <= t_after):
			return False, 'timestamp_order_invalid', {'before_ts_ns': t_before, 'input_ts_ns': t_input, 'after_ts_ns': t_after}
	except Exception as exc:
		return False, 'timestamp_parse_failed', {'exc': type(exc).__name__}

	bs = event.get('binding_before')
	is_ = event.get('binding_input')
	as_ = event.get('binding_after')
	if not isinstance(bs, dict) or not isinstance(is_, dict) or not isinstance(as_, dict):
		return False, 'missing_binding_snapshots', {}

	def _hwnd_rect(x: dict[str, Any]) -> tuple[int, tuple[int, int, int, int]]:
		rect = x.get('rect')
		if not isinstance(rect, dict):
			rect = {}
		return (
			int(x.get('hwnd') or 0),
			(
				int(rect.get('left') or 0),
				int(rect.get('top') or 0),
				int(rect.get('right') or 0),
				int(rect.get('bottom') or 0),
			),
		)

	b_hwnd, b_rect = _hwnd_rect(bs)
	i_hwnd, i_rect = _hwnd_rect(is_)
	a_hwnd, a_rect = _hwnd_rect(as_)

	if not (b_hwnd and i_hwnd and a_hwnd):
		return False, 'hwnd_missing', {'before': b_hwnd, 'input': i_hwnd, 'after': a_hwnd}
	if not (b_hwnd == i_hwnd == a_hwnd):
		return False, 'hwnd_changed', {'before': b_hwnd, 'input': i_hwnd, 'after': a_hwnd}
	if not (b_rect == i_rect == a_rect):
		return False, 'rect_changed', {'before': b_rect, 'input': i_rect, 'after': a_rect}

	return True, 'ok', {}
