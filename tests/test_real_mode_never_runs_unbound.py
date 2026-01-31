from __future__ import annotations

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

    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'window_binding_lost' in fatal.read_text(encoding='utf-8', errors='replace').lower()
