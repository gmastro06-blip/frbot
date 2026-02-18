"""Entry point registry for mode-based routing.

Replaces the large if/elif chain in main.py with a declarative registry pattern.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

from runtime.env_utils import env_choice


@dataclass
class Entrypoint:
    """Represents a single executable mode."""
    name: str                          # e.g., 'targeting', 'combat_basic'
    runner: Callable[[], int]          # Function that executes this mode
    required_profile: str | None = None  # 'prod_emergency' | 'prod_full' | None = any
    required_platform: str | None = 'win32'  # 'win32' | None = any
    enabled: Callable[[], bool] | None = None  # Dynamic enable check


@dataclass
class EntrypointRegistry:
    """Registry for all available entrypoints."""
    _entrypoints: dict[str, Entrypoint] = field(default_factory=dict)

    def register(self, entrypoint: Entrypoint) -> None:
        """Register an entrypoint."""
        self._entrypoints[entrypoint.name] = entrypoint

    def get(self, mode: str) -> Entrypoint | None:
        """Get entrypoint by mode name."""
        return self._entrypoints.get(mode)

    def all(self) -> list[Entrypoint]:
        """Get all registered entrypoints."""
        return list(self._entrypoints.values())

    def is_enabled(self, mode: str) -> bool:
        """Check if a mode is currently enabled."""
        ep = self.get(mode)
        if ep is None:
            return False
        if ep.required_platform is not None and sys.platform != ep.required_platform:
            return False
        if ep.required_profile is not None:
            profile = env_choice('FRBOT_PROFILE', {'prod_emergency', 'prod_full'}, 'prod_emergency')
            if profile != ep.required_profile:
                return False
        if ep.enabled is not None and not ep.enabled():
            return False
        return True


# Global registry instance
_registry = EntrypointRegistry()


def get_registry() -> EntrypointRegistry:
    """Get the global entrypoint registry."""
    return _registry


def register(ep: Entrypoint) -> None:
    """Convenience function to register an entrypoint."""
    _registry.register(ep)


# === Built-in mode checks ===

def _combat_looting_deposit_trade_disabled() -> bool:
    """Check if combat/looting/deposit/trade modes are disabled in prod_emergency."""
    mode = env_choice('FRBOT_MODE', {'mock', 'real', 'prod_emergency', 'prod_full',
                                      'targeting', 'healing', 'combat', 'looting',
                                      'deposit', 'trade', 'cavebot'}, 'real')
    profile = env_choice('FRBOT_PROFILE', {'prod_emergency', 'prod_full'}, 'prod_emergency')

    # In prod_emergency, these features are hard-disabled
    if profile == 'prod_emergency' and mode in {'combat', 'looting', 'deposit', 'trade'}:
        return False  # Disabled
    return True  # Enabled


# === Registry of all modes ===

def _import_entrypoints() -> None:
    """Import and register all entrypoints.

    This is called once at startup to populate the registry.
    """
    global _registry

    # Lazy imports to avoid circular dependencies
    from targeting_entrypoint import run_targeting_only
    from targeting_full_entrypoint import run_targeting_full_only
    from healing_entrypoint import run_healing_only
    from healing_full_entrypoint import run_healing_full_only
    from combat_entrypoint import run_combat_only
    from combat_full_entrypoint import run_combat_full_only
    from combat_basic_entrypoint import run_combat_basic_only
    from cavebot_entrypoint import run_cavebot_only
    from cavebot_full_entrypoint import run_cavebot_full_only
    from looting_entrypoint import run_looting_only
    from looting_full_entrypoint import run_looting_full_only
    from looting_basic_entrypoint import run_looting_basic_only
    from deposit_entrypoint import run_deposit_only
    from deposit_full_entrypoint import run_deposit_full_only
    from deposit_basic_entrypoint import run_deposit_basic_only
    from trade_entrypoint import run_trade_only
    from trade_full_entrypoint import run_trade_full_only
    from trade_basic_entrypoint import run_trade_basic_only
    from runtime.runner import run as run_default

    # Register all entrypoints
    _registry.register(Entrypoint(
        name='mock',
        runner=run_default,
        required_platform=None,  # Works on any platform
    ))

    _registry.register(Entrypoint(
        name='real',
        runner=run_default,
    ))

    # 'real' mode uses the default runner (same as mock but with real adapters)
    _registry.register(Entrypoint(
        name='real',
        runner=run_default,
    ))

    _registry.register(Entrypoint(
        name='targeting',
        runner=run_targeting_only,
    ))

    _registry.register(Entrypoint(
        name='targeting_full',
        runner=run_targeting_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='healing',
        runner=run_healing_only,
    ))

    _registry.register(Entrypoint(
        name='healing_full',
        runner=run_healing_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='combat',
        runner=run_combat_only,
        enabled=_combat_looting_deposit_trade_disabled,
    ))

    _registry.register(Entrypoint(
        name='combat_full',
        runner=run_combat_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='combat_basic',
        runner=run_combat_basic_only,
    ))

    _registry.register(Entrypoint(
        name='cavebot',
        runner=run_cavebot_only,
    ))

    _registry.register(Entrypoint(
        name='cavebot_full',
        runner=run_cavebot_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='looting',
        runner=run_looting_only,
        enabled=_combat_looting_deposit_trade_disabled,
    ))

    _registry.register(Entrypoint(
        name='looting_full',
        runner=run_looting_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='looting_basic',
        runner=run_looting_basic_only,
    ))

    _registry.register(Entrypoint(
        name='deposit',
        runner=run_deposit_only,
        enabled=_combat_looting_deposit_trade_disabled,
    ))

    _registry.register(Entrypoint(
        name='deposit_full',
        runner=run_deposit_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='deposit_basic',
        runner=run_deposit_basic_only,
    ))

    _registry.register(Entrypoint(
        name='trade',
        runner=run_trade_only,
        enabled=_combat_looting_deposit_trade_disabled,
    ))

    _registry.register(Entrypoint(
        name='trade_full',
        runner=run_trade_full_only,
        required_profile='prod_full',
    ))

    _registry.register(Entrypoint(
        name='trade_basic',
        runner=run_trade_basic_only,
    ))


# === Profile-based pipelines ===

def run_prod_full_pipeline() -> int:
    """Run the full prod_full certification pipeline.

    Returns exit code: 0 = all gates passed, non-zero = first failure.
    """
    from diagnostics.fatal import write_fatal

    gates = [
        ('targeting_full', 'targeting_full_failed'),
        ('healing_full', 'healing_full_failed'),
        ('combat_full', 'combat_full_failed'),
        ('cavebot_full', 'cavebot_full_failed'),
        ('looting_full', 'looting_full_failed'),
        ('deposit_full', 'deposit_full_failed'),
        ('trade_full', 'trade_full_failed'),
    ]

    for mode, fail_msg in gates:
        ep = _registry.get(mode)
        if ep is None:
            write_fatal('entrypoint_not_found', details={'mode': mode})
            sys.stdout.write(f'REAL_CAVEBOT_FAILED:{fail_msg}\n')
            return 1

        code = ep.runner()
        if code != 0:
            sys.stdout.write(f'REAL_CAVEBOT_FAILED:{fail_msg}\n')
            return code

    sys.stdout.write('REAL_CAVEBOT_OK\n')
    return 0


# Initialize registry on import
_import_entrypoints()
