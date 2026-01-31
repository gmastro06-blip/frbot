from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import traceback


def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def write_fatal(message: str, exc: Optional[BaseException] = None) -> None:
    """Always persist abort/crash evidence.

    Required path: diagnostics/fatal.log
    """
    Path('diagnostics').mkdir(parents=True, exist_ok=True)
    path = Path('diagnostics') / 'fatal.log'

    lines = [f'[{_ts()}] FATAL: {message}']
    if exc is not None:
        lines.append(f'Exception: {type(exc).__name__}: {exc}')

    if exc is not None:
        lines.append('Traceback:')
        lines.extend(traceback.format_exception(type(exc), exc, exc.__traceback__))

    with path.open('a', encoding='utf-8', errors='replace') as f:
        f.write('\n'.join(lines))
        f.write('\n')
