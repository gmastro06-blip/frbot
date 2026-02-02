from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _write_config(tmp_path: Path) -> Path:
	# Minimal canonical config shape: top-level {"rois": {...}}
	# Keep it valid for the config contract; gate-specific ROI completeness is tested via inventory.
	content = {
		"rois": {
			"minimap": {"x": 0, "y": 0, "width": 1, "height": 1},
			"battle_list": {"x": 0, "y": 0, "width": 1, "height": 1},
			"target_frame": {"x": 0, "y": 0, "width": 1, "height": 1},
			"hp_mp": {"x": 0, "y": 0, "width": 1, "height": 1},
			"inventory": {"x": 0, "y": 0, "width": 1, "height": 1},
			"npc_dialog": {"x": 0, "y": 0, "width": 1, "height": 1},
			"trade": {"x": 0, "y": 0, "width": 1, "height": 1},
		}
	}
	path = tmp_path / "rois.json"
	path.write_text(__import__("json").dumps(content), encoding="utf-8")
	return path


def _write_ppm(path: Path) -> None:
	# Minimal valid binary PPM (P6), 1x1 black pixel.
	data = b"P6\n1 1\n255\n" + bytes([0, 0, 0])
	path.write_bytes(data)


def _run_audit(repo_root: Path, *, frames_dir: Path, config_path: Path) -> subprocess.CompletedProcess[str]:
	env = dict(os.environ)
	env["FRBOT_MODE"] = "real"
	env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
	env["FRBOT_CONFIG_PATH"] = str(config_path)

	audit_py = repo_root / "tools" / "audit_all.py"
	return subprocess.run(
		[sys.executable, str(audit_py)],
		cwd=str(repo_root),
		env=env,
		capture_output=True,
		text=True,
		timeout=20,
	)


def test_real_mode_empty_frames_dir_is_hard_stop(tmp_path: Path) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config(tmp_path)

	proc = _run_audit(repo_root, frames_dir=frames_dir, config_path=config_path)
	assert proc.returncode != 0
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert "Preconditions: FAIL" in combined
	assert "reason: real_evidence_missing" in combined


def test_real_mode_partial_gate_evidence_stops_unverified(tmp_path: Path) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config(tmp_path)

	# Provide only TARGETING before/after evidence.
	stamp = "20260201-000000"
	reason = "idle"
	_write_ppm(frames_dir / f"targeting_{stamp}_{reason}_before.ppm")
	_write_ppm(frames_dir / f"targeting_{stamp}_{reason}_after.ppm")

	proc = _run_audit(repo_root, frames_dir=frames_dir, config_path=config_path)
	assert proc.returncode != 0
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert "Gates:" in combined
	assert "targeting: OK" in combined
	assert "healing: UNVERIFIED" in combined
	assert "FINAL DECISION: NOT_OPERATIONAL_REAL" in combined
	assert "Semantic audit: SKIPPED" in combined
