"""Tests for trade_runner - basic coverage."""
from __future__ import annotations

import pytest


class TestTradeTickEvidence:
    """Test TradeTickEvidence dataclass."""

    def test_evidence_status(self):
        """Test evidence status field."""
        from runtime.trade_runner import TradeTickEvidence

        evidence = TradeTickEvidence(
            npc=None,
            inventory_before=None,
            inventory_after=None,
            delta=None,
            status='success',
        )
        assert evidence.status == 'success'

    def test_evidence_status_failure(self):
        """Test evidence status with failure."""
        from runtime.trade_runner import TradeTickEvidence

        evidence = TradeTickEvidence(
            npc=None,
            inventory_before=None,
            inventory_after=None,
            delta=None,
            status='trade_attempts_exhausted',
        )
        assert evidence.status == 'trade_attempts_exhausted'
