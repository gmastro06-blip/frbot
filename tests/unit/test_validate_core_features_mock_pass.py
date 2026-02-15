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


def test_validate_core_mock_pass(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)

    rc = mod.run_validate_core(["--mode", "mock"])
    assert rc == 0

    validate_root = tmp_path / "diagnostics" / "validate"
    runs = sorted(validate_root.glob("*"))
    assert runs
    out_dir = runs[-1]

    summary_path = out_dir / "validate_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["final_decision"] == "OPERATIONAL"
    gates = summary.get("gates", [])
    assert isinstance(gates, list)
    assert len(gates) == 4

    names = {str(g.get("gate")) for g in gates}
    assert names == {"targeting_full", "healing_full", "cavebot_full", "waypoints"}

    for gate in gates:
        assert bool(gate.get("ok")) is True
        assert isinstance(gate.get("reason"), str) and str(gate.get("reason")).strip()
        assert isinstance(gate.get("evidence_kind"), str) and str(gate.get("evidence_kind")).strip()
        assert isinstance(gate.get("inputs_sent"), int)
        assert isinstance(gate.get("before_ppm"), str) and str(gate.get("before_ppm")).strip()
        assert isinstance(gate.get("after_ppm"), str) and str(gate.get("after_ppm")).strip()
