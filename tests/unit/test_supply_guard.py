"""Tests for supply_guard auto-supply (potion) feature."""

import pytest
from dataclasses import dataclass
from runtime.supply_guard import (
    SupplySettings,
    POTION_TYPES,
    parse_hp_from_bar,
    parse_mp_from_bar,
    should_drink_health_potion,
    should_drink_mana_potion,
    auto_supply_tick,
    _count_potions_in_inventory,
)


def test_supply_settings_defaults():
    """Test SupplySettings has correct defaults."""
    settings = SupplySettings()

    assert settings.enable_potion_refill is False
    assert settings.hp_threshold == 0.5
    assert settings.mp_threshold == 0.3
    assert settings.health_potion_key == 'F1'
    assert settings.mana_potion_key == 'F2'


def test_potion_types_defined():
    """Test POTION_TYPES contains expected potions."""
    assert 'health' in POTION_TYPES
    assert 'mana' in POTION_TYPES
    assert 'strong_health' in POTION_TYPES
    assert 'great_health' in POTION_TYPES


def test_should_drink_health_potion_true():
    """Test should_drink_health_potion when HP is low."""
    settings = SupplySettings(hp_threshold=0.5)

    assert should_drink_health_potion(0.3, settings) is True
    assert should_drink_health_potion(0.49, settings) is True
    assert should_drink_health_potion(0.5, settings) is False


def test_should_drink_health_potion_false():
    """Test should_drink_health_potion when HP is high."""
    settings = SupplySettings(hp_threshold=0.5)

    assert should_drink_health_potion(0.8, settings) is False
    assert should_drink_health_potion(1.0, settings) is False


def test_should_drink_health_potion_none():
    """Test should_drink_health_potion when HP is unreadable."""
    settings = SupplySettings()

    assert should_drink_health_potion(None, settings) is False


def test_should_drink_mana_potion_true():
    """Test should_drink_mana_potion when MP is low."""
    settings = SupplySettings(mp_threshold=0.3)

    assert should_drink_mana_potion(0.2, settings) is True
    assert should_drink_mana_potion(0.29, settings) is True


def test_should_drink_mana_potion_false():
    """Test should_drink_mana_potion when MP is high."""
    settings = SupplySettings(mp_threshold=0.3)

    assert should_drink_mana_potion(0.5, settings) is False
    assert should_drink_mana_potion(1.0, settings) is False


def test_count_potions_in_inventory():
    """Test counting potions in inventory text."""
    inventory = "gold coin 100\nhealth potion 5\nmana potion 3\nmeat"

    count = _count_potions_in_inventory(inventory)

    # Should detect health and mana
    assert count >= 2


def test_count_potions_empty():
    """Test counting potions when none present."""
    inventory = "gold coin 100\nmeat"

    count = _count_potions_in_inventory(inventory)

    assert count == 0


def test_count_potions_empty_text():
    """Test counting potions with empty text."""
    count = _count_potions_in_inventory("")

    assert count == 0


def test_auto_supply_tick_disabled():
    """Test auto_supply_tick when disabled."""
    settings = SupplySettings(enable_potion_refill=False)

    result = auto_supply_tick(
        frame=None,
        hp_bar_roi=None,
        mp_bar_roi=None,
        inventory_text="",
        settings=settings,
        now_ms=1000,
        last_drink_ts_ms=None,
    )

    should_hp, should_mp, should_buy, key, new_ts = result
    assert should_hp is False
    assert should_mp is False
    assert key is None


def test_auto_supply_tick_cooldown():
    """Test auto_supply_tick during cooldown."""
    settings = SupplySettings(enable_potion_refill=True, drink_interval_ms=1000)

    # Last drink was 500ms ago, interval is 1000ms
    result = auto_supply_tick(
        frame=None,
        hp_bar_roi=None,
        mp_bar_roi=None,
        inventory_text="",
        settings=settings,
        now_ms=1500,
        last_drink_ts_ms=1000,
    )

    should_hp, should_mp, should_buy, key, new_ts = result

    # Should NOT drink due to cooldown
    assert should_hp is False
    assert should_mp is False
    assert new_ts == 1000  # Timestamp unchanged


def test_auto_supply_tick_ready_no_frame():
    """Test auto_supply_tick ready but no frame data."""
    settings = SupplySettings(enable_potion_refill=True, drink_interval_ms=1000)

    # Enough time has passed
    result = auto_supply_tick(
        frame=None,
        hp_bar_roi=None,
        mp_bar_roi=None,
        inventory_text="",
        settings=settings,
        now_ms=3000,
        last_drink_ts_ms=1000,
    )

    should_hp, should_mp, should_buy, key, new_ts = result

    # No frame data = can't determine HP/MP = no action
    assert should_hp is False
    assert should_mp is False
