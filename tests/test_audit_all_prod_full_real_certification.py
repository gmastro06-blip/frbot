from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path


def _write_config_prod_full(tmp_path: Path) -> Path:
	# Include all ROI names required by evidence_inventory for prod_full gates.
	content = {
		"frame": {"width": 16, "height": 16},
		"rois": {
			"minimap": {"x": 0, "y": 0, "width": 2, "height": 2},
			"battle_list": {"x": 2, "y": 0, "width": 2, "height": 2},
			"target_frame": {"x": 4, "y": 0, "width": 2, "height": 2},
			"hp_mp": {"x": 6, "y": 0, "width": 2, "height": 2},
			"inventory_text": {"x": 0, "y": 2, "width": 2, "height": 2},
			"depot_container": {"x": 2, "y": 2, "width": 2, "height": 2},
			"trade_inventory": {"x": 4, "y": 2, "width": 2, "height": 2},
			"trade_npc": {"x": 6, "y": 2, "width": 2, "height": 2},
			"trade_action": {"x": 8, "y": 2, "width": 2, "height": 2},
			# combat_basic OR requirement: one of these must exist
			"combat_feedback": {"x": 0, "y": 4, "width": 2, "height": 2},
		},
	}
	path = tmp_path / "rois_prod_full.json"
	path.write_text(__import__("json").dumps(content), encoding="utf-8")
	return path


def _write_ppm_checker(path: Path, *, w: int = 16, h: int = 16) -> None:
	# Deterministic high-contrast frame to satisfy real_mode_audit calibration.
	# P6, RGB, checkerboard (0/255).
	buf = bytearray()
	for y in range(h):
		for x in range(w):
			v = 255 if ((x + y) % 2 == 0) else 0
			buf.extend([v, v, v])
	data = (f"P6\n{w} {h}\n255\n").encode("ascii") + bytes(buf)
	path.write_bytes(data)


def _run_audit(repo_root: Path, *, frames_dir: Path, config_path: Path) -> subprocess.CompletedProcess[str]:
	env = dict(os.environ)
	env["FRBOT_PROFILE"] = "prod_full"
	env["FRBOT_MODE"] = "real"
	# Hermetic: do not inherit local OBS capture settings.
	env["FRBOT_CAPTURE_SOURCE"] = "client"
	env.pop("FRBOT_OBS_SOURCE_NAME", None)
	env.pop("FRBOT_OBS_PROJECTOR_TITLE", None)
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


def _write_obs_source_manifest(frames_dir: Path, *, obs_source_name: str) -> None:
	# Minimal manifest required by tools/audit_all.py when FRBOT_CAPTURE_SOURCE=obs_source.
	data = {
		"capture_source": "obs_source",
		"obs_source_name": str(obs_source_name),
	}
	(frames_dir / "evidence_manifest.json").write_text(json.dumps(data), encoding="utf-8")


def test_prod_full_real_mode_partial_gate_evidence_stops_unverified(tmp_path: Path) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config_prod_full(tmp_path)

	stamp = "20260201-000000"
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp}_idle_before.ppm")
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp}_idle_after.ppm")

	proc = _run_audit(repo_root, frames_dir=frames_dir, config_path=config_path)
	assert proc.returncode != 0
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert "Gates:" in combined
	assert "combat_basic: OK" in combined
	assert "looting_full: UNVERIFIED" in combined
	assert "FINAL DECISION: NOT_OPERATIONAL_REAL" in combined
	assert "Semantic audit: SKIPPED" in combined


def test_prod_full_real_mode_operational_real_with_minimal_evidence(tmp_path: Path) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config_prod_full(tmp_path)

	stamp0 = "20260201-000000"
	# Idle pair also counts as combat_basic evidence.
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_before.ppm")
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_after.ppm")

	stamp1 = "20260201-000100"
	_write_ppm_checker(frames_dir / f"looting_full_{stamp1}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"looting_full_{stamp1}_tick_after.ppm")

	stamp2 = "20260201-000200"
	_write_ppm_checker(frames_dir / f"deposit_full_{stamp2}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"deposit_full_{stamp2}_tick_after.ppm")

	stamp3 = "20260201-000300"
	_write_ppm_checker(frames_dir / f"trade_full_{stamp3}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"trade_full_{stamp3}_tick_after.ppm")

	proc = _run_audit(repo_root, frames_dir=frames_dir, config_path=config_path)
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert proc.returncode == 0, combined
	assert "FINAL DECISION: OPERATIONAL_REAL" in combined


def test_prod_full_real_mode_blocks_without_obs_source_manifest_when_required(tmp_path: Path, monkeypatch) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config_prod_full(tmp_path)

	stamp0 = "20260201-000000"
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_before.ppm")
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_after.ppm")

	# Require OBS source identity without providing manifest.
	monkeypatch.setenv("FRBOT_CAPTURE_SOURCE", "obs_source")
	monkeypatch.setenv("FRBOT_OBS_SOURCE_NAME", "Tibia_Fuente")
	monkeypatch.setenv("FRBOT_PROFILE", "prod_full")
	monkeypatch.setenv("FRBOT_MODE", "real")
	monkeypatch.setenv("FRBOT_REAL_FRAMES_DIR", str(frames_dir))
	monkeypatch.setenv("FRBOT_CONFIG_PATH", str(config_path))

	audit_py = repo_root / "tools" / "audit_all.py"
	proc = subprocess.run(
		[sys.executable, str(audit_py)],
		cwd=str(repo_root),
		env=dict(os.environ),
		capture_output=True,
		text=True,
		timeout=20,
	)
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert proc.returncode != 0
	assert "evidence_manifest.json" in combined
	assert "FINAL DECISION: NOT_OPERATIONAL_REAL" in combined


def test_prod_full_real_mode_operational_real_with_obs_source_manifest(tmp_path: Path, monkeypatch) -> None:
	repo_root = Path(__file__).resolve().parents[1]
	frames_dir = tmp_path / "frames"
	frames_dir.mkdir(parents=True, exist_ok=True)
	config_path = _write_config_prod_full(tmp_path)

	stamp0 = "20260201-000000"
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_before.ppm")
	_write_ppm_checker(frames_dir / f"combat_basic_{stamp0}_idle_after.ppm")

	stamp1 = "20260201-000100"
	_write_ppm_checker(frames_dir / f"looting_full_{stamp1}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"looting_full_{stamp1}_tick_after.ppm")

	stamp2 = "20260201-000200"
	_write_ppm_checker(frames_dir / f"deposit_full_{stamp2}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"deposit_full_{stamp2}_tick_after.ppm")

	stamp3 = "20260201-000300"
	_write_ppm_checker(frames_dir / f"trade_full_{stamp3}_tick_before.ppm")
	_write_ppm_checker(frames_dir / f"trade_full_{stamp3}_tick_after.ppm")

	_write_obs_source_manifest(frames_dir, obs_source_name="Tibia_Fuente")

	monkeypatch.setenv("FRBOT_CAPTURE_SOURCE", "obs_source")
	monkeypatch.setenv("FRBOT_OBS_SOURCE_NAME", "Tibia_Fuente")
	monkeypatch.setenv("FRBOT_PROFILE", "prod_full")
	monkeypatch.setenv("FRBOT_MODE", "real")
	monkeypatch.setenv("FRBOT_REAL_FRAMES_DIR", str(frames_dir))
	monkeypatch.setenv("FRBOT_CONFIG_PATH", str(config_path))

	audit_py = repo_root / "tools" / "audit_all.py"
	proc = subprocess.run(
		[sys.executable, str(audit_py)],
		cwd=str(repo_root),
		env=dict(os.environ),
		capture_output=True,
		text=True,
		timeout=20,
	)
	combined = (proc.stdout or "") + (proc.stderr or "")
	assert proc.returncode == 0, combined
	assert "FINAL DECISION: OPERATIONAL_REAL" in combined
