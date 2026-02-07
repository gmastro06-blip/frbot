from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from runtime.runner import run


def test_focus_loss_does_not_abort_without_input(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Losing foreground/focus must not abort a capture-only tick.

    Contract: window binding is only enforced at startup guards or immediately before emitting input.
    """

    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    # Make the test hermetic: provide a minimal ROI config and run from an
    # isolated working directory.
    monkeypatch.chdir(tmp_path)
    cfg = {
        'rois': {
            'minimap': {'x': 2, 'y': 2, 'width': 64, 'height': 64},
        }
    }
    rois_path = tmp_path / 'rois.json'
    rois_path.write_text(json.dumps(cfg), encoding='utf-8')

    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', str(rois_path))
    monkeypatch.setenv('FRBOT_TICK_HZ', '50')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')

    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    # Disable cavebot so the engine emits no intents (no input in the tick).
    monkeypatch.setenv('FRBOT_ENABLE_CAVEBOT', '0')
    # Keep the run short.
    monkeypatch.setenv('FRBOT_SESSION_SECONDS', '0.02')

    # Simulate user alt-tabbing away (foreground loss) while capture continues.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '0')

    code = run()
    assert code == 0
    if fatal.exists():
        txt = fatal.read_text(encoding='utf-8', errors='replace').lower()
        assert 'window_binding_lost' not in txt
