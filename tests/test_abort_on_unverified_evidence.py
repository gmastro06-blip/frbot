from __future__ import annotations

from pathlib import Path

import pytest

from combat_entrypoint import run_combat_only
from looting_entrypoint import run_looting_only
from trade_entrypoint import run_trade_only


def test_disabled_features_abort_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'trade')
    assert run_trade_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal

    monkeypatch.delenv('FRBOT_MODE', raising=False)

    (tmp_path / 'diagnostics' / 'fatal.log').unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'combat')
    assert run_combat_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal

    monkeypatch.delenv('FRBOT_MODE', raising=False)

    (tmp_path / 'diagnostics' / 'fatal.log').unlink(missing_ok=True)

    monkeypatch.setenv('FRBOT_MODE', 'looting')
    assert run_looting_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal
