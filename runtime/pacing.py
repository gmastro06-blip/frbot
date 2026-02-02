from __future__ import annotations

import sys
import time


def _yield_to_os() -> None:
    """Yield execution to the OS scheduler (best-effort).

    Intentionally avoids time.sleep() to satisfy production constraints.
    """

    if sys.platform != 'win32':
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SwitchToThread()
    except Exception:
        return


def wait_until_ns(deadline_ns: int) -> None:
    """Wait until a monotonic deadline without calling time.sleep()."""

    try:
        end = int(deadline_ns)
    except Exception:
        return

    while time.monotonic_ns() < end:
        _yield_to_os()
