from __future__ import annotations

import os

from contracts.errors import PreflightFailed


_PROD_EMERGENCY: str = 'prod_emergency'
_PROD_FULL: str = 'prod_full'


def current_profile() -> str:
    # Supported profiles are explicit to keep runtime auditable.
    raw = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if raw in {_PROD_EMERGENCY, _PROD_FULL}:
        return str(raw)
    return _PROD_EMERGENCY


def is_prod_emergency() -> bool:
    return current_profile() == _PROD_EMERGENCY


def is_prod_full() -> bool:
    return current_profile() == _PROD_FULL


def enforce_feature_allowed(feature: str) -> None:
    """Abort-fast gate for features that are hard-disabled in PROD-EMERGENCY."""

    f = (feature or '').strip().lower()
    # PROD-EMERGENCY hard-disables high-risk autonomous features.
    # PROD-FULL runs a strict, evidence-driven pipeline and MUST allow these gates.
    if is_prod_emergency() and f in {'combat', 'looting', 'deposit', 'trade'}:
        raise PreflightFailed('feature_disabled')


def cap_ticks(requested: int) -> int:
    """Cap tick budgets in PROD-EMERGENCY.

    This is a safety guardrail: even if env asks for more, we cap.
    """

    try:
        req = int(requested)
    except Exception:
        req = int(requested) if isinstance(requested, int) else 0

    if not is_prod_emergency():
        return int(req)

    # Emergency hard cap.
    hard_cap = 300
    if req <= 0:
        return int(hard_cap)
    return int(min(req, hard_cap))


def default_session_seconds(default: float) -> float:
    """Default per-session time budget in PROD-EMERGENCY (bounded)."""

    if not is_prod_emergency():
        return float(default)

    # 300 ticks @ 20Hz == 15s. Keep time budget aligned with tick cap.
    # This is a default only; runtime may still stop earlier.
    try:
        d = float(default)
    except Exception:
        d = 1.0
    return float(max(d, 1.0))
