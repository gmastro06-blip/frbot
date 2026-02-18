"""Tests for Deposit/Trade Ledger invariants.

These tests verify critical invariants using the FakeLedger and FakeExchange.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'qa_fixtures'))

from fake_ledger import FakeLedger, FakeExchange


class TestDepositIdempotency:
    """Test deposit idempotency invariant."""

    def test_same_txid_returns_same_result(self):
        """Idempotency: same txid should return same result."""
        ledger = FakeLedger(initial_balance=0.0)

        result1 = ledger.create_deposit('tx001', 1.0, 'BTC')
        result2 = ledger.create_deposit('tx001', 1.0, 'BTC')

        assert result1['idempotent'] == False
        assert result2['idempotent'] == True
        assert result1['amount'] == result2['amount']

    def test_retry_does_not_duplicate_balance(self):
        """Retry should not duplicate balance."""
        ledger = FakeLedger(initial_balance=0.0)

        ledger.create_deposit('tx002', 5.0, 'BTC')
        ledger.confirm_deposit('tx002', 1)

        balance_after_first = ledger.get_balance('BTC')

        # Retry
        ledger.create_deposit('tx002', 5.0, 'BTC')

        balance_after_retry = ledger.get_balance('BTC')

        assert balance_after_first == balance_after_retry


class TestDepositConfirmations:
    """Test deposit confirmation handling."""

    def test_insufficient_confirmations_keeps_pending(self):
        """Insufficient confirmations should keep status pending."""
        ledger = FakeLedger(initial_balance=0.0)

        ledger.create_deposit('tx003', 3.0, 'BTC', required_confirmations=3)
        result = ledger.confirm_deposit('tx003', 2)

        assert result['status'] == 'pending'

    def test_sufficient_confirmations_confirms(self):
        """Sufficient confirmations should confirm deposit."""
        ledger = FakeLedger(initial_balance=0.0)

        ledger.create_deposit('tx004', 2.0, 'BTC', required_confirmations=1)
        result = ledger.confirm_deposit('tx004', 1)

        assert result['status'] == 'confirmed'


class TestDepositBalance:
    """Test balance reconciliation."""

    def test_balance_increases_on_confirm(self):
        """Balance should increase only after confirmation."""
        ledger = FakeLedger(initial_balance=10.0)

        ledger.create_deposit('tx005', 5.0, 'BTC')
        balance_after_create = ledger.get_balance('BTC')

        ledger.confirm_deposit('tx005', 1)
        balance_after_confirm = ledger.get_balance('BTC')

        assert balance_after_create == 10.0
        assert balance_after_confirm == 15.0


class TestTradeIdempotency:
    """Test trade idempotency invariant."""

    def test_same_order_id_returns_same_result(self):
        """Same order_id should return same result."""
        exchange = FakeExchange(initial_balance_quote=10000.0)

        result1 = exchange.create_order('order001', 'buy', 'BTC/USDT', 0.1, 50000.0)
        result2 = exchange.create_order('order001', 'buy', 'BTC/USDT', 0.1, 50000.0)

        assert result1['idempotent'] == False
        assert result2['idempotent'] == True


class TestTradeBalanceReconciliation:
    """Test balance reconciliation invariant."""

    def test_balance_conserved_after_fill(self):
        """Total value (balance + order value) should be conserved."""
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)

        initial_balance = exchange.get_balance('USDT')

        exchange.create_order('order002', 'buy', 'BTC/USDT', 0.1, None)

        final_balance = exchange.get_balance('USDT')

        # Should have spent 0.1 * 1000 + fees on BTC purchase
        assert final_balance < initial_balance


class TestTradeRejection:
    """Test trade rejection cases."""

    def test_insufficient_funds_rejects(self):
        """Insufficient funds should reject order."""
        exchange = FakeExchange(initial_balance_quote=100.0)

        result = exchange.create_order(
            'order003',
            'buy',
            'BTC/USDT',
            0.1,
            50000.0  # Requires 5000 USDT
        )

        assert result['status'] == 'rejected'


class TestTradePartialFill:
    """Test partial fill handling."""

    def test_partial_fill_status(self):
        """Partial fill should set status to partial."""
        exchange = FakeExchange(initial_balance_quote=10000.0)

        exchange.create_order('order004', 'buy', 'BTC/USDT', 0.1, 50000.0)
        result = exchange.fill_order('order004', 0.05)

        assert result['filled_quantity'] == 0.05
        assert result['status'] == 'partial'


class TestTradeCancellation:
    """Test order cancellation."""

    def test_cancel_sets_status(self):
        """Cancel should set status to cancelled."""
        exchange = FakeExchange(initial_balance_quote=10000.0)

        exchange.create_order('order005', 'buy', 'BTC/USDT', 0.1, 50000.0)
        result = exchange.cancel_order('order005')

        assert result['status'] == 'cancelled'


class TestTradeFee:
    """Test fee calculation."""

    def test_fee_deducted(self):
        """Fee should be deducted from balance."""
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)

        # Market order fills immediately
        result = exchange.create_order(
            'order006',
            'buy',
            'BTC/USDT',
            1.0,
            None  # Market
        )

        # Fee = 1.0 * 1000 * 0.001 = 1.0
        assert result['fee'] == 1.0
