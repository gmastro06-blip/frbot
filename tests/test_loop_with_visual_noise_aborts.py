from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_loop_with_visual_noise_aborts(monkeypatch: MonkeyPatch) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '120')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

    # Noise present but movement keys become no-ops: would previously loop if hashes were accepted.
    monkeypatch.setenv('FRBOT_MOCK_STUCK', '1')
    monkeypatch.setenv('FRBOT_MOCK_MINIMAP_NOISE', '1')

    code = run()
    assert code == 1
    assert fatal.exists()
    assert 'no_semantic_progress' in fatal.read_text(encoding='utf-8', errors='replace').lower()
