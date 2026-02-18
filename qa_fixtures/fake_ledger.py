#!/usr/bin/env python3
"""Fake Ledger/Exchange for QA Testing.

Provides deterministic mock implementations for deposit and trade operations.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Transaction status
TxStatus = Literal['pending', 'confirmed', 'failed']


@dataclass
class Transaction:
    """Represents a ledger transaction."""
    txid: str
    amount: float
    currency: str
    status: TxStatus = 'pending'
    confirmations: int = 0
    required_confirmations: int = 1
    created_at: float = field(default_factory=time.time)


@dataclass
class TradeOrder:
    """Represents a trade order."""
    order_id: str
    side: Literal['buy', 'sell']
    symbol: str
    quantity: float
    price: float | None  # None = market order
    filled_quantity: float = 0.0
    status: Literal['pending', 'filled', 'partial', 'cancelled', 'rejected'] = 'pending'
    fee: float = 0.0
    created_at: float = field(default_factory=time.time)


class FakeLedger:
    """Deterministic mock ledger for deposit testing.

    Features:
    - Idempotent transactions by txid
    - Configurable confirmations
    - Balance tracking
    - Event log
    """

    def __init__(self, initial_balance: float = 1000.0):
        self._balance = initial_balance
        self._transactions: dict[str, Transaction] = {}
        self._tx_history: list[dict] = []
        self._deposit_debug_dir = Path('diagnostics/deposit_debug')

    def get_balance(self, currency: str = 'BTC') -> float:
        return self._balance

    def create_deposit(self, txid: str, amount: float, currency: str = 'BTC',
                       required_confirmations: int = 1) -> dict[str, Any]:
        """Create or get existing deposit (idempotent by txid)."""
        run_id = int(time.time() * 1000)

        # Idempotency: return existing if txid exists
        if txid in self._transactions:
            tx = self._transactions[txid]
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
            required_confirmations=required_confirmations,
        )
        self._transactions[txid] = tx

        # Record history
        self._tx_history.append({
            'run_id': run_id,
            'action': 'create_deposit',
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

    def confirm_deposit(self, txid: str, confirmations: int = 1) -> dict[str, Any]:
        """Confirm a deposit transaction."""
        run_id = int(time.time() * 1000)

        if txid not in self._transactions:
            return {'error': 'txid_not_found', 'txid': txid}

        tx = self._transactions[txid]
        tx.confirmations = confirmations

        if confirmations >= tx.required_confirmations:
            tx.status = 'confirmed'
            self._balance += tx.amount

        # Record history
        self._tx_history.append({
            'run_id': run_id,
            'action': 'confirm_deposit',
            'txid': txid,
            'confirmations': confirmations,
            'new_status': tx.status,
            'timestamp': time.time(),
        })

        return {
            'txid': tx.txid,
            'status': tx.status,
            'confirmations': tx.confirmations,
            'balance_after': self._balance,
        }

    def get_deposit_status(self, txid: str) -> dict[str, Any]:
        """Get deposit status."""
        if txid not in self._transactions:
            return {'error': 'txid_not_found', 'txid': txid}

        tx = self._transactions[txid]
        return {
            'txid': tx.txid,
            'amount': tx.amount,
            'currency': tx.currency,
            'status': tx.status,
            'confirmations': tx.confirmations,
        }

    def get_history(self) -> list[dict]:
        return list(self._tx_history)

    def dump_manifest(self, run_id: int, test_name: str, inputs: dict) -> Path:
        """Dump manifest JSON for QA evidence."""
        manifest = {
            'run_id': run_id,
            'test_name': test_name,
            'inputs': inputs,
            'final_balance': self._balance,
            'transactions': [
                {
                    'txid': t.txid,
                    'amount': t.amount,
                    'status': t.status,
                    'confirmations': t.confirmations,
                }
                for t in self._transactions.values()
            ],
            'history': self._tx_history,
            'timestamp': time.time(),
        }

        debug_dir = self._deposit_debug_dir / str(run_id)
        debug_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = debug_dir / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return manifest_path


class FakeExchange:
    """Deterministic mock exchange for trade testing.

    Features:
    - Limit and market orders
    - Partial fills
    - Cancellation
    - Fee calculation
    - Order idempotency
    """

    def __init__(self, initial_balance_quote: float = 10000.0,
                 initial_balance_base: float = 1.0):
        self._balance_quote = initial_balance_quote  # USDT
        self._balance_base = initial_balance_base    # BTC
        self._orders: dict[str, TradeOrder] = {}
        self._order_history: list[dict] = []
        self._trade_debug_dir = Path('diagnostics/trade_debug')
        self._default_fee = 0.001  # 0.1%

    def get_balance(self, asset: str) -> float:
        if asset == 'USDT':
            return self._balance_quote
        elif asset == 'BTC':
            return self._balance_base
        return 0.0

    def create_order(self, order_id: str, side: str, symbol: str,
                     quantity: float, price: float | None = None) -> dict[str, Any]:
        """Create a new order (idempotent by order_id)."""
        run_id = int(time.time() * 1000)

        # Idempotency: return existing if order_id exists
        if order_id in self._orders:
            order = self._orders[order_id]
            return {
                'order_id': order.order_id,
                'status': order.status,
                'filled_quantity': order.filled_quantity,
                'idempotent': True,
            }

        # Check balance
        if side == 'buy':
            required = quantity * (price or 0) * (1 + self._default_fee)
            if required > self._balance_quote:
                order = TradeOrder(
                    order_id=order_id,
                    side=side,
                    symbol=symbol,
                    quantity=quantity,
                    price=price,
                    status='rejected',
                )
                self._orders[order_id] = order
                self._order_history.append({
                    'run_id': run_id,
                    'action': 'create_order',
                    'order_id': order_id,
                    'side': side,
                    'status': 'rejected',
                    'reason': 'insufficient_quote_balance',
                    'timestamp': time.time(),
                })
                return {
                    'order_id': order_id,
                    'status': 'rejected',
                    'reason': 'insufficient_funds',
                }
        else:  # sell
            if quantity > self._balance_base:
                order = TradeOrder(
                    order_id=order_id,
                    side=side,
                    symbol='BTC/USDT',
                    quantity=quantity,
                    price=price,
                    status='rejected',
                )
                self._orders[order_id] = order
                self._order_history.append({
                    'run_id': run_id,
                    'action': 'create_order',
                    'order_id': order_id,
                    'side': side,
                    'status': 'rejected',
                    'reason': 'insufficient_base_balance',
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
            quantity=quantity,
            price=price,
            status='pending',
        )
        self._orders[order_id] = order

        self._order_history.append({
            'run_id': run_id,
            'action': 'create_order',
            'order_id': order_id,
            'side': side,
            'quantity': quantity,
            'price': price,
            'status': 'pending',
            'timestamp': time.time(),
        })

        # If market order, fill immediately
        if price is None:
            return self._fill_order(order_id, quantity)

        return {
            'order_id': order_id,
            'status': 'pending',
            'filled_quantity': 0.0,
            'idempotent': False,
        }

    def _fill_order(self, order_id: str, fill_quantity: float) -> dict[str, Any]:
        """Fill an order (partial or full)."""
        run_id = int(time.time() * 1000)
        order = self._orders[order_id]

        # Calculate fill
        actual_fill = min(fill_quantity, order.quantity - order.filled_quantity)
        price = order.price or 1000.0  # Market price
        fee = actual_fill * price * self._default_fee

        order.filled_quantity += actual_fill

        if order.filled_quantity >= order.quantity:
            order.status = 'filled'
        else:
            order.status = 'partial'

        order.fee += fee

        # Update balances
        if order.side == 'buy':
            self._balance_quote -= (actual_fill * price + fee)
            self._balance_base += actual_fill
        else:
            self._balance_base -= actual_fill
            self._balance_quote += (actual_fill * price - fee)

        self._order_history.append({
            'run_id': run_id,
            'action': 'fill_order',
            'order_id': order_id,
            'filled': actual_fill,
            'fee': fee,
            'new_status': order.status,
            'timestamp': time.time(),
        })

        return {
            'order_id': order_id,
            'status': order.status,
            'filled_quantity': order.filled_quantity,
            'fee': fee,
        }

    def fill_order(self, order_id: str, fill_quantity: float) -> dict[str, Any]:
        """Public fill order method."""
        if order_id not in self._orders:
            return {'error': 'order_not_found', 'order_id': order_id}
        return self._fill_order(order_id, fill_quantity)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an order."""
        run_id = int(time.time() * 1000)

        if order_id not in self._orders:
            return {'error': 'order_not_found', 'order_id': order_id}

        order = self._orders[order_id]
        order.status = 'cancelled'

        self._order_history.append({
            'run_id': run_id,
            'action': 'cancel_order',
            'order_id': order_id,
            'timestamp': time.time(),
        })

        return {
            'order_id': order_id,
            'status': 'cancelled',
        }

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Get order status."""
        if order_id not in self._orders:
            return {'error': 'order_not_found', 'order_id': order_id}

        order = self._orders[order_id]
        return {
            'order_id': order.order_id,
            'side': order.side,
            'symbol': order.symbol,
            'quantity': order.quantity,
            'price': order.price,
            'filled_quantity': order.filled_quantity,
            'status': order.status,
            'fee': order.fee,
        }

    def get_history(self) -> list[dict]:
        return list(self._order_history)

    def dump_manifest(self, run_id: int, test_name: str, inputs: dict) -> Path:
        """Dump manifest JSON for QA evidence."""
        manifest = {
            'run_id': run_id,
            'test_name': test_name,
            'inputs': inputs,
            'final_balances': {
                'USDT': self._balance_quote,
                'BTC': self._balance_base,
            },
            'orders': [
                {
                    'order_id': o.order_id,
                    'side': o.side,
                    'quantity': o.quantity,
                    'filled_quantity': o.filled_quantity,
                    'status': o.status,
                    'fee': o.fee,
                }
                for o in self._orders.values()
            ],
            'history': self._order_history,
            'timestamp': time.time(),
        }

        debug_dir = self._trade_debug_dir / str(run_id)
        debug_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = debug_dir / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return manifest_path
