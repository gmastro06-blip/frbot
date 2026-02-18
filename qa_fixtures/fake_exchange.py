"""Fake Exchange and Ledger for QA Testing.

Provides deterministic mock implementations of exchange/ledger operations
for testing deposit and trade flows without real API dependencies.
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Literal


# Transaction status
TxStatus = Literal['pending', 'confirmed', 'failed']


@dataclass
class Transaction:
    """Represents a deposit/withdrawal transaction."""
    txid: str
    amount: float
    currency: str
    status: TxStatus = 'pending'
    confirmations: int = 0
    timestamp: float = field(default_factory=time.time)
    retries: int = 0


@dataclass
class TradeOrder:
    """Represents a trade order."""
    order_id: str
    side: Literal['buy', 'sell']
    symbol: str
    amount: float
    price: float | None  # None for market orders
    filled_amount: float = 0.0
    status: Literal['open', 'partial', 'filled', 'cancelled', 'rejected'] = 'open'
    fees: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Balance:
    """Account balance for a currency."""
    currency: str
    available: float
    locked: float = 0.0

    @property
    def total(self) -> float:
        return self.available + self.locked


class FakeLedger:
    """Deterministic fake ledger for testing deposit flows.

    Features:
    - Idempotent deposits by txid
    - Confirmation tracking
    - Balance tracking
    """

    def __init__(self, required_confirmations: int = 3):
        self.required_confirmations = required_confirmations
        self.transactions: dict[str, Transaction] = {}
        self.balances: dict[str, Balance] = {}
        self._tx_history: list[dict] = []

    def get_balance(self, currency: str) -> Balance:
        """Get balance for currency."""
        if currency not in self.balances:
            self.balances[currency] = Balance(currency=currency, available=1000.0)
        return self.balances[currency]

    def deposit(self, txid: str, amount: float, currency: str = 'USD') -> dict:
        """Process deposit - idempotent by txid."""
        if txid in self.transactions:
            # Idempotent: return existing transaction
            tx = self.transactions[txid]
            return {
                'txid': tx.txid,
                'amount': tx.amount,
                'currency': tx.currency,
                'status': tx.status,
                'confirmations': tx.confirmations,
                'idempotent': True,
            }

        # Create new transaction
        tx = Transaction(
            txid=txid,
            amount=amount,
            currency=currency,
            status='pending',
            confirmations=0,
        )
        self.transactions[txid] = tx
        self._tx_history.append({
            'action': 'deposit',
            'txid': txid,
            'amount': amount,
            'currency': currency,
            'timestamp': time.time(),
        })

        return {
            'txid': tx.txid,
            'amount': tx.amount,
            'currency': tx.currency,
            'status': tx.status,
            'confirmations': tx.confirmations,
            'idempotent': False,
        }

    def confirm(self, txid_transaction: str, confirmations: int | None = None) -> bool:
        """Confirm a transaction (simulates blockchain confirmations)."""
        if txid not in self.transactions:
            return False

        tx = self.transactions[txid]
        if confirmations is not None:
            tx.confirmations = confirmations
        else:
            tx.confirmations += 1

        if tx.confirmations >= self.required_confirmations and tx.status == 'pending':
            tx.status = 'confirmed'
            # Update balance
            if tx.currency not in self.balances:
                self.balances[tx.currency] = Balance(currency=tx.currency, available=0)
            self.balances[tx.currency].available += tx.amount
            return True
        return False

    def get_transaction(self, txid: str) -> Transaction | None:
        """Get transaction by txid."""
        return self.transactions.get(txid)

    def get_history(self) -> list[dict]:
        """Get transaction history."""
        return self._tx_history.copy()


class FakeExchange:
    """Deterministic fake exchange for testing trade flows.

    Features:
    - Limit and market orders
    - Partial fills
    - Cancellation
    - Fee calculation
    - Insufficient funds handling
    """

    def __init__(self, maker_fee: float = 0.001, taker_fee: float = 0.002):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.orders: dict[str, TradeOrder] = {}
        self.balances: dict[str, Balance] = {}
        self.order_history: list[dict] = []
        self._next_order_id = 1

        # Initialize with some balance
        for c in ['BTC', 'ETH', 'USD']:
            self.balances[c] = Balance(currency=c, available=10000.0 if c != 'BTC' else 10.0)

    def get_balance(self, currency: str) -> Balance:
        """Get balance for currency."""
        if currency not in self.balances:
            self.balances[currency] = Balance(currency=currency, available=0.0)
        return self.balances[currency]

    def create_order(
        self,
        side: Literal['buy', 'sell'],
        symbol: str,
        amount: float,
        price: float | None = None,
    ) -> dict:
        """Create a trade order."""
        order_id = f"ORDER-{self._next_order_id:06d}"
        self._next_order_id += 1

        base, quote = symbol.split('/')
        order_side = 'buy' if side == 'buy' else 'sell'

        # Check sufficient funds
        if side == 'buy':
            required = amount * (price or 1000)  # Use price or default for market
            if self.balances[quote].available < required:
                order = TradeOrder(
                    order_id=order_id,
                    side=side,
                    symbol=symbol,
                    amount=amount,
                    price=price,
                    status='rejected',
                    fees=0,
                )
                self.orders[order_id] = order
                self.order_history.append({
                    'action': 'create_order',
                    'order_id': order_id,
                    'side': side,
                    'symbol': symbol,
                    'amount': amount,
                    'price': price,
                    'status': 'rejected',
                    'reason': 'insufficient_funds',
                    'timestamp': time.time(),
                })
                return {
                    'order_id': order_id,
                    'status': 'rejected',
                    'reason': 'insufficient_funds',
                }
        else:  # sell
            if self.balances[base].available < amount:
                order = TradeOrder(
                    order_id=order_id,
                    side=side,
                    symbol=symbol,
                    amount=amount,
                    price=price,
                    status='rejected',
                    fees=0,
                )
                self.orders[order_id] = order
                self.order_history.append({
                    'action': 'create_order',
                    'order_id': order_id,
                    'side': side,
                    'symbol': symbol,
                    'amount': amount,
                    'price': price,
                    'status': 'rejected',
                    'reason': 'insufficient_funds',
                    'timestamp': time.time(),
                })
                return {
                    'order_id': order_id,
                    'status': 'rejected',
                    'reason': 'insufficient_funds',
                }

        # Create order
        order = TradeOrder(
            order_id=order_id,
            side=side,
            symbol=symbol,
            amount=amount,
            price=price,
            status='open',
        )
        self.orders[order_id] = order

        # Reserve funds
        if side == 'buy':
            required = amount * (price or 1000)
            self.balances[quote].available -= required
            self.balances[quote].locked += required
        else:
            self.balances[base].available -= amount
            self.balances[base].locked += amount

        self.order_history.append({
            'action': 'create_order',
            'order_id': order_id,
            'side': side,
            'symbol': symbol,
            'amount': amount,
            'price': price,
            'status': 'open',
            'timestamp': time.time(),
        })

        return {
            'order_id': order_id,
            'status': 'open',
            'amount': amount,
            'filled': 0,
        }

    def fill_order(self, order_id: str, fill_amount: float | None = None) -> dict:
        """Fill an order (partial or full)."""
        if order_id not in self.orders:
            return {'error': 'order_not_found'}

        order = self.orders[order_id]
        if order.status not in ('open', 'partial'):
            return {'error': 'order_not_fillable'}

        # Determine fill amount
        if fill_amount is None:
            fill_amount = order.amount - order.filled_amount

        fill_amount = min(fill_amount, order.amount - order.filled_amount)

        # Calculate fees
        fee_rate = self.taker_fee if order.price is None else self.maker_fee
        fill_value = fill_amount * (order.price or 1000)
        fees = fill_value * fee_rate

        # Update order
        order.filled_amount += fill_amount
        order.fees += fees

        # Update balances
        base, quote = order.symbol.split('/')
        if order.side == 'buy':
            self.balances[base].available += fill_amount
            self.balances[quote].locked -= fill_amount * (order.price or 1000)
            self.balances[quote].available += (fill_amount * (order.price or 1000)) - fees
        else:
            self.balances[quote].available += (fill_amount * (order.price or 1000)) - fees

        # Update status
        if order.filled_amount >= order.amount:
            order.status = 'filled'
        else:
            order.status = 'partial'

        self.order_history.append({
            'action': 'fill_order',
            'order_id': order_id,
            'fill_amount': fill_amount,
            'fees': fees,
            'status': order.status,
            'timestamp': time.time(),
        })

        return {
            'order_id': order_id,
            'filled': fill_amount,
            'fees': fees,
            'status': order.status,
        }

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        if order_id not in self.orders:
            return {'error': 'order_not_found'}

        order = self.orders[order_id]
        if order.status not in ('open', 'partial'):
            return {'error': 'order_not_cancellable'}

        # Refund locked funds
        base, quote = order.symbol.split('/')
        unfilled = order.amount - order.filled_amount

        if order.side == 'buy':
            refund = unfilled * (order.price or 1000)
            self.balances[quote].locked -= refund
            self.balances[quote].available += refund
        else:
            self.balances[base].locked -= unfilled
            self.balances[base].available += unfilled

        order.status = 'cancelled'

        self.order_history.append({
            'action': 'cancel_order',
            'order_id': order_id,
            'timestamp': time.time(),
        })

        return {'order_id': order_id, 'status': 'cancelled'}

    def get_order(self, order_id: str) -> TradeOrder | None:
        """Get order by ID."""
        return self.orders.get(order_id)

    def get_order_history(self) -> list[dict]:
        """Get order history."""
        return self.order_history.copy()

    def reset(self):
        """Reset the exchange to initial state."""
        self.orders.clear()
        self.order_history.clear()
        self._next_order_id = 1
        for c in ['BTC', 'ETH', 'USD']:
            self.balances[c] = Balance(currency=c, available=10000.0 if c != 'BTC' else 10.0)
