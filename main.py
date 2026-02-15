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
from runtime.runner import run


load_repo_env()


def _mode() -> str:
    return (os.environ.get('FRBOT_MODE', 'real') or 'real').strip().lower()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def main() -> int:
    mode = _mode()

    # CI and unit tests run in mock mode across platforms.
    # PROD-EMERGENCY constraints apply only to REAL execution.
    if mode == 'mock':
        return int(run())

    # Windows-only (PROD-EMERGENCY): abort immediately with fatal.log.
    if sys.platform != 'win32':
        write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
        return 1

    # Supported PROD profiles: prod_emergency (default) and prod_full.
    # If prod_full is explicitly requested, force it for the run.
    if mode == 'prod_full':
        os.environ['FRBOT_PROFILE'] = 'prod_full'
    else:
        prof = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if prof not in {'prod_emergency', 'prod_full'}:
            os.environ['FRBOT_PROFILE'] = 'prod_emergency'

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

    # Hard-disabled feature modes (no routes, no execution).
    if mode in {'combat', 'looting', 'deposit', 'trade'}:
        write_fatal('feature_disabled', details={'feature': mode})
        print('REAL_CAVEBOT_FAILED:feature_disabled')
        return 1

    if mode == 'prod_full':
        # Full certification pipeline: fail fast on first gate failure.
        from targeting_full_entrypoint import run_targeting_full_only
        from healing_full_entrypoint import run_healing_full_only
        from combat_full_entrypoint import run_combat_full_only
        from cavebot_full_entrypoint import run_cavebot_full_only
        from looting_full_entrypoint import run_looting_full_only
        from deposit_full_entrypoint import run_deposit_full_only
        from trade_full_entrypoint import run_trade_full_only

        code = int(run_targeting_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:targeting_full_failed')
            return code

        code = int(run_healing_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:healing_full_failed')
            return code

        code = int(run_combat_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:combat_full_failed')
            return code

        code = int(run_cavebot_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:cavebot_full_failed')
            return code

        code = int(run_looting_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:looting_full_failed')
            return code

        code = int(run_deposit_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:deposit_full_failed')
            return code

        code = int(run_trade_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:trade_full_failed')
            return code

        print('REAL_CAVEBOT_OK')
        return 0

    if mode == 'combat_basic':
        from combat_basic_entrypoint import run_combat_basic_only

        code = int(run_combat_basic_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:combat_basic_failed')
        return code

    if mode == 'looting_basic':
        from looting_basic_entrypoint import run_looting_basic_only

        code = int(run_looting_basic_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:looting_basic_failed')
        return code

    if mode == 'looting_full':
        from looting_full_entrypoint import run_looting_full_only

        code = int(run_looting_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:looting_full_failed')
        return code

    if mode == 'deposit_basic':
        from deposit_basic_entrypoint import run_deposit_basic_only

        code = int(run_deposit_basic_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:deposit_basic_failed')
        return code

    if mode == 'trade_basic':
        from trade_basic_entrypoint import run_trade_basic_only

        code = int(run_trade_basic_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:trade_basic_failed')
        return code

    if mode == 'targeting':
        from targeting_entrypoint import run_targeting_only

        code = int(run_targeting_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:targeting_failed')
        return code
    if mode == 'targeting_full':
        from targeting_full_entrypoint import run_targeting_full_only

        code = int(run_targeting_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:targeting_full_failed')
        return code
    if mode == 'cavebot':
        from cavebot_entrypoint import run_cavebot_only

        code = int(run_cavebot_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:cavebot_failed')
        return code
    if mode == 'cavebot_full':
        from cavebot_full_entrypoint import run_cavebot_full_only

        code = int(run_cavebot_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:cavebot_full_failed')
        return code
    if mode == 'healing':
        from healing_entrypoint import run_healing_only

        code = int(run_healing_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:healing_failed')
        return code
    if mode == 'healing_full':
        from healing_full_entrypoint import run_healing_full_only

        code = int(run_healing_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:healing_full_failed')
        return code

    if mode == 'combat_full':
        from combat_full_entrypoint import run_combat_full_only

        code = int(run_combat_full_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:combat_full_failed')
        return code

    code = int(run())

    # Automatic result marker for PROD-EMERGENCY REAL cavebot certification.
    if code == 0:
        print('REAL_CAVEBOT_OK')
        return 0

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
