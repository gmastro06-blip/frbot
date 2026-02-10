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

    assert rc == 1
    assert out == "RELEASE_NO_GO\n"


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
