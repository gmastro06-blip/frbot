"""Tests for combat_runner - basic coverage."""
from __future__ import annotations


class TestCombatState:
    """Test CombatState dataclass."""

    def test_combat_state_exists(self):
        """Test CombatState can be instantiated."""
        from contracts.runtime import CombatState

        state = CombatState()
        assert state is not None
