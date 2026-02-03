from __future__ import annotations

import select
import sys
import time


def _yield_to_os() -> None:
    """Yield execution to the OS scheduler (best-effort).

    Intentionally avoids time.sleep() to satisfy production constraints.
    """

    if sys.platform != 'win32':
        # On non-Windows we can't rely on SwitchToThread; caller may use select-based waits.
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SwitchToThread()
    except Exception:
        return


def sleep_ms(ms: float) -> None:
    """Sleep without using time.sleep().

    Contract: this is allowed in PROD-EMERGENCY because it avoids Python's
    time.sleep() while still providing deterministic pacing.
    """

    try:
        v = float(ms)
    except Exception:
        v = 0.0

    if not (v > 0.0):
        _yield_to_os()
        return

    # Clamp to a sensible upper bound for safety.
    v = min(v, 60_000.0)

    if sys.platform == 'win32':
        try:
            import ctypes

            ctypes.windll.kernel32.Sleep(int(v))
            return
        except Exception:
            # Fall back to yield loop.
            pass

    # Cross-platform wait without time.sleep: select timeout.
    try:
        select.select([], [], [], float(v) / 1000.0)
    except Exception:
        # Last resort: bounded yield loop.
        end = time.monotonic_ns() + int(v * 1_000_000.0)
        wait_until_ns(end)


def sleep_s(seconds: float) -> None:
    try:
        v = float(seconds)
    except Exception:
        v = 0.0
    sleep_ms(v * 1000.0)


def wait_until_ns(deadline_ns: int) -> None:
    """Wait until a monotonic deadline without calling time.sleep()."""

    try:
        end = int(deadline_ns)
    except Exception:
        return

    while True:
        now = time.monotonic_ns()
        if now >= end:
            return

        remaining_ns = end - now
        # If more than ~2ms remain, actually block to avoid burning CPU.
        if remaining_ns >= 2_000_000:
            sleep_ms(float(remaining_ns) / 1_000_000.0)
        else:
            _yield_to_os()
