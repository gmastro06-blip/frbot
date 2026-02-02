from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_minimap_delta_without_position_change_aborts(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

    # Visual deltas (noise) but no marker movement.
    monkeypatch.setenv('FRBOT_MOCK_STUCK', '1')
    monkeypatch.setenv('FRBOT_MOCK_MINIMAP_NOISE', '1')

    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'no_semantic_progress' in fatal.read_text(encoding='utf-8', errors='replace').lower()
