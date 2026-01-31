from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_runner_mock_mode_succeeds(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

    code = run()
    assert code == 0
    assert not fatal.exists()
