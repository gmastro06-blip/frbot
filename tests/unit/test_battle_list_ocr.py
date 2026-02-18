"""Tests for battle list OCR functionality."""

import os
import pytest
from PIL import Image


# Mock mode tests
def test_ocr_mock_mode_returns_entities(monkeypatch):
    """Test that mock mode returns deterministic entities."""
    monkeypatch.setenv('FRBOT_OCR_MOCK', '1')

    from runtime.battle_list_ocr import detect_monsters_with_ocr

    img = Image.new('RGB', (200, 200), color=(50, 50, 50))
    result = detect_monsters_with_ocr(img)

    assert len(result) == 3
    assert result[0]['name'] == 'Dragon'
    assert result[0]['match_type'] == 'mock'
    assert result[0]['confidence'] == 0.95


def test_ocr_pipeline_mock_mode(monkeypatch):
    """Test full OCR pipeline in mock mode."""
    monkeypatch.setenv('FRBOT_OCR_MOCK', '1')

    from runtime.battle_list_ocr import run_ocr_pipeline

    # Use 200x200 which auto-detects as tibia_low_res
    img = Image.new('RGB', (200, 200), color=(50, 50, 50))
    result = run_ocr_pipeline(img)

    assert result['entities_count'] > 0
    # Profile is auto-detected from resolution
    assert result['profile_name'] in ('default', 'tibia_low_res')


def test_ocr_ui_profiles(monkeypatch):
    """Test UI profile selection."""
    from runtime.battle_list_ocr import UI_PROFILES

    # Default profile
    profile = UI_PROFILES.get('default')
    assert profile is not None
    assert profile.ocr_upscale == 3

    # Different profile
    profile2 = UI_PROFILES.get('tibia_hi_res')
    assert profile2 is not None
    assert profile2.ocr_upscale == 4


def test_ocr_normalization():
    """Test text normalization handles common OCR confusions."""
    from runtime.battle_list_ocr import _normalize_text

    # Test space normalization (collapse multiple spaces)
    assert _normalize_text('Orc   Warrior') == 'Orc Warrior'
    # Test strip
    assert _normalize_text('  Orc  ') == 'Orc'


def test_ocr_confidence_threshold(monkeypatch):
    """Test confidence threshold from profile."""
    monkeypatch.setenv('FRBOT_OCR_MOCK', '1')

    from runtime.battle_list_ocr import detect_monsters_with_ocr

    img = Image.new('RGB', (200, 200), color=(50, 50, 50))

    # Mock always returns high confidence
    result = detect_monsters_with_ocr(img, min_confidence=0.5)
    assert len(result) == 3

    # Very high threshold should still return mock (bypasses check in mock mode)
    result2 = detect_monsters_with_ocr(img, min_confidence=0.99)
    assert len(result2) == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
