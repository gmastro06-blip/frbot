from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_audit(repo_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    audit_py = repo_root / "tools" / "audit_prod_full.py"
    return subprocess.run(
        [sys.executable, str(audit_py)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_audit_prod_full_reports_missing_preconditions_without_gate_eval(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_full"
    env.pop("FRBOT_REAL_FRAMES_DIR", None)
    env.pop("FRBOT_CONFIG_PATH", None)

    proc = _run_audit(repo_root, env)
    combined = (proc.stdout or "") + (proc.stderr or "")

    assert proc.returncode != 0
    assert "FINAL DECISION: NOT_OPERATIONAL_REAL" in combined
    assert "real_frames_dir_missing" in combined
    # Config may be defaulted but marked as missing/invalid
    assert "missing_last_result" not in combined


def test_audit_prod_full_reports_real_evidence_missing(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "rois_prod_full.json"
    config.write_text('{"frame":{"width":1,"height":1},"rois":{}}', encoding="utf-8")

    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_full"
    env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
    env["FRBOT_CONFIG_PATH"] = str(config)
    env["FRBOT_OBS_SOURCE_NAME"] = "Tibia_Fuente"

    proc = _run_audit(repo_root, env)
    combined = (proc.stdout or "") + (proc.stderr or "")

    assert proc.returncode != 0
    assert "FINAL DECISION: NOT_OPERATIONAL_REAL" in combined
    assert "real_evidence_missing" in combined
    assert "missing_last_result" not in combined
