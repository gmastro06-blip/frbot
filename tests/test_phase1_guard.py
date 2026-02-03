from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _write_ppm(path: Path) -> None:
    # Minimal valid binary PPM (P6), 1x1 black pixel.
    path.write_bytes(b"P6\n1 1\n255\n" + bytes([0, 0, 0]))


def _write_manifest(frames_dir: Path, *, version: str, complete: bool) -> None:
    gates = ("targeting", "healing", "combat", "cavebot", "looting", "deposit", "trade")
    gates_payload: dict[str, list[list[str]]] = {}
    payload: dict[str, object] = {
        "version": version,
        "timestamp": "2026-02-01T00:00:00Z",
        "window_hwnd": "0x1",
        "window_title": "Tibia",
        "gates": gates_payload,
    }

    for g in gates:
        if complete:
            b = f"{g}_20260201-000000_idle_before.ppm"
            a = f"{g}_20260201-000000_idle_after.ppm"
            _write_ppm(frames_dir / b)
            _write_ppm(frames_dir / a)
            gates_payload[g] = [[b, a]]
        else:
            gates_payload[g] = []

    (frames_dir / "_evidence_manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_config(path: Path) -> None:
    rois = {
        "minimap": {"x": 0, "y": 0, "width": 1, "height": 1},
        "battle_list": {"x": 0, "y": 0, "width": 1, "height": 1},
        "hp_mp": {"x": 0, "y": 0, "width": 1, "height": 1},
        "target_frame": {"x": 0, "y": 0, "width": 1, "height": 1},
    }
    path.write_text(json.dumps({"rois": rois}, indent=2, sort_keys=True), encoding="utf-8")


def _run_guard(repo_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    guard = repo_root / "tools" / "phase1_guard.py"
    return subprocess.run(
        [sys.executable, str(guard)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_phase1_guard_missing_env_is_hard_stop(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_emergency"
    env.pop("FRBOT_REAL_FRAMES_DIR_OLD", None)
    env.pop("FRBOT_REAL_FRAMES_DIR_NEW", None)
    env.pop("FRBOT_CONFIG_PATH", None)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "phase1_preconditions_failed"
    assert "env_missing:FRBOT_REAL_FRAMES_DIR_OLD" in payload["missing"]


def test_phase1_guard_partial_evidence_is_hard_stop(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    _write_manifest(old_dir, version="15.x", complete=False)
    _write_manifest(new_dir, version="15.y", complete=False)

    cfg = tmp_path / "rois.json"
    _write_config(cfg)

    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_emergency"
    env["FRBOT_REAL_FRAMES_DIR_OLD"] = str(old_dir)
    env["FRBOT_REAL_FRAMES_DIR_NEW"] = str(new_dir)
    env["FRBOT_CONFIG_PATH"] = str(cfg)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "phase1_preconditions_failed"


def test_phase1_guard_complete_evidence_is_ready(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    _write_manifest(old_dir, version="15.x", complete=True)
    _write_manifest(new_dir, version="15.y", complete=True)

    cfg = tmp_path / "rois.json"
    _write_config(cfg)

    env = dict(os.environ)
    env["FRBOT_PROFILE"] = "prod_emergency"
    env["FRBOT_REAL_FRAMES_DIR_OLD"] = str(old_dir)
    env["FRBOT_REAL_FRAMES_DIR_NEW"] = str(new_dir)
    env["FRBOT_CONFIG_PATH"] = str(cfg)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "PHASE 1 READY"
