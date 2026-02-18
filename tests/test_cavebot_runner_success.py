from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_runner_mock_mode_succeeds(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    # Change to tmp_path to avoid polluting project directory
    import os
    monkeypatch.chdir(tmp_path)

    # Create diagnostics directory in temp path
    (tmp_path / 'diagnostics').mkdir(exist_ok=True)
    fatal = tmp_path / 'diagnostics' / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.delenv('FRBOT_CONFIG_PATH', raising=False)

    code = run()
    assert code == 0, f"Expected code 0, got {code}"
    assert not fatal.exists(), f"fatal.log should not exist but does: {fatal.read_text() if fatal.exists() else ''}"
