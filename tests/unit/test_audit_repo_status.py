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


class _Writer:
    def __init__(self) -> None:
        self.writes: list[tuple[Path, dict]] = []

    def __call__(self, path: Path, payload: dict) -> None:
        self.writes.append((path, payload))


def test_env_missing_is_not_operational_and_writes_json(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    writer = _Writer()

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        # Avoid touching real git/poetry. Return failures; env blockers should dominate.
        return mod.CmdResult(argv=list(argv), returncode=1, stdout="", stderr="stub")

    # Even with missing env, we must always write status_repo.json and window_diagnostics.json.
    exit_code, report = mod.run_repo_status_audit(
        repo_root=tmp_path,
        env={},
        run_cmd=run_cmd,
        list_windows=lambda: ([], {"ok": False, "reason": "stub"}),
        now_iso=lambda: "2026-01-01T00:00:00+00:00",
        write_json=writer,
    )

    assert report["final_decision"] == "NOT_READY"
    assert exit_code == mod.EXIT_NOT_READY

    # Must write both files.
    written_paths = {p.as_posix() for (p, _payload) in writer.writes}
    assert (tmp_path / mod.STATUS_REPO_JSON_REL).as_posix() in written_paths
    assert (tmp_path / mod.WINDOW_DIAGNOSTICS_JSON_REL).as_posix() in written_paths

    blockers = set(report.get("root_blockers", []))
    assert "obs_source_missing" in blockers
    assert "obs_source_name_missing" in blockers
    assert "window_selector_missing" in blockers
    assert "profile_not_prod_full" in blockers


def test_self_heal_hwnd_by_title_exact(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    writer = _Writer()

    # Minimal environment for REAL.
    env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
        "FRBOT_OBS_SOURCE_NAME": "TIBIA_CAPTURE",
        "FRBOT_WINDOW_HWND": "0xDEADBEEF",  # invalid placeholder -> treated as unset
        "FRBOT_WINDOW_TITLE": "Tibia - Character",
        "FRBOT_CONFIG_PATH": str(tmp_path / "config.json"),
        "FRBOT_REAL_FRAMES_DIR": str(tmp_path / "frames_full"),
    }
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    windows = [
        mod.VisibleWindow(
            hwnd=123,
            title="Tibia - Character",
            minimized=False,
            rect_left=0,
            rect_top=0,
            rect_right=100,
            rect_bottom=100,
            z_order=5,
        )
    ]

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        # Pretend repo is clean, pytest ok, audit_prod_full passes.
        if argv[:2] == ["git", "status"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="main\n", stderr="")
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
        write_json=writer,
    )

    assert report["final_decision"] in {"READY", "NOT_READY"}  # depends on other checks; should not be NOT_OPERATIONAL
    assert report["window"]["effective_hwnd"] == 123
    assert report["window"]["selection"]["match_kind"] == "title_exact"
    assert "window_hwnd_invalid" not in set(report["root_blockers"])
    assert exit_code in {mod.EXIT_READY, mod.EXIT_NOT_READY}


def test_repo_dirty_makes_not_ready(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    writer = _Writer()

    env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
        "FRBOT_OBS_SOURCE_NAME": "TIBIA_CAPTURE",
        "FRBOT_WINDOW_HWND": "123",
        "FRBOT_CONFIG_PATH": str(tmp_path / "config.json"),
        "FRBOT_REAL_FRAMES_DIR": str(tmp_path / "frames_full"),
    }
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    windows = [
        mod.VisibleWindow(
            hwnd=123,
            title="Tibia",
            minimized=False,
            rect_left=0,
            rect_top=0,
            rect_right=100,
            rect_bottom=100,
            z_order=0,
        )
    ]

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        if argv[:2] == ["git", "status"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout=" M src/x.py\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="main\n", stderr="")
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
        write_json=writer,
    )

    assert report["final_decision"] == "NOT_READY"
    assert exit_code == mod.EXIT_NOT_READY
    assert "repo_dirty" in set(report.get("root_blockers", []))


def test_status_json_is_written_each_run(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    writer = _Writer()

    env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
        "FRBOT_OBS_SOURCE_NAME": "TIBIA_CAPTURE",
        "FRBOT_WINDOW_TITLE": "Tibia",
        "FRBOT_CONFIG_PATH": str(tmp_path / "config.json"),
        "FRBOT_REAL_FRAMES_DIR": str(tmp_path / "frames_full"),
    }
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        if argv[:2] == ["git", "status"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="main\n", stderr="")
        if argv[:2] == ["poetry", "run"] and "pytest" in argv:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="1 passed in 0.01s\n", stderr="")
        if argv[:3] == ["poetry", "run", "python"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="FINAL DECISION: OPERATIONAL_REAL\n", stderr="")
        return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")

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

    mod.run_repo_status_audit(
        repo_root=tmp_path,
        env=env,
        run_cmd=run_cmd,
        list_windows=lambda: (windows, {"ok": True, "windows": [], "monitors": []}),
        now_iso=lambda: "2026-01-01T00:00:00+00:00",
        write_json=writer,
    )
    mod.run_repo_status_audit(
        repo_root=tmp_path,
        env=env,
        run_cmd=run_cmd,
        list_windows=lambda: (windows, {"ok": True, "windows": [], "monitors": []}),
        now_iso=lambda: "2026-01-01T00:00:01+00:00",
        write_json=writer,
    )

    status_writes = [p for (p, _payload) in writer.writes if p.as_posix().endswith(mod.STATUS_REPO_JSON_REL)]
    assert len(status_writes) == 2


def test_explicit_hwnd_is_accepted_when_enumeration_fails(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    writer = _Writer()

    env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
        "FRBOT_OBS_SOURCE_NAME": "TIBIA_CAPTURE",
        "FRBOT_WINDOW_HWND": "123",
        "FRBOT_CONFIG_PATH": str(tmp_path / "config.json"),
        "FRBOT_REAL_FRAMES_DIR": str(tmp_path / "frames_full"),
    }
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    def run_cmd(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout_s: int = 0) -> Any:
        if argv[:2] == ["git", "status"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="main\n", stderr="")
        if argv[:2] == ["poetry", "run"] and "pytest" in argv:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="1 passed in 0.01s\n", stderr="")
        if argv[:3] == ["poetry", "run", "python"]:
            return mod.CmdResult(argv=list(argv), returncode=0, stdout="FINAL DECISION: OPERATIONAL_REAL\n", stderr="")
        return mod.CmdResult(argv=list(argv), returncode=0, stdout="", stderr="")

    exit_code, report = mod.run_repo_status_audit(
        repo_root=tmp_path,
        env=env,
        run_cmd=run_cmd,
        list_windows=lambda: ([], {"ok": False, "reason": "stub"}),
        now_iso=lambda: "2026-01-01T00:00:00+00:00",
        write_json=writer,
    )

    assert report["window"]["effective_hwnd"] == 123
    assert report["window"]["selection"]["match_kind"] == "explicit_hwnd_unverified"
    assert "window_hwnd_invalid" not in set(report.get("root_blockers", []))
    assert report["final_decision"] in {"READY", "NOT_READY"}
    assert exit_code in {mod.EXIT_READY, mod.EXIT_NOT_READY}


def test_exit_code_constants_are_stable() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    assert mod.EXIT_READY == 0
    assert mod.EXIT_NOT_READY == 2
    assert mod.EXIT_NOT_OPERATIONAL == 3
