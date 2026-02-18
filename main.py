from __future__ import annotations

# === BASELINE STABLE ===
# Do not modify control-flow/contracts here without a contract review.
# Extend only by adding new independent gates. See BASELINE.md.

import os
import sys
import json
from pathlib import Path

from diagnostics.fatal import write_fatal
from runtime.env_bootstrap import load_repo_env
from runtime.env_utils import env_str, env_choice, env_bool
from runtime.entrypoint_registry import get_registry, run_prod_full_pipeline


load_repo_env()


def _mode() -> str:
    return env_str('FRBOT_MODE', 'real').strip().lower()


def _profile() -> str:
    """Resolve the effective profile."""
    mode = _mode()

    # prod_full mode explicitly sets the profile
    if mode == 'prod_full':
        return 'prod_full'

    # Otherwise use FRBOT_PROFILE or default to prod_emergency
    profile = env_choice('FRBOT_PROFILE', {'prod_emergency', 'prod_full'}, 'prod_emergency')
    return profile


def main() -> int:
    mode = _mode()
    profile = _profile()

    # CI and unit tests run in mock mode across platforms.
    # PROD-EMERGENCY constraints apply only to REAL execution.
    if mode == 'mock':
        from runtime.runner import run
        return int(run())

    # Windows-only (PROD-EMERGENCY): abort immediately with fatal.log.
    if sys.platform != 'win32':
        write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
        return 1

    # Set the profile in environment for downstream code
    os.environ['FRBOT_PROFILE'] = profile

    # PROD STARTUP GUARDS: must be REAL + HWND-bound + foreground + OBS source identity.
    # (Hard-stop here, before any runners start.)
    from runtime.startup_guards import enforce_prod_emergency_real_startup_guards

    try:
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=True)
    except Exception:
        # Automatic result marker (no human confirmation).
        try:
            fatal_p = Path('diagnostics') / 'fatal.log'
            if fatal_p.exists():
                rec = json.loads(fatal_p.read_text(encoding='utf-8', errors='replace').strip() or '{}')
                reason = str(rec.get('reason') or 'unknown')
            else:
                reason = 'unknown'
        except Exception:
            reason = 'unknown'
        print(f"REAL_CAVEBOT_FAILED:{reason}")
        return 1

    # Get the registry and look up the entrypoint
    registry = get_registry()

    # Handle prod_full pipeline specially
    if mode == 'prod_full':
        return run_prod_full_pipeline()

    # Get entrypoint for the current mode
    ep = registry.get(mode)

    if ep is None:
        available_modes = [e.name for e in registry.all()]
        write_fatal('invalid_mode', details={'mode': mode, 'available': available_modes})
        print(f'REAL_CAVEBOT_FAILED:invalid_mode')
        return 1

    # Check if entrypoint is enabled
    if not registry.is_enabled(mode):
        write_fatal('feature_disabled', details={'feature': mode})
        print('REAL_CAVEBOT_FAILED:feature_disabled')
        return 1

    # Run the entrypoint
    code = int(ep.runner())

    # Automatic result marker for PROD-EMERGENCY REAL cavebot certification.
    if code == 0:
        print('REAL_CAVEBOT_OK')
        return 0

    # Extract reason from fatal.log if available
    try:
        fatal_p = Path('diagnostics') / 'fatal.log'
        if fatal_p.exists():
            rec = json.loads(fatal_p.read_text(encoding='utf-8', errors='replace').strip() or '{}')
            reason = str(rec.get('reason') or 'unknown')
        else:
            reason = 'unknown'
    except Exception:
        reason = 'unknown'

    print(f"REAL_CAVEBOT_FAILED:{reason}")
    return int(code)


if __name__ == '__main__':
    raise SystemExit(main())
