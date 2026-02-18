"""Tests for ring_guard auto-ring feature."""

import pytest
from dataclasses import dataclass
from runtime.ring_guard import (
    RingSettings,
    TIBIA_RINGS,
    is_ring_equipped,
    should_equip_ring,
    check_ring_in_inventory,
    get_ring_to_equip,
    auto_ring_tick,
)


def test_ring_settings_defaults():
    """Test RingSettings has correct defaults."""
    settings = RingSettings()

    assert settings.ring_slot_roi == 'ring_slot'
    assert settings.equip_key == 'F11'
    assert settings.default_ring == 'power_ring'
    assert settings.ring_switch_interval_ms == 5000


def test_tibia_rings_defined():
    """Test TIBIA_RINGS contains expected rings."""
    assert 'power_ring' in TIBIA_RINGS
    assert 'time_ring' in TIBIA_RINGS
    assert 'stealth_ring' in TIBIA_RINGS


def test_is_ring_equipped_returns_tuple():
    """Test is_ring_equipped returns correct tuple."""
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

    settings = RingSettings()
    frame = MockFrame()
    roi = MockRoi()

    equipped, ratio = is_ring_equipped(frame, roi, settings)

    assert isinstance(equipped, bool)
    assert isinstance(ratio, float)


def test_is_ring_equipped_no_ring():
    """Test is_ring_equipped when no ring."""
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

    settings = RingSettings()
    frame = MockFrame()
    roi = MockRoi()

    equipped, ratio = is_ring_equipped(frame, roi, settings)

    assert equipped is False


def test_should_equip_ring_first_time():
    """Test should_equip_ring returns True for first time."""
    result = should_equip_ring(now_ms=1000, last_ring_ts_ms=None, ring_switch_interval_ms=1000)
    assert result is True


def test_should_equip_ring_cooldown():
    """Test should_equip_ring respects cooldown."""
    # Should return False during cooldown
    result = should_equip_ring(now_ms=1500, last_ring_ts_ms=1000, ring_switch_interval_ms=1000)
    assert result is False

    # Should return True after cooldown
    result = should_equip_ring(now_ms=2500, last_ring_ts_ms=1000, ring_switch_interval_ms=1000)
    assert result is True


def test_check_ring_in_inventory_found():
    """Test ring detection when present."""
    inventory = "gold coin 100\npower ring\nmeat"
    assert check_ring_in_inventory(inventory, 'power ring') is True


def test_check_ring_in_inventory_not_found():
    """Test ring detection when not present."""
    inventory = "gold coin 100\nmeat"
    assert check_ring_in_inventory(inventory, 'power ring') is False


def test_check_ring_case_insensitive():
    """Test ring detection is case insensitive."""
    inventory = "gold coin 100\nPOWER RING\nmeat"
    assert check_ring_in_inventory(inventory, 'power ring') is True


def test_get_ring_to_equip_no_current():
    """Test ring equip when nothing currently equipped."""
    settings = RingSettings(default_ring='power_ring')
    inventory = "gold coin 100\npower_ring"  # Use underscore format

    ring = get_ring_to_equip(inventory, settings, current_ring=None)

    assert ring == 'power_ring'


def test_get_ring_to_equip_already_equipped():
    """Test no change when correct ring already equipped."""
    settings = RingSettings(default_ring='power_ring')
    inventory = "gold coin 100\npower ring"

    ring = get_ring_to_equip(inventory, settings, current_ring='power_ring')

    assert ring is None


def test_get_ring_to_equip_not_in_inventory():
    """Test no ring when not in inventory."""
    settings = RingSettings(default_ring='power_ring')
    inventory = "gold coin 100\nmeat"

    ring = get_ring_to_equip(inventory, settings, current_ring=None)

    assert ring is None


def test_auto_ring_tick_cooldown():
    """Test auto_ring_tick during cooldown."""
    settings = RingSettings(ring_switch_interval_ms=1000)

    should_equip, ring_name, new_ts, ratio = auto_ring_tick(
        frame=None,
        ring_roi=None,
        inventory_text="",
        settings=settings,
        now_ms=1500,
        last_ring_ts_ms=1000,
        current_ring=None,
    )

    assert should_equip is False
    assert new_ts == 1000


def test_auto_ring_tick_no_inventory():
    """Test auto_ring_tick when ring not in inventory."""
    settings = RingSettings(default_ring='power_ring', ring_switch_interval_ms=1000)

    should_equip, ring_name, new_ts, ratio = auto_ring_tick(
        frame=None,
        ring_roi=None,
        inventory_text="gold coin 100",
        settings=settings,
        now_ms=2500,
        last_ring_ts_ms=1000,
        current_ring=None,
    )

    assert should_equip is False
    assert ring_name is None
