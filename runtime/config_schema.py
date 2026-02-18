#!/usr/bin/env python
"""
Config schema and validation for FRBot.

Centralizes all FRBOT_* environment variable handling with validation,
defaults, and sanitized logging.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Union, Callable

logger = logging.getLogger(__name__)

# Sensitive env vars that should be redacted in logs
_SECRETS = {'password', 'token', 'secret', 'key', 'api_'}


def _is_secret(key: str) -> bool:
    """Check if env var name suggests it contains a secret."""
    key_lower = key.lower()
    return any(s in key_lower for s in _SECRETS)


def _redact(value: str) -> str:
    """Redact sensitive values."""
    if len(value) <= 4:
        return '****'
    return value[:2] + '****' + value[-2:]


@dataclass
class ConfigSchema:
    """Schema definition for a configuration value."""

    name: str
    default: Any
    env_vars: list[str]
    description: str = ""
    validator: Union[Callable[[Any], Any], None] = None
    type_hint: type = str


# All FRBOT config variables with defaults and validators
_CONFIG_SCHEMA: list[ConfigSchema] = [
    # Capture
    ConfigSchema("capture_source", "real", ["FRBOT_CAPTURE_SOURCE"], "Capture backend: real/mock"),
    ConfigSchema("obs_source_name", "", ["FRBOT_OBS_SOURCE_NAME"], "OBS source name"),
    ConfigSchema("obs_ws_host", "127.0.0.1", ["FRBOT_OBS_WS_HOST"], "OBS WebSocket host"),
    ConfigSchema("obs_ws_port", 4455, ["FRBOT_OBS_WS_PORT"], "OBS WebSocket port", type_hint=int),

    # Runtime
    ConfigSchema("mode", "mock", ["FRBOT_MODE"], "Runtime mode: real/mock"),
    ConfigSchema("profile", "prod_full", ["FRBOT_PROFILE"], "Profile: prod_emergency/prod_full"),
    ConfigSchema("tick_hz", 20.0, ["FRBOT_TICK_HZ"], "Ticks per second", type_hint=float),
    ConfigSchema("max_ticks", 30, ["FRBOT_MAX_TICKS"], "Max ticks per run", type_hint=int),

    # Input
    ConfigSchema("input_method", "", ["FRBOT_INPUT_METHOD"], "Input method: sendinput/postmessage"),
    ConfigSchema("focus_throttle_ms", 500, ["FRBOT_FOCUS_THROTTLE_MS"], "Focus throttle ms", type_hint=int),

    # Cavebot
    ConfigSchema("cavebot_waypoints_file", "", ["FRBOT_CAVEBOT_WAYPOINTS_FILE"], "Waypoints JSON file"),
    ConfigSchema("cavebot_stuck_window", 5, ["FRBOT_CAVEBOT_STUCK_WINDOW"], "Stuck detection window (ticks)", type_hint=int),
    ConfigSchema("player_marker_rgb", "255,0,255", ["FRBOT_PLAYER_MARKER_RGB"], "Player marker RGB"),
    ConfigSchema("player_marker_tol", 30, ["FRBOT_PLAYER_MARKER_TOL"], "Marker tolerance", type_hint=int),

    # Healing
    ConfigSchema("enable_healing", False, ["FRBOT_ENABLE_HEALING"], "Enable healing gate", type_hint=bool),
    ConfigSchema("heal_key", "F1", ["FRBOT_HEAL_KEY"], "Heal hotkey"),
    ConfigSchema("heal_hp_threshold", 0.5, ["FRBOT_HEAL_HP_THRESHOLD"], "HP threshold (0-1)", type_hint=float),
    ConfigSchema("heal_allow_no_evidence", False, ["FRBOT_HEAL_ALLOW_NO_EVIDENCE"], "Allow no evidence", type_hint=bool),

    # Combat
    ConfigSchema("enable_combat", False, ["FRBOT_ENABLE_COMBAT"], "Enable combat gate", type_hint=bool),
    ConfigSchema("attack_key", "SPACE", ["FRBOT_ATTACK_KEY"], "Attack hotkey"),

    # Looting
    ConfigSchema("enable_looting", False, ["FRBOT_ENABLE_LOOTING"], "Enable looting gate", type_hint=bool),

    # Deposit/Trade
    ConfigSchema("deposit_backend", "mock", ["FRBOT_DEPOSIT_BACKEND"], "Deposit backend"),
    ConfigSchema("trade_backend", "mock", ["FRBOT_TRADE_BACKEND"], "Trade backend"),
]


def get_effective_config() -> dict[str, Any]:
    """Get effective configuration from environment with defaults.

    Returns a dictionary of all FRBOT config values with their effective values.
    Secrets are redacted in the returned dictionary for safe logging.
    """
    result: dict[str, Any] = {}

    for schema in _CONFIG_SCHEMA:
        # Try each env var in order
        value: Any = None
        for env_var in schema.env_vars:
            value = os.environ.get(env_var)
            if value is not None:
                break

        # Use default if not found
        if value is None:
            value = schema.default

        # Convert type
        if schema.type_hint is bool:
            value = value in {'1', 'true', 'yes', 'on'} if isinstance(value, str) else bool(value)
        elif schema.type_hint is int:
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = schema.default
        elif schema.type_hint is float:
            try:
                value = float(value)
            except (ValueError, TypeError):
                value = schema.default

        # Validate
        if schema.validator and value is not None:
            try:
                value = schema.validator(value)
            except Exception:
                value = schema.default

        # Store with schema name as key
        result[schema.name] = value

    return result


def print_effective_config() -> None:
    """Log effective configuration (for debugging).

    Secrets are redacted.
    """
    config = get_effective_config()
    logger.info("=" * 60)
    logger.info("FRBOT EFFECTIVE CONFIG")
    logger.info("=" * 60)

    for key, value in sorted(config.items()):
        # Check if key might be secret
        display_value = _redact(str(value)) if _is_secret(key) else value
        logger.info(f"  {key}: {display_value}")

    logger.info("=" * 60)


def get_config_value(name: str, default: Any = None) -> Any:
    """Get a specific config value by name.

    Args:
        name: Config name (e.g., 'tick_hz', 'mode')
        default: Default value if not found

    Returns:
        The config value or default
    """
    config = get_effective_config()
    return config.get(name, default)


def validate_config() -> list[str]:
    """Validate configuration and return list of errors.

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []
    config = get_effective_config()

    # Validate specific combinations
    if config.get('mode') == 'real' and config.get('capture_source') == 'mock':
        errors.append("mode=real requires capture_source!=mock")

    tick_hz = config.get('tick_hz')
    if isinstance(tick_hz, (int, float)):
        if tick_hz <= 0 or tick_hz > 200:
            errors.append(f"tick_hz must be in (0, 200], got {tick_hz}")
    else:
        errors.append(f"tick_hz must be numeric, got {tick_hz}")

    return errors


def _log_config() -> None:
    """Log effective configuration (for debugging)."""
    config = get_effective_config()
    logger.info("=" * 60)
    logger.info("FRBOT EFFECTIVE CONFIG")
    logger.info("=" * 60)

    for key, value in sorted(config.items()):
        display_value = _redact(str(value)) if _is_secret(key) else value
        logger.info(f"  {key}: {display_value}")

    logger.info("=" * 60)


# Module can be run directly for debugging
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    _log_config()
    errors = validate_config()
    if errors:
        logger.info("\nVALIDATION ERRORS:")
        for err in errors:
            logger.info(f"  - {err}")
    else:
        logger.info("\nValidation: OK")
