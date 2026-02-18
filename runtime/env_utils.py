"""Common environment utilities for all entrypoints.

Eliminates duplicated _env_str, _env_int, _env_bool, _env_float across entrypoints.
"""

from __future__ import annotations

import os
from typing import TypeVar, Callable

from contracts.errors import PreflightFailed

T = TypeVar('T')


def env_str(name: str, default: str = '') -> str:
    """Parse string env var with fallback."""
    raw = os.environ.get(name)
    return default if raw is None else raw


def env_int(name: str, default: int = 0) -> int:
    """Parse int env var with fallback.

    Special handling for FRBOT_WINDOW_HWND to support hex formats.
    """
    raw = os.environ.get(name)

    if name == 'FRBOT_WINDOW_HWND' and raw is not None and str(raw).strip() != '':
        s = str(raw).strip()
        # Handle placeholders like 0x, 0xyourhwnd
        if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
            return int(default)
        if s.lower() in {'0xyourhwnd', 'yourhwnd', '0x<yourhwnd>'}:
            return int(default)
        try:
            return int(s, 0)
        except Exception as exc:
            raise PreflightFailed('window_hwnd_invalid') from exc

    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def env_float(name: str, default: float = 0.0) -> float:
    """Parse float env var with fallback."""
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean env var with fallback.

    Truthy: '1', 'true', 'yes', 'on'
    Falsy: '0', 'false', 'no', 'off', ''
    """
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip() not in {'0', 'false', 'no', 'off'}


def env_choice(name: str, choices: set[str], default: str) -> str:
    """Parse env var that must be one of a set of choices."""
    raw = env_str(name, default).strip().lower()
    if raw not in choices:
        return default
    return raw


def parse_or_default(
    name: str,
    parser: Callable[[str], T],
    default: T
) -> T:
    """Parse env var with custom parser, falling back to default on error."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return parser(raw)
    except Exception:
        return default
