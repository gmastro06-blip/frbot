"""Tests for deposit_runner - basic coverage."""
from __future__ import annotations


class TestDepositTickEvidence:
    """Test DepositTickEvidence dataclass."""

    def test_evidence_status(self):
        """Test evidence status field."""
        from runtime.deposit_runner import DepositTickEvidence

        evidence = DepositTickEvidence(
            inventory_before=None,
            inventory_after=None,
            depot_before=None,
            depot_after=None,
            inventory_delta=None,
            depot_delta=None,
            status='success',
        )
        assert evidence.status == 'success'

    def test_evidence_status_failure(self):
        """Test evidence status with failure."""
        from runtime.deposit_runner import DepositTickEvidence

        evidence = DepositTickEvidence(
            inventory_before=None,
            inventory_after=None,
            depot_before=None,
            depot_after=None,
            inventory_delta=None,
            depot_delta=None,
            status='deposit_inventory_unreadable',
        )
        assert evidence.status == 'deposit_inventory_unreadable'
