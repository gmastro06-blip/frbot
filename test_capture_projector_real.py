"""Convenience shim for running the projector capture diagnostic.

This lets you run:
  poetry run python test_capture_projector_real.py ...
from the repository root.

The implementation lives in tools/test_capture_projector_real.py.
"""

from __future__ import annotations

from tools.test_capture_projector_real import main


if __name__ == '__main__':
    raise SystemExit(main())
