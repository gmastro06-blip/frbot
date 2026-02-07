from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_window_binding_lost_abort(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')

    # Simulate losing binding.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '0')

    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'window_binding_lost' in fatal.read_text(encoding='utf-8', errors='replace').lower()
