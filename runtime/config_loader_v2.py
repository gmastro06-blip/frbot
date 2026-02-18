"""Centralized configuration loader using dataclasses with validation.

Replaces scattered _env_* functions across entrypoints with a unified config system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, cast


# === Base env parsers ===

def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return default if raw is None else raw


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    try:
        return int(raw, 0)  # Support hex
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return int(default)


def _env_float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except Exception:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {'0', 'false', 'no', 'off'}


def _env_choice(name: str, choices: set[str], default: str) -> str:
    raw = _env_str(name, default).strip().lower()
    if raw not in choices:
        return default
    return raw


# === Config Dataclasses ===

@dataclass
class CaptureConfig:
    """Capture adapter configuration."""
    backend: str = 'real'
    source: str = 'client'
    config_path: str = ''
    frame_max_age_ms: int = 500


@dataclass
class WindowConfig:
    """Window binding configuration."""
    hwnd: int = 0
    title_substring: str = ''
    try_focus: bool = True


@dataclass
class MarkerConfig:
    """Player marker detection configuration."""
    rgb: str = '255,0,255'
    tol: int = 30
    min_pixels: int = 5
    max_pixels: int = 0
    min_fill_ratio: float = 0.15
    max_aspect_ratio: float = 4.0
    pixels_per_tile: float = 1.0


@dataclass
class CavebotConfig:
    """Cavebot configuration."""
    enabled: bool = True
    max_attempts_per_waypoint: int = 3
    max_time_ms_per_waypoint: int = 5000
    max_ticks: int = 200
    max_ticks_per_waypoint: int = 20
    min_pixel_delta: int = 2


@dataclass
class TargetingConfig:
    """Targeting system configuration."""
    enabled: bool = False
    backend: str = 'real'
    battle_list_roi: str = 'battle_list'
    target_frame_roi: str = 'target_frame'
    max_attempts_per_target: int = 2
    max_time_ms_per_target: int = 2500


@dataclass
class HealingConfig:
    """Healing system configuration."""
    enabled: bool = False
    backend: str = 'real'
    hp_mp_roi: str = 'hp_mp'
    hp_threshold: float = 0.5
    mp_min: float = 0.0
    mp_cost: float = 0.0
    hp_increase_min: float = 0.02
    heal_key: str = 'F1'
    max_attempts: int = 2
    max_time_ms: int = 2500


@dataclass
class CombatConfig:
    """Combat system configuration."""
    enabled: bool = False
    backend: str = 'real'
    attack_key: str = 'SPACE'
    target_hp_decrease_min: float = 0.02


@dataclass
class LootingConfig:
    """Looting system configuration."""
    enabled: bool = False
    mode: Literal['premium', 'free'] = 'premium'
    quick_loot_key: str = 'R'
    max_attempts_per_corpse: int = 3
    max_ticks: int = 20
    require_inventory_delta: bool = True


@dataclass
class DepositConfig:
    """Deposit system configuration."""
    enabled: bool = False
    key: str = 'D'
    max_attempts: int = 3
    max_ticks: int = 20


@dataclass
class TradeConfig:
    """Trade system configuration."""
    enabled: bool = False
    action: Literal['buy', 'sell', 'deposit'] = 'buy'
    expected_npc_id: int = 1
    max_attempts: int = 3
    max_ticks: int = 20


@dataclass
class RuntimeSettings:
    """Main runtime settings loaded from environment variables."""

    # Mode and profile
    mode: Literal['mock', 'real'] = 'real'
    profile: Literal['prod_emergency', 'prod_full'] = 'prod_emergency'

    # Core settings
    tick_hz: float = 20.0
    session_seconds: float = 1.0
    max_ticks: int = 0

    # Sub-configs
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    marker: MarkerConfig = field(default_factory=MarkerConfig)
    cavebot: CavebotConfig = field(default_factory=CavebotConfig)
    targeting: TargetingConfig = field(default_factory=TargetingConfig)
    healing: HealingConfig = field(default_factory=HealingConfig)
    combat: CombatConfig = field(default_factory=CombatConfig)
    looting: LootingConfig = field(default_factory=LootingConfig)
    deposit: DepositConfig = field(default_factory=DepositConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)

    # Feature flags
    enable_cavebot: bool = True
    enable_targeting: bool = False
    enable_healing: bool = False
    enable_combat: bool = False

    # Bot config
    bot_config_path: str = ''

    # ROI names
    minimap_roi: str = 'minimap'
    battle_list_roi: str = 'battle_list'
    target_frame_roi: str = 'target_frame'
    hp_mp_roi: str = 'hp_mp'


def load_settings() -> RuntimeSettings:
    """Load runtime settings from environment variables.

    Returns a RuntimeSettings instance with all config populated from env vars.
    """
    # Mode and profile
    mode = cast(Literal['mock', 'real'], _env_choice('FRBOT_MODE', {'mock', 'real'}, 'real'))
    profile = cast(Literal['prod_emergency', 'prod_full'], _env_choice('FRBOT_PROFILE', {'prod_emergency', 'prod_full'}, 'prod_emergency'))

    # Core settings
    tick_hz = _env_float('FRBOT_TICK_HZ', 20.0)
    session_seconds = _env_float('FRBOT_SESSION_SECONDS', 1.0)
    max_ticks = _env_int('FRBOT_MAX_TICKS', 0)

    # Capture config
    capture = CaptureConfig(
        backend=_env_choice('FRBOT_CAPTURE_BACKEND', {'real', 'mock', 'mss', 'obs-source'}, 'real'),
        source=_env_choice('FRBOT_CAPTURE_SOURCE', {'client', 'obs', 'obs-projector'}, 'client'),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),
        frame_max_age_ms=_env_int('FRBOT_FRAME_MAX_AGE_MS', 500),
    )

    # Window config
    window = WindowConfig(
        hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
        try_focus=_env_bool('FRBOT_TRY_FOCUS', True),
    )

    # Marker config
    marker = MarkerConfig(
        rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,0,255'),
        tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 30),
        min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 5),
        max_pixels=_env_int('FRBOT_PLAYER_MARKER_MAX_PIXELS', 0),
        min_fill_ratio=_env_float('FRBOT_PLAYER_MARKER_MIN_FILL_RATIO', 0.15),
        max_aspect_ratio=_env_float('FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO', 4.0),
        pixels_per_tile=_env_float('FRBOT_PIXELS_PER_TILE', 1.0),
    )

    # Cavebot config
    cavebot = CavebotConfig(
        enabled=_env_bool('FRBOT_ENABLE_CAVEBOT', True),
        max_attempts_per_waypoint=_env_int('FRBOT_MAX_ATTEMPTS_PER_WAYPOINT', 3),
        max_time_ms_per_waypoint=_env_int('FRBOT_MAX_TIME_MS_PER_WAYPOINT', 5000),
        max_ticks=_env_int('FRBOT_CAVEBOT_MAX_TICKS', 200),
        max_ticks_per_waypoint=_env_int('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', 20),
        min_pixel_delta=_env_int('FRBOT_CAVEBOT_MIN_PIXEL_DELTA', 2),
    )

    # Targeting config
    targeting = TargetingConfig(
        enabled=_env_bool('FRBOT_ENABLE_TARGETING', False),
        backend=_env_choice('FRBOT_TARGETING_BACKEND', {'real', 'mock'}, 'real'),
        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        max_attempts_per_target=_env_int('FRBOT_MAX_ATTEMPTS_PER_TARGET', 2),
        max_time_ms_per_target=_env_int('FRBOT_MAX_TIME_MS_PER_TARGET', 2500),
    )

    # Healing config
    healing = HealingConfig(
        enabled=_env_bool('FRBOT_ENABLE_HEALING', False),
        backend=_env_choice('FRBOT_HEALING_BACKEND', {'real', 'mock'}, 'real'),
        hp_mp_roi=_env_str('FRBOT_HP_MP_ROI', 'hp_mp'),
        hp_threshold=_env_float('FRBOT_HEAL_HP_THRESHOLD', 0.5),
        mp_min=_env_float('FRBOT_HEAL_MP_MIN', 0.0),
        mp_cost=_env_float('FRBOT_HEAL_MP_COST', 0.0),
        hp_increase_min=_env_float('FRBOT_HEAL_HP_INCREASE_MIN', 0.02),
        heal_key=_env_str('FRBOT_HEAL_KEY', 'F1'),
        max_attempts=_env_int('FRBOT_MAX_ATTEMPTS_PER_HEAL', 2),
        max_time_ms=_env_int('FRBOT_MAX_TIME_MS_PER_HEAL', 2500),
    )

    # Combat config
    combat = CombatConfig(
        enabled=_env_bool('FRBOT_ENABLE_COMBAT', False),
        backend=_env_choice('FRBOT_COMBAT_BACKEND', {'real', 'mock'}, 'real'),
        attack_key=_env_str('FRBOT_ATTACK_KEY', 'SPACE'),
        target_hp_decrease_min=_env_float('FRBOT_COMBAT_HP_DECREASE_MIN', 0.02),
    )

    # Looting config
    looting = LootingConfig(
        enabled=_env_bool('FRBOT_ENABLE_LOOTING', False),
        mode=cast(Literal['premium', 'free'], _env_choice('FRBOT_LOOTING_MODE', {'premium', 'free'}, 'premium')),
        quick_loot_key=_env_str('FRBOT_QUICK_LOOT_KEY', 'R'),
        max_attempts_per_corpse=_env_int('FRBOT_LOOTING_MAX_ATTEMPTS', 3),
        max_ticks=_env_int('FRBOT_LOOTING_MAX_TICKS', 20),
        require_inventory_delta=_env_bool('FRBOT_LOOTING_REQUIRE_DELTA', True),
    )

    # Deposit config
    deposit = DepositConfig(
        enabled=_env_bool('FRBOT_ENABLE_DEPOSIT', False),
        key=_env_str('FRBOT_DEPOSIT_KEY', 'D'),
        max_attempts=_env_int('FRBOT_DEPOSIT_MAX_ATTEMPTS', 3),
        max_ticks=_env_int('FRBOT_DEPOSIT_MAX_TICKS', 20),
    )

    # Trade config
    trade = TradeConfig(
        enabled=_env_bool('FRBOT_ENABLE_TRADE', False),
        action=cast(Literal['buy', 'sell', 'deposit'], _env_choice('FRBOT_TRADE_ACTION', {'buy', 'sell', 'deposit'}, 'buy')),
        expected_npc_id=_env_int('FRBOT_TRADE_NPC_ID', 1),
        max_attempts=_env_int('FRBOT_TRADE_MAX_ATTEMPTS', 3),
        max_ticks=_env_int('FRBOT_TRADE_MAX_TICKS', 20),
    )

    return RuntimeSettings(
        mode=mode,
        profile=profile,
        tick_hz=tick_hz,
        session_seconds=session_seconds,
        max_ticks=max_ticks,
        capture=capture,
        window=window,
        marker=marker,
        cavebot=cavebot,
        targeting=targeting,
        healing=healing,
        combat=combat,
        looting=looting,
        deposit=deposit,
        trade=trade,
        enable_cavebot=_env_bool('FRBOT_ENABLE_CAVEBOT', True),
        enable_targeting=_env_bool('FRBOT_ENABLE_TARGETING', False),
        enable_healing=_env_bool('FRBOT_ENABLE_HEALING', False),
        enable_combat=_env_bool('FRBOT_ENABLE_COMBAT', False),
        bot_config_path=_env_str('FRBOT_BOT_CONFIG_PATH', ''),
        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),
        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        hp_mp_roi=_env_str('FRBOT_HP_MP_ROI', 'hp_mp'),
    )


def get_setting(key: str, default: str = '') -> str:
    """Convenience function to get a single setting as string."""
    return _env_str(f'FRBOT_{key.upper()}', default)
