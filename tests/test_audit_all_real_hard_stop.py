from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_audit_all_real_mode_without_frames_is_hard_stop(tmp_path: Path, monkeypatch: object) -> None:
	# Run as a subprocess to validate the CLI contract.
	repo_root = Path(__file__).resolve().parents[1]
	audit_py = repo_root / 'tools' / 'audit_all.py'

	env = dict(os.environ)
	env['FRBOT_MODE'] = 'real'
	env.pop('FRBOT_REAL_FRAMES_DIR', None)
	env.pop('FRBOT_CONFIG_PATH', None)

	proc = subprocess.run(
		[sys.executable, str(audit_py)],
		cwd=str(repo_root),
		env=env,
		capture_output=True,
		text=True,
		timeout=20,
	)

	assert proc.returncode != 0
	combined = (proc.stdout or '') + (proc.stderr or '')
	assert 'Preconditions: FAIL' in combined
	assert 'reason: real_evidence_missing' in combined
	assert 'FRBOT_REAL_FRAMES_DIR missing' in combined
	assert 'FRBOT_CONFIG_PATH missing' in combined
