from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_module(repo_root: Path) -> Any:
    path = repo_root / "tools" / "validate_core_features.py"
    spec = importlib.util.spec_from_file_location("validate_core_features", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_validate_core_mock_fail_fast_on_targeting(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)

    rc = mod.run_validate_core(["--mode", "mock", "--mock-battle-rows", ""])
    assert rc == 2

    validate_root = tmp_path / "diagnostics" / "validate"
    runs = sorted(validate_root.glob("*"))
    assert runs
    out_dir = runs[-1]

    summary = json.loads((out_dir / "validate_summary.json").read_text(encoding="utf-8"))
    final = str(summary.get("final_decision", ""))
    assert final.startswith("NOT_OPERATIONAL:")
    assert "gate_failed:targeting_full:" in final

    gates = summary.get("gates", [])
    assert isinstance(gates, list)
    assert len(gates) == 0
