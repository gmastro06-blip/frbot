from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module(repo_root: Path):
    path = repo_root / "tools" / "audit_repo_status.py"
    spec = importlib.util.spec_from_file_location("audit_repo_status", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_parse_gate_last_result_ok(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    (frames_dir / "before.ppm").write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
    (frames_dir / "after.ppm").write_bytes(b"P6\n1 1\n255\n\xff\xff\xff")

    payload = {
        "gate": "trade_full",
        "ok": True,
        "outcome_kind": "ok_trade_confirmed_pixel_delta",
        "reason": "",
        "before_ppm": "before.ppm",
        "after_ppm": "after.ppm",
    }
    (frames_dir / "trade_full_last_result.json").write_text(json.dumps(payload), encoding="utf-8")

    out = mod.parse_gate_last_result(frames_dir=frames_dir, gate="trade_full")
    assert out["gate_name"] == "trade_full"
    assert out["ok"] is True
    assert out["outcome_kind"] == "ok_trade_confirmed_pixel_delta"
    assert out["before_ppm"] == "before.ppm"
    assert out["after_ppm"] == "after.ppm"
    assert out["evidence_files"] == ["before.ppm", "after.ppm"]
    assert out["next_action"] == ""


def test_report_schema_keys_present(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    report = mod.new_status_report()
    for key in (
        "timestamp",
        "platform",
        "git_clean",
        "last_commit",
        "tests",
        "audit_mock",
        "audit_prod_full",
        "gates",
    ):
        assert key in report

    # Gate entry schema.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "trade_full_last_result.json").write_text(json.dumps({"ok": False}), encoding="utf-8")
    gate = mod.parse_gate_last_result(frames_dir=frames_dir, gate="trade_full")
    for key in ("gate_name", "ok", "outcome_kind", "reason", "evidence_files", "before_ppm", "after_ppm", "next_action"):
        assert key in gate
