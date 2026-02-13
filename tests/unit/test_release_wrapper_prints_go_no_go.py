from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any


def _load_module(repo_root: Path) -> Any:
    path = repo_root / "tools" / "release_prod_full.py"
    spec = importlib.util.spec_from_file_location("release_prod_full", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_release_wrapper_prints_no_go_when_missing_env(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    # Run fully isolated under tmp_path.
    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)

    # Ensure required env vars are missing.
    monkeypatch.delenv("FRBOT_OBS_SOURCE_NAME", raising=False)
    monkeypatch.delenv("FRBOT_WINDOW_TITLE", raising=False)
    monkeypatch.delenv("FRBOT_WINDOW_HWND", raising=False)
    monkeypatch.delenv("FRBOT_REAL_FRAMES_DIR", raising=False)

    rc = mod.run_release([])
    out = capsys.readouterr().out

    assert rc == 2
    assert out.startswith("RELEASE_NO_GO:")


def test_release_wrapper_prints_go_when_all_steps_ok(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("FRBOT_REAL_FRAMES_DIR", raising=False)

    # Provide required args.
    argv = [
        "-ObsSource",
        "Tibia_Fuente",
        "-WindowTitle",
        "Tibia - Onniwabanshu",
    ]

    def fake_run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> Any:
        # Create side-effects expected by the zipping step.
        frames_dir = Path(env["FRBOT_REAL_FRAMES_DIR"])
        (tmp_path / "diagnostics").mkdir(parents=True, exist_ok=True)

        if argv[:3] == ["poetry", "run", "python"] and argv[-1].endswith("tools/audit_repo_status.py"):
            (tmp_path / "diagnostics" / "status_repo.json").write_text("{}", encoding="utf-8")
            (tmp_path / "diagnostics" / "window_diagnostics.json").write_text("{}", encoding="utf-8")

        if argv[:3] == ["poetry", "run", "python"] and argv[-1].endswith("main.py"):
            # Simulate runtime emitting its canonical logs.
            (tmp_path / "diagnostics" / "runtime.log").write_text("{}\n", encoding="utf-8")

        # Ensure at least one ppm + last_result exists.
        frames_dir.mkdir(parents=True, exist_ok=True)
        (frames_dir / "targeting_full_last_result.json").write_text("{}", encoding="utf-8")
        (frames_dir / "sample.ppm").write_text("P3\n1 1\n255\n0 0 0\n", encoding="utf-8")

        return mod.StepResult(name=argv[0], argv=list(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run", fake_run)

    rc = mod.run_release(argv)
    out = capsys.readouterr().out

    assert rc == 0
    assert out == "RELEASE_GO\n"

    # Zip should exist.
    releases = tmp_path / "diagnostics" / "releases"
    zips = list(releases.glob("*.zip"))
    assert len(zips) == 1


def test_release_uses_timestamp_subdir_when_frames_dir_is_base(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    monkeypatch.setattr(mod, "_repo_root", lambda: tmp_path)
    base_frames = tmp_path / "diagnostics" / "frames_full"
    base_frames.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FRBOT_REAL_FRAMES_DIR", str(base_frames))

    seen_env_frames: list[str] = []

    def fake_run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> Any:
        seen_env_frames.append(str(env.get("FRBOT_REAL_FRAMES_DIR", "")))
        frames_dir = Path(env["FRBOT_REAL_FRAMES_DIR"])
        (tmp_path / "diagnostics").mkdir(parents=True, exist_ok=True)
        if argv[:3] == ["poetry", "run", "python"] and argv[-1].endswith("tools/audit_repo_status.py"):
            (tmp_path / "diagnostics" / "status_repo.json").write_text("{}", encoding="utf-8")
            (tmp_path / "diagnostics" / "window_diagnostics.json").write_text("{}", encoding="utf-8")
        if argv[:3] == ["poetry", "run", "python"] and argv[-1].endswith("main.py"):
            (tmp_path / "diagnostics" / "runtime.log").write_text("{}\n", encoding="utf-8")
        frames_dir.mkdir(parents=True, exist_ok=True)
        (frames_dir / "targeting_full_last_result.json").write_text("{}", encoding="utf-8")
        (frames_dir / "sample.ppm").write_text("P3\n1 1\n255\n0 0 0\n", encoding="utf-8")
        return mod.StepResult(name=argv[0], argv=list(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run", fake_run)

    rc = mod.run_release(["-ObsSource", "Tibia_Fuente", "-WindowTitle", "Tibia"])
    assert rc == 0
    assert seen_env_frames
    used = Path(seen_env_frames[0])
    assert used.parent.resolve() == base_frames.resolve()
    assert used.name != base_frames.name


def test_release_step_env_isolated_for_pytest_and_runtime_tuning() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    base_env = {
        "FRBOT_PROFILE": "prod_full",
        "FRBOT_HEALING_BACKEND": "real",
        "FRBOT_HEALING_FULL_STRICT_VERIFY_ATTEMPTS": "2",
        "FRBOT_CAPTURE_SOURCE": "obs_source",
    }

    pytest_env, runtime_env, audit_env = mod._build_step_envs(base_env)

    assert "FRBOT_HEALING_BACKEND" not in pytest_env
    assert "FRBOT_HEALING_FULL_STRICT_VERIFY_ATTEMPTS" not in pytest_env
    assert "FRBOT_HEALING_BACKEND" not in audit_env
    assert "FRBOT_HEALING_FULL_STRICT_VERIFY_ATTEMPTS" not in audit_env

    assert runtime_env.get("FRBOT_POST_HEAL_DELAY_MS") == "1200"
    assert runtime_env.get("FRBOT_POST_HEAL_POLL_MS") == "80"
    assert runtime_env.get("FRBOT_HEAL_MP_DECREASE_MIN") == "0.0"
    assert runtime_env.get("FRBOT_INPUT_METHOD") == "postmessage"
    assert runtime_env.get("FRBOT_POST_ATTACK_DELAY_MS") == "300"
    assert runtime_env.get("FRBOT_COMBAT_AFTER_WINDOW_MS") == "2200"
    assert runtime_env.get("FRBOT_COMBAT_AFTER_POLL_MS") == "100"
    assert runtime_env.get("FRBOT_COMBAT_ALLOW_LOCK_ONLY_SUCCESS") == "1"
    assert runtime_env.get("FRBOT_CAVEBOT_MIN_PIXEL_DELTA") == "1"
    assert runtime_env.get("FRBOT_CAVEBOT_STUCK_WINDOW") == "10"
    assert runtime_env.get("FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG") == "130"
    assert runtime_env.get("FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK") == "3"
    assert runtime_env.get("FRBOT_CAVEBOT_DEAD_RECKON_ON_STATIC") == "1"
    assert runtime_env.get("FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX") == "1"
    assert runtime_env.get("FRBOT_LOOTING_FULL_MAX_ACTIONS") == "30"
    assert runtime_env.get("FRBOT_LOOTING_FULL_STOP_NO_DELTA") == "6"
    assert runtime_env.get("FRBOT_LOOTING_BASIC_ACTION") == "key"
    assert runtime_env.get("FRBOT_LOOTING_BASIC_STRICT_VERIFY_ATTEMPTS") == "8"
    assert runtime_env.get("FRBOT_LOOTING_FULL_ALLOW_NO_EVIDENCE_PASS") == "1"
    assert runtime_env.get("FRBOT_TRADE_DELTA_PX_TOL") == "10"
    assert runtime_env.get("FRBOT_TRADE_DELTA_RATIO_MIN") == "0.001"
    assert runtime_env.get("FRBOT_TRADE_FULL_ALLOW_NO_DELTA_PASS") == "1"
    assert runtime_env.get("FRBOT_DEPOSIT_DEPOT_DELTA_PX_TOL") == "10"
    assert runtime_env.get("FRBOT_DEPOSIT_DEPOT_DELTA_RATIO_MIN") == "0.001"
    assert runtime_env.get("FRBOT_DEPOSIT_FULL_ALLOW_NO_DELTA_PASS") == "1"
