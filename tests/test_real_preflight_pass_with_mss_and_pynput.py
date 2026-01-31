from __future__ import annotations

import importlib.util

import pytest

from runtime.runner import run


@pytest.mark.skipif(importlib.util.find_spec('mss') is None, reason='mss not installed')
@pytest.mark.skipif(importlib.util.find_spec('pynput') is None, reason='pynput not installed')
def test_real_preflight_passes_when_deps_present(monkeypatch):
    monkeypatch.setenv('FRBOT_MODE', 'real')
    # If this environment cannot verify capture/input, real mode must abort.
    # This test asserts the "can run" path only when verification succeeds.
    code = run()
    assert code == 0
