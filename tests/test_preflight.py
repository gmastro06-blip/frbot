from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_real_mode_aborts_fast_and_writes_fatal(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_TICK_HZ', '20')
    code = run()
    assert code == 1
    assert fatal.exists()
    text = fatal.read_text(encoding='utf-8', errors='replace').lower()
    # Real mode must abort if real adapters are not available/verified.
    assert (
        'missing dependency' in text
        or 'not verified' in text
        or 'verify failed' in text
    )


def test_mock_mode_runs_deterministically(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    code = run()
    assert code == 0
