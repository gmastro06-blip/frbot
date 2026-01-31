from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_abort_when_minimap_missing_in_config(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    cfg_path = tmp_path / 'rois.json'
    cfg_path.write_text(json.dumps({'rois': {'not_minimap': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}}), encoding='utf-8')

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', str(cfg_path))
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')

    code = run()
    assert code == 1
    assert fatal.exists()
    text = fatal.read_text(encoding='utf-8', errors='replace').lower()
    assert 'minimap_not_detected' in text
