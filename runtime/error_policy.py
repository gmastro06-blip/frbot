from __future__ import annotations

import os


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def should_reraise() -> bool:
    """Return True when broad exception handlers should re-raise the caught exception.

    Controlled by env var `FRBOT_STRICT_ERRORS`. When set to '1' we enable strict
    error propagation for REAL-style runs (non-mock). This helper is intentionally
    conservative: it avoids re-raising in `mock` mode to keep tests deterministic.
    """
    try:
        if _env_str('FRBOT_STRICT_ERRORS', '') != '1':
            return False
        mode = (os.environ.get('FRBOT_MODE', '') or '').strip().lower()
        # If explicitly running in mock mode, do not reraise.
        if mode == 'mock':
            return False
        return True
    except Exception:
        return False
