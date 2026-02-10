from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional
import traceback

from diagnostics.schema import base_context_fields


def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def write_fatal(message: str, exc: Optional[BaseException] = None, *, details: Optional[dict] = None) -> None:
    """Always persist abort/crash evidence.

    Format contract:
    - diagnostics/fatal.log is a single JSON object (UTF-8)
    - Includes traceback information when an exception is available
    """
    Path('diagnostics').mkdir(parents=True, exist_ok=True)
    path = Path('diagnostics') / 'fatal.log'

    tb_lines: list[str] = []
    exc_type: str | None = None
    exc_message: str | None = None
    if exc is not None:
        exc_type = type(exc).__name__
        exc_message = str(exc)
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)

        # Allow exceptions to carry structured details.
        if details is None:
            maybe = getattr(exc, 'details', None)
            if isinstance(maybe, dict):
                details = maybe

    payload: dict[str, Any] = {
        'ts': _ts(),
        **base_context_fields(),
        'level': 'FATAL',
        'reason': str(message),
        'message': str(message),
        'exc_type': exc_type,
        'exc_message': exc_message,
        'traceback': tb_lines,
    }

    if isinstance(details, dict) and details:
        payload['details'] = details

    # Overwrite to keep fatal.log valid JSON (not JSONL).
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
