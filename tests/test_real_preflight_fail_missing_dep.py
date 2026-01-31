from __future__ import annotations

import importlib.util
from pathlib import Path

from runtime.runner import run


def test_real_preflight_fails_when_missing_deps(monkeypatch):
    has_mss = importlib.util.find_spec('mss') is not None
    has_pynput = importlib.util.find_spec('pynput') is not None
    if has_mss and has_pynput:
        # This test is specifically about the missing-dependency abort path.
        return

    fatal = Path('diagnostics') / 'fatal.log'
    fatal.unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'real')
    code = run()
    assert code == 1
    assert fatal.exists()
    text = fatal.read_text(encoding='utf-8', errors='replace').lower()
    assert 'missing dependency' in text
