from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


TradeIntentType = Literal['buy', 'sell', 'deposit']


@dataclass(frozen=True, slots=True)
class TradeIntent:
    intent_type: TradeIntentType


@dataclass(frozen=True, slots=True)
class TradeAbort:
    reason: str


def select_trade_intent(action: str) -> tuple[Optional[TradeIntent], Optional[TradeAbort]]:
    """Pure rule selection for Trade gate.

    No IO. No claims of success.
    """

    a = (action or '').strip().lower()
    if a == 'buy':
        return TradeIntent(intent_type='buy'), None
    if a == 'sell':
        return TradeIntent(intent_type='sell'), None
    if a == 'deposit':
        return TradeIntent(intent_type='deposit'), None

    return None, TradeAbort(reason='trade_unverified_action')
