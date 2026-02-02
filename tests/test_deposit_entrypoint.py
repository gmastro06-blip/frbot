from __future__ import annotations

from pathlib import Path

from deposit_entrypoint import run_deposit_only

import pytest


def test_deposit_feature_is_hard_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FRBOT_MODE', 'deposit')

    assert run_deposit_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal
