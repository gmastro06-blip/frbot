from __future__ import annotations

# === BASELINE STABLE ===
# Do not modify control-flow/contracts here without a contract review.
# Extend only by adding new independent gates. See BASELINE.md.

import os
import sys

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
        return 1

    # Hard-disabled feature modes (no routes, no execution).
    if _mode() in {'combat', 'looting', 'deposit', 'trade'}:
        write_fatal('feature_disabled', details={'feature': _mode()})
        return 1

    if _mode() == 'targeting':
        from targeting_entrypoint import run_targeting_only

        return run_targeting_only()
    if _mode() == 'cavebot':
        from cavebot_entrypoint import run_cavebot_only

        return run_cavebot_only()
    if _mode() == 'healing':
        from healing_entrypoint import run_healing_only

        return run_healing_only()
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
