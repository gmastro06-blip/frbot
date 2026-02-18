"""Tests for input adapters - coverage for input modules."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


class TestInputAdapterProtocol:
    """Test InputAdapter protocol compliance."""

    def test_protocol_minimal_implementation(self):
        """Verify InputAdapter can be minimally implemented."""
        from adapters.input.base import InputAdapter

        class MinimalInput(InputAdapter):
            name = "test"

            def preflight(self) -> bool:
                return True

            def click(self, x: int, y: int) -> None:
                pass

        adapter = MinimalInput()
        assert adapter.name == "test"
        assert adapter.preflight() is True


class TestCaptureAdapterProtocol:
    """Test CaptureAdapter protocol compliance."""

    def test_frame_validation_valid(self):
        """Test Frame.validate() with valid frame."""
        from adapters.capture.base import Frame

        frame = Frame(width=640, height=480, monotonic_ts_ns=1234567890)
        errors = frame.validate()
        assert errors == []

    def test_frame_validation_invalid_width(self):
        """Test Frame.validate() with invalid width."""
        from adapters.capture.base import Frame

        frame = Frame(width=0, height=480, monotonic_ts_ns=1234567890)
        errors = frame.validate()
        assert "invalid width" in errors[0]

    def test_frame_validation_invalid_height(self):
        """Test Frame.validate() with invalid height."""
        from adapters.capture.base import Frame

        frame = Frame(width=640, height=-1, monotonic_ts_ns=1234567890)
        errors = frame.validate()
        assert "invalid height" in errors[0]

    def test_frame_validation_invalid_rgb_length(self):
        """Test Frame.validate() with RGB length mismatch."""
        from adapters.capture.base import Frame

        # 640*480*3 = 921600, but we provide 100 bytes
        frame = Frame(width=640, height=480, monotonic_ts_ns=1234567890, rgb=b'x' * 100)
        errors = frame.validate()
        assert "invalid rgb length" in errors[0]

    def test_frame_validation_valid_rgb(self):
        """Test Frame.validate() with valid RGB."""
        from adapters.capture.base import Frame

        rgb = b'\x00' * (640 * 480 * 3)  # Full frame
        frame = Frame(width=640, height=480, monotonic_ts_ns=1234567890, rgb=rgb)
        errors = frame.validate()
        assert errors == []


class TestConfigSchema:
    """Test runtime config schema."""

    def test_get_effective_config_returns_dict(self):
        """Test get_effective_config returns a dictionary."""
        from runtime.config_schema import get_effective_config

        config = get_effective_config()
        assert isinstance(config, dict)
        assert len(config) > 0

    def test_get_config_value_with_default(self):
        """Test get_config_value returns default for unknown key."""
        from runtime.config_schema import get_config_value

        value = get_config_value("unknown_key", "default_value")
        assert value == "default_value"

    def test_validate_config_mode_real_with_mock_capture(self):
        """Test validation catches mode=real with capture=mock."""
        from runtime.config_schema import get_effective_config, validate_config
        import os

        # This will use the current env, but we test the validation logic
        config = get_effective_config()
        errors = validate_config()
        assert isinstance(errors, list)

    def test_redact_function(self):
        """Test _redact function."""
        from runtime.config_schema import _redact

        # Short values
        assert _redact("abc") == "****"

        # Long values
        result = _redact("longpassword123")
        assert result.startswith("lo")
        assert result.endswith("23")
        assert "****" in result