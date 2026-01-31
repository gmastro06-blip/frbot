from __future__ import annotations

import pytest
from _pytest.monkeypatch import MonkeyPatch

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.preflight import preflight


def test_never_enters_running_without_verified_adapters(monkeypatch: MonkeyPatch) -> None:
    # capture fails -> preflight raises -> caller must abort.
    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '0')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')

    ctx = RuntimeContext(
        config=RuntimeConfig(mode='mock'),
        status=RuntimeStatus(),
        telemetry=RuntimeTelemetry(),
    )
    with pytest.raises(PreflightFailed):
        preflight(ctx)
    assert ctx.status.state in {RuntimeState.PREFLIGHT, RuntimeState.INIT}
