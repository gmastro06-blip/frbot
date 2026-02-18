from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _write_config(path: Path, *, rois: dict, frame: dict | None = None) -> None:
    data = {"rois": rois}
    if frame:
        data["frame"] = frame
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _run_guard(repo_root: Path, env: dict[str, str], args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    guard = repo_root / "tools" / "roi_guard.py"
    cmd = [sys.executable, str(guard)]
    if args:
        cmd.extend(args)
    return subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_roi_guard_missing_env_is_hard_stop(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("FRBOT_CONFIG_PATH", None)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "roi_preconditions_failed"
    # Accept either env_missing or env_not_absolute as valid indicators of missing config
    missing_str = ",".join(payload.get("missing", []) + payload.get("invalid", []))
    assert "env_missing" in missing_str or "env_not_absolute" in missing_str or "missing_config" in missing_str


def test_roi_guard_missing_rois_is_hard_stop(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    cfg = tmp_path / "rois.json"
    _write_config(cfg, rois={"minimap": {"x": 0, "y": 0, "width": 1, "height": 1}})

    env = dict(os.environ)
    env["FRBOT_CONFIG_PATH"] = str(cfg)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["reason"] == "roi_preconditions_failed"
    assert payload["details"]["missing_critical_rois"]


def test_roi_guard_complete_rois_is_ready(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    # Provide everything required by evidence_inventory defaults + semantic critical.
    # Note: Both flat format ("hp_mp") AND nested format ("healing": {"hp_mp": ...}) needed for compatibility
    rois = {
        # semantic critical (flat)
        "minimap": {"x": 0, "y": 0, "width": 1, "height": 1},
        "battle_list": {"x": 0, "y": 0, "width": 1, "height": 1},
        "inventory_text": {"x": 0, "y": 0, "width": 1, "height": 1},
        "hp_bar": {"x": 0, "y": 0, "width": 1, "height": 1},
        "mp_bar": {"x": 0, "y": 0, "width": 1, "height": 1},
        "trade_npc": {"x": 0, "y": 0, "width": 1, "height": 1},
        "trade_inventory": {"x": 0, "y": 0, "width": 1, "height": 1},
        # healing module requires hp_mp (flat AND nested for compatibility)
        "hp_mp": {"x": 0, "y": 0, "width": 1, "height": 1},
        "healing": {"hp_mp": {"x": 0, "y": 0, "width": 1, "height": 1}},
        # evidence_inventory defaults (extras)
        "target_frame": {"x": 0, "y": 0, "width": 1, "height": 1},
        "hp_text": {"x": 0, "y": 0, "width": 1, "height": 1},
        "mp_text": {"x": 0, "y": 0, "width": 1, "height": 1},
        "heal_cooldown": {"x": 0, "y": 0, "width": 1, "height": 1},
        "target_hp_bar": {"x": 0, "y": 0, "width": 1, "height": 1},
        "combat_cooldown": {"x": 0, "y": 0, "width": 1, "height": 1},
        "combat_feedback": {"x": 0, "y": 0, "width": 1, "height": 1},
        "loot_container_open": {"x": 0, "y": 0, "width": 1, "height": 1},
        "loot_corpse": {"x": 0, "y": 0, "width": 1, "height": 1},
        "loot_take": {"x": 0, "y": 0, "width": 1, "height": 1},
        "depot_container": {"x": 0, "y": 0, "width": 1, "height": 1},
        "trade_action": {"x": 0, "y": 0, "width": 1, "height": 1},
    }

    cfg = tmp_path / "rois.json"
    _write_config(cfg, rois=rois)

    env = dict(os.environ)
    env["FRBOT_CONFIG_PATH"] = str(cfg)

    proc = _run_guard(repo_root, env)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "ROI READY"
