from __future__ import annotations

# === BASELINE STABLE ===
# Do not modify control-flow/contracts here without a contract review.
# Extend only by adding new independent gates. See BASELINE.md.

import os
import sys
import json
from pathlib import Path

from diagnostics.fatal import write_fatal
from runtime.runner import run


def _mode() -> str:
    return (os.environ.get('FRBOT_MODE', 'real') or 'real').strip().lower()


def main() -> int:
    # Windows-only (PROD-EMERGENCY): abort immediately with fatal.log.
    if sys.platform != 'win32':
        write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
        return 1

    # Fixed profile: always PROD-EMERGENCY; ignore external overrides.
    os.environ['FRBOT_PROFILE'] = 'prod_emergency'

    # PROD-EMERGENCY STARTUP GUARDS: must be REAL + HWND-bound + foreground.
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
    if _mode() in {'combat', 'looting', 'deposit', 'trade'}:
        write_fatal('feature_disabled', details={'feature': _mode()})
        print('REAL_CAVEBOT_FAILED:feature_disabled')
        return 1

    if _mode() == 'targeting':
        from targeting_entrypoint import run_targeting_only

        code = int(run_targeting_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:targeting_failed')
        return code
    if _mode() == 'cavebot':
        from cavebot_entrypoint import run_cavebot_only

        code = int(run_cavebot_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:cavebot_failed')
        return code
    if _mode() == 'healing':
        from healing_entrypoint import run_healing_only

        code = int(run_healing_only())
        if code != 0:
            print('REAL_CAVEBOT_FAILED:healing_failed')
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
