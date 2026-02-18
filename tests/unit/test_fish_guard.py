"""Tests for fish_guard auto-fish feature."""

import pytest
from dataclasses import dataclass
from runtime.fish_guard import (
    FishSettings,
    is_fish_biting,
    should_fish,
    auto_fish_tick,
    calculate_fish_chance,
    check_bait_in_inventory,
)


def test_is_fish_biting_returns_tuple():
    """Test is_fish_biting returns correct tuple."""
    @dataclass
    class MockFrame:
        rgb: bytes = b''
        width: int = 0
        height: int = 0

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 10
        height: int = 10

    settings = FishSettings()
    frame = MockFrame()
    roi = MockRoi()

    biting, ratio = is_fish_biting(frame, roi, settings)

    assert isinstance(biting, bool)
    assert isinstance(ratio, float)


def test_is_fish_biting_no_bite():
    """Test is_fish_biting when no green indicator."""
    @dataclass
    class MockFrame:
        rgb: bytes = b''
        width: int = 0
        height: int = 0

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 10
        height: int = 10

    settings = FishSettings()
    frame = MockFrame()
    roi = MockRoi()

    biting, ratio = is_fish_biting(frame, roi, settings)

    assert biting is False
    assert ratio == 0.0


def test_should_fish_first_time():
    """Test should_fish returns True for first attempt."""
    result = should_fish(now_ms=1000, last_fish_ts_ms=None, fish_interval_ms=1000)
    assert result is True


def test_should_fish_cooldown_not_ready():
    """Test should_fish returns False when cooldown not ready."""
    result = should_fish(now_ms=1500, last_fish_ts_ms=1000, fish_interval_ms=1000)
    assert result is False


def test_should_fish_cooldown_ready():
    """Test should_fish returns True when cooldown ready."""
    result = should_fish(now_ms=2500, last_fish_ts_ms=1000, fish_interval_ms=1000)
    assert result is True


def test_auto_fish_tick_not_ready_cooldown():
    """Test auto_fish_tick during cooldown period."""
    @dataclass
    class MockFrame:
        rgb: bytes = b''
        width: int = 0
        height: int = 0

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 10
        height: int = 10

    settings = FishSettings(fish_interval_ms=1000)
    frame = MockFrame()
    roi = MockRoi()

    fished, new_ts, ratio = auto_fish_tick(frame, roi, settings, 1500, 1000)

    assert fished is False
    assert new_ts == 1000  # Should return previous timestamp


def test_auto_fish_tick_ready_no_bite():
    """Test auto_fish_tick when ready but no bite."""
    @dataclass
    class MockFrame:
        rgb: bytes = b''
        width: int = 0
        height: int = 0

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 10
        height: int = 10

    settings = FishSettings(fish_interval_ms=1000)
    frame = MockFrame()
    roi = MockRoi()

    fished, new_ts, ratio = auto_fish_tick(frame, roi, settings, 2500, 1000)

    # No bite detected, should not fish
    assert fished is False
    assert ratio == 0.0


def test_calculate_fish_chance_max():
    """Test fish chance at max skill (77+)."""
    # At skill 77+, max chance is 50%
    assert calculate_fish_chance(77) == 0.5
    assert calculate_fish_chance(100) == 0.5
    assert calculate_fish_chance(200) == 0.5


def test_calculate_fish_chance_below_max():
    """Test fish chance below max skill."""
    # Skill 0 = 0%
    assert calculate_fish_chance(0) == 0.0
    # Skill 50 = ~32%
    chance = calculate_fish_chance(50)
    assert 0.3 < chance < 0.35


def test_check_bait_in_inventory_found():
    """Test bait detection when present."""
    inventory = "gold coin 100\nworm 50\nmeat"
    assert check_bait_in_inventory(inventory, 'worm') is True


def test_check_bait_in_inventory_not_found():
    """Test bait detection when not present."""
    inventory = "gold coin 100\nmeat"
    assert check_bait_in_inventory(inventory, 'worm') is False


def test_check_bait_case_insensitive():
    """Test bait detection is case insensitive."""
    inventory = "gold coin 100\nWORM 50\nmeat"
    assert check_bait_in_inventory(inventory, 'worm') is True


def test_calculate_fish_chance():
    """Test fishing chance calculation based on Tibia mechanics."""
    # Skill 0 = 0% chance
    assert calculate_fish_chance(0) == 0.0

    # Skill 50 = ~32% chance
    chance = calculate_fish_chance(50)
    assert 0.3 < chance < 0.35

    # Skill 77+ = 50% max
    assert calculate_fish_chance(77) == 0.5
    assert calculate_fish_chance(100) == 0.5
    assert calculate_fish_chance(200) == 0.5


def test_check_bait_in_inventory():
    """Test bait detection in inventory text."""
    # No text = no bait
    assert check_bait_in_inventory('') is False

    # Case insensitive
    assert check_bait_in_inventory('Worm') is True
    assert check_bait_in_inventory('WORM') is True
    assert check_bait_in_inventory('worm') is True

    # Partial match should work
    assert check_bait_in_inventory('10 worms') is True

    # No worm = no bait
    assert check_bait_in_inventory('sword shield helmet') is False
