from __future__ import annotations

import os

from contracts.errors import PreflightFailed


def parse_window_hwnd_env(name: str = 'FRBOT_WINDOW_HWND') -> int:
	"""Parse HWND env var deterministically.

	- Supports decimal ("123") and hex ("0x7B") via int(..., 0)
	- If unset/empty -> 0
	- If set but invalid -> abort with canonical reason
	"""
	raw = os.environ.get(name)
	if raw is None:
		return 0
	s = str(raw).strip()
	if s == '':
		return 0
	# Common placeholder used in docs/scripts. Treat as unset so unit tests and
	# mock-mode runs aren't affected by a developer's shell environment.
	if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
		return 0
	# Another common placeholder in instructions.
	if s.strip().lower() in {'0xyourhwnd', 'yourhwnd', '0x<yourhwnd>'}:
		return 0
	try:
		value = int(s, 0)
	except Exception as exc:
		raise PreflightFailed('window_hwnd_invalid') from exc
	if value < 0:
		raise PreflightFailed('window_hwnd_invalid')
	return int(value)
