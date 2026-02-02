from __future__ import annotations

"""Convenience shim.

Allows running:
  python test_capture_real.py ...
Which forwards to tools/test_capture_real.py.
"""

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
TOOLS_SCRIPT = REPO_ROOT / 'tools' / 'test_capture_real.py'


def main() -> int:
    # Ensure repo root is importable (mirrors the tools script behavior).
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    if not TOOLS_SCRIPT.exists():
        sys.stderr.write('tools/test_capture_real.py not found\n')
        return 2

    # Execute the tools script as if it were run directly.
    import runpy

    runpy.run_path(str(TOOLS_SCRIPT), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
