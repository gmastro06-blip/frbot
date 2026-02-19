from __future__ import annotations

"""Tests unitarios para la regla targeting_basic inputs_sent=0.

Caso 1: locked_preexisting=True + inputs_sent=0 + PPMs presentes -> PASS (reason="locked_preexisting")
Caso 2: locked_preexisting=False + inputs_sent=0 -> FAIL con reason="targeting_no_action_taken"
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_module(repo_root: Path) -> Any:
    path = repo_root / "tools" / "validate_core_features.py"
    spec = importlib.util.spec_from_file_location("validate_core_features_targeting_no_action", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _setup_frames(frames_dir: Path) -> None:
    """Crea PPMs mínimos de evidencia en frames_dir."""
    (frames_dir / "targeting_1_before.ppm").write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
    (frames_dir / "targeting_1_after.ppm").write_bytes(b"P6\n1 1\n255\x00\x00\x00")


# ---------------------------------------------------------------------------
# Caso 1: locked_preexisting=True + inputs_sent=0 + PPMs existen => PASS
# ---------------------------------------------------------------------------

def test_targeting_locked_preexisting_inputs_zero_passes(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    _setup_frames(frames_dir)

    # El runtime escribe locked_preexisting=True en el last_result antes de que
    # _run_gate_real_projector lo relea.
    last_result_path = frames_dir / "targeting_basic_last_result.json"
    last_result_path.write_text(
        json.dumps({"locked_preexisting": True, "ok": True, "reason": "locked_preexisting"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_wait_tibia_foreground", lambda **_: True)
    monkeypatch.setattr(mod, "_clear_global_fatal", lambda: None)
    monkeypatch.setattr(mod, "_read_global_fatal_reason", lambda: None)
    monkeypatch.setattr(mod, "_with_frbot_env", lambda _env, fn: fn())
    monkeypatch.setattr(mod, "_copy_fatal_to_frames", lambda _f: None)
    monkeypatch.setattr(mod, "write_fatal", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "_latest_gate_ppm_pair", lambda *a, **kw: ("targeting_1_before.ppm", "targeting_1_after.ppm"))

    # consume_last_run_inputs_sent devuelve 0 (sin input enviado)
    monkeypatch.setattr(
        mod,
        "_targeting_entrypoint_mod",
        type("_T", (), {"consume_last_run_inputs_sent": staticmethod(lambda: 0)})(),
    )

    result = mod._run_gate_real_projector(
        frames_dir=frames_dir,
        profile="validate_core_projector",
        gate_name="targeting_basic",
        gate_dump_name="targeting",
        env={"FRBOT_CONFIG_PATH": str(tmp_path / "cfg.json")},
        run_fn=lambda: 0,   # rc=0 (éxito)
        tibia_hwnd=123,
        strict_inputs_eq=1,
        allow_background_input=True,
    )

    assert result.ok is True, f"esperado PASS, got reason={result.reason}"
    assert result.reason == "locked_preexisting", f"reason esperado 'locked_preexisting', got {result.reason!r}"
    assert int(result.inputs_sent) == 0


# ---------------------------------------------------------------------------
# Caso 2: locked_preexisting=False + inputs_sent=0 => FAIL reason determinista
# ---------------------------------------------------------------------------

def test_targeting_no_lock_inputs_zero_fails_with_no_action_taken(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    _setup_frames(frames_dir)

    # Sin last_result previo (o locked_preexisting=False)
    last_result_path = frames_dir / "targeting_basic_last_result.json"
    last_result_path.write_text(
        json.dumps({"locked_preexisting": False, "ok": True, "reason": "ok"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_wait_tibia_foreground", lambda **_: True)
    monkeypatch.setattr(mod, "_clear_global_fatal", lambda: None)
    monkeypatch.setattr(mod, "_read_global_fatal_reason", lambda: None)
    monkeypatch.setattr(mod, "_with_frbot_env", lambda _env, fn: fn())
    monkeypatch.setattr(mod, "_copy_fatal_to_frames", lambda _f: None)
    monkeypatch.setattr(mod, "write_fatal", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "_latest_gate_ppm_pair", lambda *a, **kw: ("targeting_1_before.ppm", "targeting_1_after.ppm"))

    monkeypatch.setattr(
        mod,
        "_targeting_entrypoint_mod",
        type("_T", (), {"consume_last_run_inputs_sent": staticmethod(lambda: 0)})(),
    )

    result = mod._run_gate_real_projector(
        frames_dir=frames_dir,
        profile="validate_core_projector",
        gate_name="targeting_basic",
        gate_dump_name="targeting",
        env={"FRBOT_CONFIG_PATH": str(tmp_path / "cfg.json")},
        run_fn=lambda: 0,   # rc=0 (el entrypoint salió OK, pero sin inputs)
        tibia_hwnd=123,
        strict_inputs_eq=1,
        allow_background_input=True,
    )

    assert result.ok is False, f"esperado FAIL, got ok=True, reason={result.reason}"
    # El reason incluye el gate_name completo para facilitar el diagnóstico.
    assert result.reason == "targeting_basic_no_action_taken", (
        f"reason esperado 'targeting_basic_no_action_taken', got {result.reason!r}"
    )
    # No debe ser el genérico "invalid_inputs_sent"
    assert result.reason != "invalid_inputs_sent"
    assert int(result.inputs_sent) == 0
