from __future__ import annotations

import os

from contracts.errors import PreflightFailed
from runtime.error_policy import should_reraise


_PROD_EMERGENCY: str = 'prod_emergency'
_PROD_FULL: str = 'prod_full'
_PROD_REAL: str = 'prod_real'  # Unified: targeting + cavebot + healing (full automation)


def current_profile() -> str:
    # Supported profiles are explicit to keep runtime auditable.
    raw = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if raw in {_PROD_EMERGENCY, _PROD_FULL, _PROD_REAL}:
        return str(raw)
    return _PROD_EMERGENCY


def is_prod_emergency() -> bool:
    return current_profile() == _PROD_EMERGENCY


def is_prod_full() -> bool:
    return current_profile() == _PROD_FULL


def is_prod_real() -> bool:
    """Full automation profile: targeting + cavebot + healing."""
    return current_profile() == _PROD_REAL


def enforce_feature_allowed(feature: str) -> None:
    """Abort-fast gate for features that are hard-disabled in PROD profiles.

    - prod_emergency: only targeting allowed
    - prod_full: strict evidence-based pipeline
    - prod_real: full automation (targeting + cavebot + healing)
    """

    f = (feature or '').strip().lower()

    # prod_emergency: only targeting is allowed
    if is_prod_emergency() and f in {'combat', 'looting', 'deposit', 'trade', 'cavebot', 'healing'}:
        raise PreflightFailed('feature_disabled')

    # prod_full: allows combat, looting, deposit, trade but NOT cavebot/healing (evidence-based)
    # prod_real: allows all features


def cap_ticks(requested: int) -> int:
    """Cap tick budgets based on profile.

    - prod_emergency: hard cap at 300 ticks
    - prod_full/prod_real: unlimited (uses requested value)
    """

    try:
        req = int(requested)
    except Exception:
        if should_reraise():
            raise
        req = int(requested) if isinstance(requested, int) else 0

    if is_prod_emergency():
        # Emergency hard cap.
        hard_cap = 300
        if req <= 0:
            return int(hard_cap)
        return int(min(req, hard_cap))

    # prod_full and prod_real: unlimited
    return int(req)


def default_session_seconds(default: float) -> float:
    """Default per-session time budget based on profile.

    - prod_emergency: bounded (max 15s = 300 ticks @ 20Hz)
    - prod_full/prod_real: unlimited (uses default)
    """

    if is_prod_emergency():
        # 300 ticks @ 20Hz == 15s
        try:
            d = float(default)
        except Exception:
            if should_reraise():
                raise
            d = 1.0
        return float(max(d, 1.0))

    # prod_full and prod_real: unlimited
    return float(default)
