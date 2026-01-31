from __future__ import annotations

import os

from runtime.runner import run


def _mode() -> str:
    return (os.environ.get('FRBOT_MODE', 'real') or 'real').strip().lower()


def main() -> int:
    if _mode() == 'targeting':
        from targeting_entrypoint import run_targeting_only

        return run_targeting_only()
    if _mode() == 'cavebot':
        from cavebot_entrypoint import run_cavebot_only

        return run_cavebot_only()
    if _mode() == 'healing':
        from healing_entrypoint import run_healing_only

        return run_healing_only()
    if _mode() == 'combat':
        from combat_entrypoint import run_combat_only

        return run_combat_only()
    if _mode() == 'looting':
        from looting_entrypoint import run_looting_only

        return run_looting_only()
    if _mode() == 'deposit':
        from deposit_entrypoint import run_deposit_only

        return run_deposit_only()
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
