from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Mapping


def _load_module(repo_root: Path) -> Any:
    path = repo_root / "tools" / "audit_repo_status.py"
    spec = importlib.util.spec_from_file_location("audit_repo_status", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_git_dirty_adds_repo_dirty_blocker(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
        "FRBOT_OBS_SOURCE_NAME": "TIBIA_CAPTURE",
        "FRBOT_WINDOW_TITLE": "Tibia",
        "FRBOT_CONFIG_PATH": str(tmp_path / "config.json"),
        "FRBOT_REAL_FRAMES_DIR": str(tmp_path / "frames"),
    }

    windows = [
        mod.VisibleWindow(
            hwnd=1,
            title="Tibia",
            minimized=False,
            rect_left=0,
            rect_top=0,
            rect_right=10,
            rect_bottom=10,
            z_order=0,
        )
    ]

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        if argv[:2] == ["git", "status"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout=" M src/x.py\n?? new.txt\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="feature\n", stderr="")
        if argv[:2] == ["poetry", "run"] and "pytest" in argv:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="1 passed in 0.01s\n", stderr="")
        if argv[:3] == ["poetry", "run", "python"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="FINAL DECISION: OPERATIONAL_REAL\n", stderr="")
        return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")

    exit_code, report = mod.run_repo_status_audit(
        repo_root=tmp_path,
        env=env,
        run_cmd=run_cmd,
        list_windows=lambda: (windows, {"ok": True, "windows": [], "monitors": []}),
        now_iso=lambda: "2026-01-01T00:00:00+00:00",
        write_json=lambda _p, _payload: None,
    )

    assert report["final_decision"] == "READY"
    assert exit_code == mod.EXIT_READY
    assert report.get("is_dirty") is True
    assert report.get("untracked_count") == 1
