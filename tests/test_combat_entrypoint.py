from __future__ import annotations

from pathlib import Path

import pytest

from combat_entrypoint import run_combat_only


def test_combat_feature_is_hard_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FRBOT_MODE', 'combat')

    assert run_combat_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal
