from __future__ import annotations

from pathlib import Path


def test_run_release_ps1_calls_release_tool() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ps1 = (repo_root / "tools" / "run_release_prod_full.ps1").read_text(encoding="utf-8", errors="replace")
    assert "tools\\release_prod_full.py" in ps1 or "tools/release_prod_full.py" in ps1


def test_release_tool_emits_reasoned_no_go() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    py = (repo_root / "tools" / "release_prod_full.py").read_text(encoding="utf-8", errors="replace")
    assert "RELEASE_GO" in py
    assert "RELEASE_NO_GO:" in py

    # Step contract (fail-fast, in order) lives in the orchestrator.
    assert "pytest" in py
    assert "tools/audit_repo_status.py" in py or "tools\\audit_repo_status.py" in py
    assert "main.py" in py
    assert "tools/audit_prod_full.py" in py or "tools\\audit_prod_full.py" in py


def test_root_run_release_wrapper_has_required_self_heal_defaults() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ps1 = (repo_root / "run_release_prod_full.ps1").read_text(encoding="utf-8", errors="replace")

    assert "Set-Location -LiteralPath $PSScriptRoot" in ps1
    assert "FRBOT_PROFILE" in ps1 and "prod_full" in ps1
    assert "FRBOT_CAPTURE_SOURCE" in ps1 and "obs_source" in ps1
    assert "FRBOT_INPUT_METHOD" in ps1 and "sendinput_vk" in ps1
    assert "rois_prod_full.json" in ps1
    assert "Remove-Item Env:FRBOT_WINDOW_HWND" in ps1
    assert "tools\\release_prod_full.py" in ps1 or "tools/release_prod_full.py" in ps1
