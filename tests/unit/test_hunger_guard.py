"""Tests for hunger_guard auto_eat integration."""

import pytest
from dataclasses import dataclass
from runtime.hunger_guard import (
    HungerSettings,
    is_hungry,
    should_press_eat,
    auto_eat_tick,
)


def test_auto_eat_tick_returns_tuple():
    """Test auto_eat_tick returns correct tuple structure."""
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

    settings = HungerSettings()
    frame = MockFrame()
    roi = MockRoi()

    # Test with no hunger
    ate, new_ts, ratio = auto_eat_tick(frame, roi, settings, 1000, None)

    assert isinstance(ate, bool)
    assert new_ts is None or isinstance(new_ts, int)
    assert isinstance(ratio, float)


def test_auto_eat_tick_not_hungry():
    """Test auto_eat_tick when not hungry."""
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

    settings = HungerSettings()
    frame = MockFrame()
    roi = MockRoi()

    # Frame with no pixels matching hungry color -> not hungry
    ate, new_ts, ratio = auto_eat_tick(frame, roi, settings, 1000, None)

    # Should not eat when not hungry
    assert ate is False
    assert ratio == 0.0


def test_auto_eat_tick_hungry_and_cooldown_ready():
    """Test auto_eat_tick when hungry and cooldown is ready."""
    # Test should_press_eat directly with hungry=True and no previous eat
    result = should_press_eat(
        hungry=True,
        now_ms=1000,
        last_eat_ts_ms=None,
        eat_interval_ms=1200,
    )
    assert result is True


def test_auto_eat_tick_hungry_but_cooldown():
    """Test auto_eat_tick when hungry but cooldown not ready."""
    @dataclass
    class MockFrame:
        rgb: bytes = b'\xff\xaa\x00' * 1
        width: int = 1
        height: int = 1

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 1
        height: int = 1

    settings = HungerSettings(match_ratio_min=0.5, eat_interval_ms=1000)
    frame = MockFrame()
    roi = MockRoi()

    # Called at 2000ms but last eat was at 1000ms with 1000ms cooldown
    ate, new_ts, ratio = auto_eat_tick(frame, roi, settings, 1500, 1000)

    # Should NOT eat due to cooldown
    assert ate is False
    assert new_ts == 1000  # Should return previous timestamp


def test_auto_eat_tick_after_cooldown():
    """Test auto_eat_tick after cooldown is ready."""
    @dataclass
    class MockFrame:
        rgb: bytes = b'\xff\xaa\x00' * 1
        width: int = 1
        height: int = 1

    @dataclass
    class MockRoi:
        x: int = 0
        y: int = 0
        width: int = 1
        height: int = 1

    settings = HungerSettings(match_ratio_min=0.5, eat_interval_ms=1000)
    frame = MockFrame()
    roi = MockRoi()

    # Called at 2500ms, last eat at 1000ms, cooldown 1000ms -> should eat
    ate, new_ts, ratio = auto_eat_tick(frame, roi, settings, 2500, 1000)

    assert ate is True
    assert new_ts == 2500
