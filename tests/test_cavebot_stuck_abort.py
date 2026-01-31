from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_runner_aborts_when_stuck_three_times(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_STUCK', '1')
    monkeypatch.setenv('FRBOT_MOCK_MINIMAP_NOISE', '1')
    monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

    code = run()
    assert code == 1
    assert fatal.exists()
    text = fatal.read_text(encoding='utf-8', errors='replace').lower()
    assert 'no_semantic_progress' in text
