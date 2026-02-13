from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from diagnostics.schema import base_context_fields


def _ts() -> str:
	return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def log(logger: logging.Logger, *, event: str, gate: str, **fields: Any) -> None:
	"""Write a single JSONL event to runtime.log.

	Stable keys:
	- ts: ISO timestamp (local tz)
	- event: event name (e.g., tick, success)
	- gate: logical component/gate name
	Additional fields are per-event but should remain stable once introduced.
	"""
	payload: dict[str, Any] = {
		'ts': _ts(),
		**base_context_fields(),
		'event': str(event),
		'gate': str(gate),
	}
	for k, v in fields.items():
		payload[str(k)] = v
	logger.info(json.dumps(payload, separators=(',', ':'), sort_keys=True))
