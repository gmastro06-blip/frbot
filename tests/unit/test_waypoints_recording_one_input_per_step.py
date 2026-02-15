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


def test_waypoints_recording_one_input_per_step(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    result = mod._run_waypoints_mock(tmp_path)

    assert result.gate == "waypoints"
    assert result.ok is True
    assert result.inputs_sent == 3

    jsonl = tmp_path / "waypoints_session.jsonl"
    assert jsonl.exists()

    step_lines = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("event") == "step":
            step_lines.append(payload)

    assert len(step_lines) == 3
    assert all(int(s.get("inputs_sent", 0)) == 1 for s in step_lines)
