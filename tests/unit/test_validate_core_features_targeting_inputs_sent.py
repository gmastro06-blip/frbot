from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_module(repo_root: Path) -> Any:
    path = repo_root / "tools" / "validate_core_features.py"
    spec = importlib.util.spec_from_file_location("validate_core_features", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_targeting_basic_preserves_inputs_sent_even_if_gate_fails(tmp_path: Path, monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod = _load_module(repo_root)

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "targeting_1_before.ppm").write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
    (frames_dir / "targeting_1_after.ppm").write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")

    monkeypatch.setattr(mod, "_wait_tibia_foreground", lambda **_: True)
    monkeypatch.setattr(mod, "_clear_global_fatal", lambda: None)
    monkeypatch.setattr(mod, "_read_global_fatal_reason", lambda: None)
    monkeypatch.setattr(mod, "_with_frbot_env", lambda _env, fn: fn())
    monkeypatch.setattr(mod, "_copy_fatal_to_frames", lambda _frames: None)
    monkeypatch.setattr(mod, "write_fatal", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        mod,
        "_targeting_entrypoint_mod",
        type("_T", (), {"consume_last_run_inputs_sent": staticmethod(lambda: 1)})(),
    )

    result = mod._run_gate_real_projector(
        frames_dir=frames_dir,
        profile="validate_core_projector",
        gate_name="targeting_basic",
        gate_dump_name="targeting",
        env={"FRBOT_CONFIG_PATH": str(tmp_path / "cfg.json")},
        run_fn=lambda: 1,
        tibia_hwnd=123,
        strict_inputs_eq=1,
        allow_background_input=True,
    )

    assert result.ok is False
    assert int(result.inputs_sent) == 1
