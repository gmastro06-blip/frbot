from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True, slots=True)
class LootIntent:
    """Single looting action.

    Invariant: one intent => one input => one AFTER evidence check.
    """

    kind: Literal['press_key', 'click']

    # For press_key intents.
    key: Optional[str] = None

    # For click intents.
    click_x: Optional[int] = None
    click_y: Optional[int] = None

    mode: Literal['premium', 'free'] = 'premium'
    expected_evidence: Literal['inventory_delta'] = 'inventory_delta'
