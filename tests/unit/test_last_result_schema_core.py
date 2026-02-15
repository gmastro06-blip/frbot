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


def test_last_result_schema_core(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)

    rc = mod.run_validate_core(["--mode", "mock"])
    assert rc == 0

    validate_root = tmp_path / "diagnostics" / "validate"
    runs = sorted(validate_root.glob("*"))
    assert runs
    out_dir = runs[-1]

    expected_gates = ["targeting_full", "healing_full", "cavebot_full", "waypoints"]
    required = {
        "ok",
        "gate",
        "profile",
        "reason",
        "evidence_kind",
        "inputs_sent",
        "before_ppm",
        "after_ppm",
        "ts",
        "capture_source",
        "window_hwnd",
    }

    for gate in expected_gates:
        p = out_dir / f"{gate}_last_result.json"
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert required.issubset(set(data.keys()))
        assert data["gate"] == gate
        assert isinstance(data["ok"], bool)
        assert isinstance(data["inputs_sent"], int)
        assert isinstance(data["window_hwnd"], int)
        assert isinstance(data["reason"], str) and data["reason"].strip()
        assert isinstance(data["evidence_kind"], str) and data["evidence_kind"].strip()
