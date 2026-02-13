from __future__ import annotations

import sys
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_real_mode_never_runs_unbound(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_TICK_HZ', '20')

    # Provide no window selector; binding must fail BEFORE any capture/input dependencies.
    monkeypatch.delenv('FRBOT_WINDOW_TITLE', raising=False)
    monkeypatch.delenv('FRBOT_WINDOW_HWND', raising=False)

    # Hermetic: developer shells may have projector/capture env set.
    monkeypatch.delenv('FRBOT_CAPTURE_BACKEND', raising=False)
    monkeypatch.delenv('FRBOT_CAPTURE_TARGET', raising=False)
    monkeypatch.delenv('FRBOT_PROJECTOR_WINDOW_TITLE', raising=False)
    monkeypatch.delenv('FRBOT_PROJECTOR_WINDOW_HWND', raising=False)
    monkeypatch.delenv('FRBOT_PROJECTOR_REQUIRE_FOREGROUND', raising=False)

    code = run()
    assert code == 1
    assert fatal.exists()
    msg = fatal.read_text(encoding='utf-8', errors='replace').lower()
    if sys.platform == 'win32':
        assert 'window_binding_lost' in msg
    else:
        assert 'unsupported_platform' in msg
