from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_real_mode_aborts_fast(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_TICK_HZ', '20')
    code = run()
    assert code == 1
    assert (Path('diagnostics') / 'fatal.log').exists()


def test_mock_mode_runs(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    code = run()
    assert code == 0
