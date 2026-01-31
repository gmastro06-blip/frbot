from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_abort_when_capture_not_verified(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '0')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'capture not verified' in fatal.read_text(encoding='utf-8', errors='replace')


def test_abort_when_input_not_verified(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '0')
    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'input not verified' in fatal.read_text(encoding='utf-8', errors='replace')
