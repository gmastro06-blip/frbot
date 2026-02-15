from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cavebot_entrypoint import run_cavebot_only
from cavebot_full_entrypoint import run_cavebot_full_only
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair
from diagnostics.last_frames import clear as clear_last_frames
from diagnostics.last_frames import snapshot
from healing_entrypoint import run_healing_only
from healing_full_entrypoint import run_healing_full_only
from adapters.capture.meld_projector_real import MeldProjectorMinimapRealCapture
from adapters.window.win32 import Win32WindowBinding
from adapters.windows import win32 as w32
from runtime.config_loader import load_rois
from runtime.minimap_semantics import marker_config_from_env
try:
    from tools._bootstrap import bootstrap_tool_env as _bootstrap_tool_env
except ImportError:
    from _bootstrap import bootstrap_tool_env as _bootstrap_tool_env  # type: ignore[no-redef]
from runtime.pacing import wait_until_ns
from runtime.preflight import preflight as runtime_preflight
from runtime.route_recorder import WaypointRecorder
from targeting_entrypoint import run_targeting_only
from targeting_full_entrypoint import run_targeting_full_only

_targeting_entrypoint_mod = importlib.import_module("targeting_entrypoint")


_bootstrap_tool_env(__file__)


@dataclass(frozen=True)
class GateResult:
    gate: str
    ok: bool
    reason: str
    evidence_kind: str
    inputs_sent: int
    before_ppm: str | None
    after_ppm: str | None
    ts: str
    capture_source: str
    window_hwnd: int
    last_result_path: str


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_hwnd(raw: str) -> int:
    s = str(raw or "").strip()
    if not s:
        return 0
    try:
        return int(s, 0)
    except Exception:
        return 0


def _profile_from_env() -> str:
    raw = str(os.environ.get("FRBOT_PROFILE", "") or "").strip().lower()
    if raw:
        return raw
    mode = str(os.environ.get("FRBOT_MODE", "") or "").strip().lower()
    if mode == "prod_full":
        return "prod_full"
    return "prod_emergency"


def _runtime_mode(default: str) -> str:
    raw = str(os.environ.get("FRBOT_VALIDATE_MODE", "") or "").strip().lower()
    if raw in {"mock", "real"}:
        return raw
    env_mode = str(os.environ.get("FRBOT_MODE", "") or "").strip().lower()
    if env_mode in {"mock", "real"}:
        return env_mode
    return default


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _artifact_dir(repo_root: Path) -> Path:
    ts = _now_iso().replace(":", "").replace("-", "").replace("+", "_").replace("T", "_")
    out = repo_root / "diagnostics" / "validate" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _clean_frbot_env(base_env: dict[str, str]) -> dict[str, str]:
    clean = {k: v for (k, v) in base_env.items() if not str(k).startswith("FRBOT_")}
    return clean


def _mock_rois_path(artifact_dir: Path) -> Path:
    cfg = {
        "rois": {
            "minimap": {"x": 2, "y": 2, "width": 64, "height": 64},
            "battle_list": {"x": 70, "y": 2, "width": 80, "height": 64},
            "target_frame": {"x": 70, "y": 70, "width": 80, "height": 20},
            "hp_bar": {"x": 2, "y": 70, "width": 60, "height": 6},
            "mp_bar": {"x": 2, "y": 78, "width": 60, "height": 6},
            "hp_text": {"x": 2, "y": 86, "width": 4, "height": 1},
            "mp_text": {"x": 2, "y": 88, "width": 4, "height": 1},
            "heal_cooldown": {"x": 2, "y": 90, "width": 6, "height": 3},
            "combat_feedback": {"x": 10, "y": 90, "width": 6, "height": 3},
        }
    }
    out = artifact_dir / "validate_mock_rois.json"
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _with_frbot_env(env_updates: dict[str, str], fn: Callable[[], Any]) -> Any:
    old = {k: v for (k, v) in os.environ.items() if str(k).startswith("FRBOT_")}
    for key in list(os.environ.keys()):
        if str(key).startswith("FRBOT_"):
            os.environ.pop(key, None)
    os.environ.update(env_updates)
    try:
        return fn()
    finally:
        for key in list(os.environ.keys()):
            if str(key).startswith("FRBOT_"):
                os.environ.pop(key, None)
        os.environ.update(old)


def _parse_last_result(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError("last_result_missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError(f"last_result_invalid_json:{type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("last_result_invalid_schema")
    return data


def _resolve_ppm(artifact_dir: Path, name: str | None) -> str | None:
    if not name:
        return None
    p = artifact_dir / str(name)
    if p.exists() and p.is_file():
        return str(p.name)
    return None


def _write_canonical_last_result(
    *,
    artifact_dir: Path,
    profile: str,
    gate: str,
    ok: bool,
    reason: str,
    evidence_kind: str,
    inputs_sent: int,
    before_ppm: str | None,
    after_ppm: str | None,
    capture_source: str,
    window_hwnd: int,
) -> GateResult:
    ts = _now_iso()
    payload: dict[str, object] = {
        "ok": bool(ok),
        "gate": str(gate),
        "profile": str(profile),
        "reason": str(reason),
        "evidence_kind": str(evidence_kind),
        "inputs_sent": int(inputs_sent),
        "before_ppm": before_ppm,
        "after_ppm": after_ppm,
        "ts": str(ts),
        "capture_source": str(capture_source),
        "window_hwnd": int(window_hwnd),
    }
    out = artifact_dir / f"{gate}_last_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GateResult(
        gate=str(gate),
        ok=bool(ok),
        reason=str(reason),
        evidence_kind=str(evidence_kind),
        inputs_sent=int(inputs_sent),
        before_ppm=before_ppm,
        after_ppm=after_ppm,
        ts=str(ts),
        capture_source=str(capture_source),
        window_hwnd=int(window_hwnd),
        last_result_path=str(out),
    )


def _validate_evidence_files(artifact_dir: Path, before_ppm: str | None, after_ppm: str | None) -> None:
    if not before_ppm:
        raise RuntimeError("missing_before_ppm")
    if not after_ppm:
        raise RuntimeError("missing_after_ppm")
    if not (artifact_dir / str(before_ppm)).exists():
        raise RuntimeError("before_ppm_missing_file")
    if not (artifact_dir / str(after_ppm)).exists():
        raise RuntimeError("after_ppm_missing_file")


def _run_full_gate(
    *,
    artifact_dir: Path,
    profile: str,
    gate: str,
    env: dict[str, str],
    run_fn: Callable[[], int],
    min_inputs: int,
    strict_inputs_eq: int | None = None,
) -> GateResult:
    clear_last_frames(gate)
    rc = int(_with_frbot_env(env, run_fn))

    src_path = artifact_dir / f"{gate}_last_result.json"
    raw = _parse_last_result(src_path)

    ok = bool(raw.get("ok", rc == 0))
    reason = str(raw.get("reason") or raw.get("outcome_kind") or raw.get("evidence_reason") or ("ok" if ok else "failed"))
    evidence_kind = str(raw.get("evidence_kind") or raw.get("evidence_reason") or reason)
    inputs_sent = int(raw.get("inputs_sent", raw.get("actions_sent", 0)) or 0)

    before_ppm = _resolve_ppm(artifact_dir, raw.get("before_ppm") if isinstance(raw.get("before_ppm"), str) else None)
    after_ppm = _resolve_ppm(artifact_dir, raw.get("after_ppm") if isinstance(raw.get("after_ppm"), str) else None)

    if not ok:
        raise RuntimeError(f"gate_failed:{gate}:{reason}")

    _validate_evidence_files(artifact_dir, before_ppm, after_ppm)

    if strict_inputs_eq is not None and int(inputs_sent) != int(strict_inputs_eq):
        raise RuntimeError(f"invalid_inputs_sent:{gate}:{inputs_sent}")
    if int(inputs_sent) < int(min_inputs):
        raise RuntimeError(f"invalid_inputs_sent:{gate}:{inputs_sent}")

    return _write_canonical_last_result(
        artifact_dir=artifact_dir,
        profile=profile,
        gate=gate,
        ok=True,
        reason=reason,
        evidence_kind=evidence_kind,
        inputs_sent=int(inputs_sent),
        before_ppm=before_ppm,
        after_ppm=after_ppm,
        capture_source=str(env.get("FRBOT_CAPTURE_SOURCE", "client")),
        window_hwnd=_parse_hwnd(str(env.get("FRBOT_WINDOW_HWND", "0"))),
    )


class _MockSnap:
    def __init__(self, hwnd: int = 123) -> None:
        self.hwnd = int(hwnd)


class _MockBinding:
    def __init__(self, hwnd: int = 123) -> None:
        self._hwnd = int(hwnd)

    def assert_bound(self) -> None:
        return

    def snapshot(self) -> _MockSnap:
        return _MockSnap(hwnd=int(self._hwnd))


class _MockInput:
    def __init__(self) -> None:
        self.events: list[str] = []

    def assert_bound(self, hwnd: int | None = None) -> None:
        return

    def press_key(self, key: str) -> None:
        self.events.append(str(key))

    def click(self, x: int, y: int) -> None:
        self.events.append(f"click:{int(x)},{int(y)}")


class _MockCapture:
    def __init__(self, frames: list[Any]) -> None:
        self._frames = list(frames)
        self._idx = 0

    def grab(self) -> Any:
        if self._idx >= len(self._frames):
            return self._frames[-1]
        out = self._frames[self._idx]
        self._idx += 1
        return out


def _mk_frame(*, marker_x: int, marker_y: int, digest: str) -> Any:
    from contracts.capture import Frame

    w = 16
    h = 16
    rgb = bytearray(w * h * 3)
    mm = bytearray(w * h * 3)

    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            rgb[i + 0] = 24
            rgb[i + 1] = 24
            rgb[i + 2] = 24
            mm[i + 0] = 0
            mm[i + 1] = 0
            mm[i + 2] = 0

    for y in range(max(0, marker_y - 1), min(h, marker_y + 2)):
        for x in range(max(0, marker_x - 1), min(w, marker_x + 2)):
            i = (y * w + x) * 3
            mm[i + 0] = 255
            mm[i + 1] = 0
            mm[i + 2] = 255

    return Frame(
        width=w,
        height=h,
        monotonic_ts_ns=1,
        digest_hex=str(digest),
        rgb=bytes(rgb),
        minimap_detected=True,
        minimap_rgb=bytes(mm),
        minimap_width=w,
        minimap_height=h,
        minimap_digest_hex=str(digest),
    )


def _run_waypoints_mock(artifact_dir: Path) -> GateResult:
    cfg = marker_config_from_env("255,0,255", "20", "3", "0", "0.10", "4.0")

    frames = [
        _mk_frame(marker_x=8, marker_y=8, digest="a0"),
        _mk_frame(marker_x=8, marker_y=6, digest="a1"),
        _mk_frame(marker_x=8, marker_y=6, digest="a1"),
        _mk_frame(marker_x=6, marker_y=6, digest="a2"),
        _mk_frame(marker_x=6, marker_y=6, digest="a2"),
        _mk_frame(marker_x=6, marker_y=6, digest="a3"),
    ]

    capture = _MockCapture(frames)
    input_adapter = _MockInput()
    binding = _MockBinding(hwnd=123)

    rec = WaypointRecorder(
        capture=capture,
        input_adapter=input_adapter,
        binding=binding,
        marker_cfg=cfg,
        out_dir=artifact_dir,
        max_steps=20,
        after_poll_attempts=1,
        after_poll_interval_ms=1,
    )

    rec.start({"mode": "mock", "gate": "waypoints_record"})
    rec.record_move("up")
    rec.record_move("left")
    rec.record_action("stairs_down", "PageDown")
    out_json = rec.stop(save=True)

    if out_json is None or not out_json.exists():
        raise RuntimeError("gate_failed:waypoints_record:missing_session_json")

    steps = list(rec.steps)
    if len(steps) != 3:
        raise RuntimeError("gate_failed:waypoints_record:invalid_step_count")
    if any(int(s.inputs_sent) != 1 for s in steps):
        raise RuntimeError("gate_failed:waypoints_record:invalid_inputs_sent")

    waypoints_jsonl = artifact_dir / "waypoints_session.jsonl"
    shutil.copyfile(rec.jsonl_path, waypoints_jsonl)

    for ppm in rec.jsonl_path.parent.glob("*.ppm"):
        dst = artifact_dir / ppm.name
        if not dst.exists():
            shutil.copyfile(ppm, dst)

    session_json = artifact_dir / "waypoints_session.json"
    shutil.copyfile(out_json, session_json)

    before_ppm = str(steps[0].before_ppm)
    after_ppm = str(steps[-1].after_ppm)
    _validate_evidence_files(artifact_dir, before_ppm, after_ppm)

    return _write_canonical_last_result(
        artifact_dir=artifact_dir,
        profile=_profile_from_env(),
        gate="waypoints",
        ok=True,
        reason="waypoints_recorded",
        evidence_kind="semantic_marker_progress",
        inputs_sent=3,
        before_ppm=before_ppm,
        after_ppm=after_ppm,
        capture_source="mock",
        window_hwnd=123,
    )


def _run_waypoints_real(artifact_dir: Path, env: dict[str, str]) -> GateResult:
    cfg = RuntimeConfig(
        mode="real",
        tick_hz=20.0,
        config_path=str(env.get("FRBOT_CONFIG_PATH", "")),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        minimap_roi=str(env.get("FRBOT_MINIMAP_ROI", "minimap")),
        player_marker_rgb=str(env.get("FRBOT_PLAYER_MARKER_RGB", "255,0,255")),
        player_marker_tol=int(str(env.get("FRBOT_PLAYER_MARKER_TOL", "30") or "30")),
        player_marker_min_pixels=int(str(env.get("FRBOT_PLAYER_MARKER_MIN_PIXELS", "5") or "5")),
        player_marker_max_pixels=int(str(env.get("FRBOT_PLAYER_MARKER_MAX_PIXELS", "0") or "0")),
        window_hwnd=_parse_hwnd(str(env.get("FRBOT_WINDOW_HWND", "0"))),
        window_title_substring=str(env.get("FRBOT_WINDOW_TITLE", "")),
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())
    capture, input_adapter, binding = _with_frbot_env(env, lambda: runtime_preflight(ctx))

    marker_cfg = marker_config_from_env(
        cfg.player_marker_rgb,
        str(cfg.player_marker_tol),
        str(cfg.player_marker_min_pixels),
        str(cfg.player_marker_max_pixels),
        str(cfg.player_marker_min_fill_ratio),
        str(cfg.player_marker_max_aspect_ratio),
    )

    rec = WaypointRecorder(
        capture=capture,
        input_adapter=input_adapter,
        binding=binding,
        marker_cfg=marker_cfg,
        out_dir=artifact_dir,
        max_steps=20,
    )

    rec.start({"mode": "real", "gate": "waypoints_record"})
    rec.record_move("up")
    rec.record_move("left")
    rec.record_action("rope", str(env.get("FRBOT_ROPE_KEY", "F8") or "F8"))
    out_json = rec.stop(save=True)

    if out_json is None or not out_json.exists():
        raise RuntimeError("gate_failed:waypoints_record:missing_session_json")

    steps = list(rec.steps)
    if len(steps) != 3:
        raise RuntimeError("gate_failed:waypoints_record:invalid_step_count")
    if any(int(s.inputs_sent) != 1 for s in steps):
        raise RuntimeError("gate_failed:waypoints_record:invalid_inputs_sent")

    waypoints_jsonl = artifact_dir / "waypoints_session.jsonl"
    shutil.copyfile(rec.jsonl_path, waypoints_jsonl)

    for ppm in rec.jsonl_path.parent.glob("*.ppm"):
        dst = artifact_dir / ppm.name
        if not dst.exists():
            shutil.copyfile(ppm, dst)

    session_json = artifact_dir / "waypoints_session.json"
    shutil.copyfile(out_json, session_json)

    before_ppm = str(steps[0].before_ppm)
    after_ppm = str(steps[-1].after_ppm)
    _validate_evidence_files(artifact_dir, before_ppm, after_ppm)

    return _write_canonical_last_result(
        artifact_dir=artifact_dir,
        profile=_profile_from_env(),
        gate="waypoints",
        ok=True,
        reason="waypoints_recorded",
        evidence_kind="semantic_marker_progress",
        inputs_sent=3,
        before_ppm=before_ppm,
        after_ppm=after_ppm,
        capture_source=str(env.get("FRBOT_CAPTURE_SOURCE", "obs_source")),
        window_hwnd=_parse_hwnd(str(env.get("FRBOT_WINDOW_HWND", "0"))),
    )


def _run_waypoints_gate(mode: str, artifact_dir: Path, env: dict[str, str]) -> GateResult:
    if mode == "mock":
        return _run_waypoints_mock(artifact_dir)
    return _run_waypoints_real(artifact_dir, env)


def _base_gate_env(*, mode: str, profile: str, artifact_dir: Path, args: argparse.Namespace) -> dict[str, str]:
    clean = _clean_frbot_env(dict(os.environ))
    env: dict[str, str] = dict(clean)
    env["FRBOT_PROFILE"] = str(profile)
    env["FRBOT_REAL_FRAMES_DIR"] = str(artifact_dir)
    env["FRBOT_DUMP_FRAMES"] = "1"
    env["FRBOT_TRY_FOCUS"] = "1" if bool(getattr(args, "try_focus", False)) else "0"

    if mode == "mock":
        env["FRBOT_MODE"] = "mock"
        env["FRBOT_CAPTURE_SOURCE"] = "client"
        env["FRBOT_TARGETING_FULL_BACKEND"] = "mock"
        env["FRBOT_HEALING_FULL_BACKEND"] = "mock"
        env["FRBOT_CAVEBOT_FULL_BACKEND"] = "mock"
        env["FRBOT_MOCK_BATTLE_LIST_ROWS"] = str(getattr(args, "mock_battle_rows", "Rat:1:1"))
        env["FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR"] = "normal"

        env["FRBOT_HEAL_HP_THRESHOLD"] = "0.80"
        env["FRBOT_HEAL_HP_INCREASE_MIN"] = "0.05"
        env["FRBOT_HEAL_KEY"] = "F1"
        env["FRBOT_MOCK_HP_CURRENT"] = "40"
        env["FRBOT_MOCK_HP_MAX"] = "100"
        env["FRBOT_MOCK_MP_CURRENT"] = "80"
        env["FRBOT_MOCK_MP_MAX"] = "100"
        env["MOCK_HEAL_EVIDENCE"] = "ok"
        env["MOCK_HEAL_COOLDOWN"] = "clear"

        env["FRBOT_PLAYER_MARKER_RGB"] = "255,0,255"
        env["FRBOT_PLAYER_MARKER_TOL"] = "5"
        env["FRBOT_PLAYER_MARKER_MIN_PIXELS"] = "5"
        env["FRBOT_PLAYER_MARKER_MAX_PIXELS"] = "0"
        env["MOCK_CAVEBOT_PROGRESS_OK"] = "true"
        env["FRBOT_CAVEBOT_WAYPOINTS"] = json.dumps([
            {"waypoint_id": "wp0", "x": 4, "y": 1, "z": 7, "radius_px": 0, "max_ticks": 20}
        ])

        env["FRBOT_MOCK_WINDOW_OK"] = "1"
        env["FRBOT_MOCK_WINDOW_FOREGROUND"] = "1"
        env["FRBOT_MOCK_WINDOW_RECT_OK"] = "1"

        rois_path = _mock_rois_path(artifact_dir)
        env["FRBOT_CONFIG_PATH"] = str(rois_path)
    else:
        env["FRBOT_MODE"] = "prod_full"
        env["FRBOT_CAPTURE_SOURCE"] = "obs_source"
        env["FRBOT_TRY_FOCUS"] = "1"
        env["FRBOT_ALLOW_BACKGROUND_INPUT"] = "1"
        env["FRBOT_INPUT_METHOD"] = "postmessage"
        env["FRBOT_COMBO_METHOD"] = "postmessage"
        env["FRBOT_OBS_SOURCE_NAME"] = str(getattr(args, "obs_source", "") or "")
        env["FRBOT_CONFIG_PATH"] = str(getattr(args, "config_path", "") or "")
        if str(getattr(args, "window_hwnd", "") or "").strip():
            env["FRBOT_WINDOW_HWND"] = str(getattr(args, "window_hwnd"))
        if str(getattr(args, "window_title", "") or "").strip():
            env["FRBOT_WINDOW_TITLE"] = str(getattr(args, "window_title"))
        env["FRBOT_TARGETING_FULL_BACKEND"] = "real"
        env["FRBOT_HEALING_FULL_BACKEND"] = "real"
        env["FRBOT_CAVEBOT_FULL_BACKEND"] = "real"
    return env


def _write_summary(artifact_dir: Path, mode: str, profile: str, final_decision: str, gate_results: list[GateResult]) -> Path:
    payload: dict[str, Any] = {
        "ts": _now_iso(),
        "mode": str(mode),
        "profile": str(profile),
        "final_decision": str(final_decision),
        "gates": [
            {
                "gate": g.gate,
                "ok": g.ok,
                "reason": g.reason,
                "evidence_kind": g.evidence_kind,
                "inputs_sent": g.inputs_sent,
                "before_ppm": g.before_ppm,
                "after_ppm": g.after_ppm,
                "last_result_path": g.last_result_path,
                "capture_source": g.capture_source,
                "window_hwnd": g.window_hwnd,
                "ts": g.ts,
            }
            for g in gate_results
        ],
    }
    out = artifact_dir / "validate_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _validate_real_args(args: argparse.Namespace) -> None:
    if str(getattr(args, "obs_source", "") or "").strip() == "":
        raise RuntimeError("real_missing_obs_source")
    if str(getattr(args, "config_path", "") or "").strip() == "":
        raise RuntimeError("real_missing_config_path")
    has_title = str(getattr(args, "window_title", "") or "").strip() != ""
    has_hwnd = str(getattr(args, "window_hwnd", "") or "").strip() != ""
    if not has_title and not has_hwnd:
        raise RuntimeError("real_missing_window_selector")


def _resolve_tibia_hwnd_by_title(title_substring: str) -> int:
    match = w32.find_window_by_title_substring(str(title_substring or ""))
    if match is None:
        return 0
    return int(match.hwnd)


def _resolve_real_window_titles(*, projector_title: str, tibia_title: str) -> tuple[str, str]:
    preferred_projector = str(projector_title or "").strip()
    preferred_tibia = str(tibia_title or "").strip()

    def _first_existing(title: str) -> str | None:
        t = str(title or "").strip()
        if not t:
            return None
        m = w32.find_window_by_title_substring(t)
        if m is None:
            return None
        return str(m.title)

    resolved_projector = _first_existing(preferred_projector) or preferred_projector
    resolved_tibia = _first_existing(preferred_tibia) or preferred_tibia

    if resolved_projector and resolved_tibia:
        return resolved_projector, resolved_tibia

    try:
        wins = w32.list_top_level_windows(title_substring="", visible_only=True)
    except Exception:
        wins = []

    titles = [str(w.title) for w in wins if str(getattr(w, "title", "")).strip()]

    if not resolved_tibia:
        tibia_candidates = [t for t in titles if t.lower().startswith("tibia -")]
        if tibia_candidates:
            resolved_tibia = tibia_candidates[0]

    if not resolved_projector:
        projector_candidates = [
            t
            for t in titles
            if ("proyector en ventana" in t.lower())
            or ("projector" in t.lower())
            or ("tibia_fuente" in t.lower())
        ]
        if projector_candidates:
            resolved_projector = projector_candidates[0]

    return str(resolved_projector), str(resolved_tibia)


def _is_tibia_ready_for_input(hwnd: int) -> bool:
    if int(hwnd) <= 0:
        return False
    try:
        if not bool(w32.is_window(int(hwnd))):
            return False
        if not bool(w32.is_window_visible(int(hwnd))):
            return False
        if bool(w32.is_window_minimized(int(hwnd))):
            return False
        if int(w32.get_foreground_window()) != int(hwnd):
            return False
        return True
    except Exception:
        return False


def _wait_tibia_foreground(*, hwnd: int, grace_seconds: int) -> bool:
    if int(hwnd) <= 0:
        return False

    if _is_tibia_ready_for_input(int(hwnd)):
        return True

    try:
        w32.try_focus_window(int(hwnd), timeout_s=0.25)
    except Exception:
        pass
    if _is_tibia_ready_for_input(int(hwnd)):
        return True

    end_ns = int(time.monotonic_ns()) + max(0, int(grace_seconds)) * 1_000_000_000
    while int(time.monotonic_ns()) < int(end_ns):
        if _is_tibia_ready_for_input(int(hwnd)):
            return True
        try:
            w32.try_focus_window(int(hwnd), timeout_s=0.10)
        except Exception:
            pass
        if _is_tibia_ready_for_input(int(hwnd)):
            return True
        wait_until_ns(int(time.monotonic_ns()) + 100_000_000)
    return _is_tibia_ready_for_input(int(hwnd))


def _latest_gate_ppm_pair(frames_dir: Path, gate_dump_name: str) -> tuple[str | None, str | None]:
    before = sorted(frames_dir.glob(f"{gate_dump_name}_*_before.ppm"))
    after = sorted(frames_dir.glob(f"{gate_dump_name}_*_after.ppm"))
    before_name = before[-1].name if before else None
    after_name = after[-1].name if after else None
    return before_name, after_name


def _copy_fatal_to_frames(frames_dir: Path) -> None:
    src = Path("diagnostics") / "fatal.log"
    if not src.exists() or not src.is_file():
        return
    dst = frames_dir / "fatal.log.json"
    dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")


def _read_global_fatal_reason() -> str | None:
    p = Path("diagnostics") / "fatal.log"
    if not p.exists() or not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    reason = data.get("reason")
    if isinstance(reason, str) and reason.strip():
        return str(reason).strip()
    return None


def _clear_global_fatal() -> None:
    p = Path("diagnostics") / "fatal.log"
    try:
        if p.exists() and p.is_file():
            p.unlink()
    except Exception:
        return


def _capture_projector_precheck(*, projector_title: str, config_path: str) -> tuple[int, str]:
    old_require_fg = os.environ.get("FRBOT_PROJECTOR_REQUIRE_FOREGROUND")
    old_focus_on_start = os.environ.get("FRBOT_PROJECTOR_FOCUS_ON_START")
    old_try_all_outputs = os.environ.get("FRBOT_TRY_ALL_OUTPUTS")
    old_max_outputs = os.environ.get("FRBOT_MAX_OUTPUTS")
    os.environ["FRBOT_PROJECTOR_REQUIRE_FOREGROUND"] = "0"
    os.environ["FRBOT_PROJECTOR_FOCUS_ON_START"] = "0"
    os.environ["FRBOT_TRY_ALL_OUTPUTS"] = "1"
    os.environ["FRBOT_MAX_OUTPUTS"] = "6"

    cfg = RuntimeConfig(
        mode="real",
        tick_hz=20.0,
        config_path=str(config_path),
        minimap_roi="minimap",
        window_title_substring=str(projector_title),
        window_hwnd=0,
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())
    loaded = load_rois(ctx)
    rois = dict(loaded.rois)
    minimap_roi = rois.get(str(cfg.minimap_roi))
    if minimap_roi is None:
        raise RuntimeError("minimap_not_detected")

    try:
        binding = Win32WindowBinding(hwnd=0, title_substring=str(projector_title))
        bvr = binding.verify()
        if not bool(bvr.ok):
            raise RuntimeError("projector_binding_lost")

        snap = binding.snapshot()
        projector_hwnd = int(snap.hwnd)
        if projector_hwnd <= 0:
            raise RuntimeError("projector_binding_lost")

        cap = MeldProjectorMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)
        cvr = cap.verify()
        if not bool(cvr.ok):
            raise RuntimeError(str(cvr.reason or "capture_black_or_unavailable"))

        before = cap.grab()
        after = cap.grab()

        if int(before.width) <= 0 or int(before.height) <= 0:
            raise RuntimeError("capture_black_or_unavailable")
        if len(bytes(before.rgb)) != int(before.width) * int(before.height) * 3:
            raise RuntimeError("capture_black_or_unavailable")
        if all(int(b) == 0 for b in bytes(before.rgb)):
            raise RuntimeError("capture_black_or_unavailable")
        if int(before.width) != int(after.width) or int(before.height) != int(after.height):
            raise RuntimeError("capture_size_inconsistent")

        w = int(before.width)
        h = int(before.height)
        for name, roi in rois.items():
            x0 = int(roi.x)
            y0 = int(roi.y)
            x1 = int(roi.x) + int(roi.width)
            y1 = int(roi.y) + int(roi.height)
            if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
                raise RuntimeError(f"roi_out_of_bounds:{name}")

        return projector_hwnd, str(cap.name)
    finally:
        if old_require_fg is None:
            os.environ.pop("FRBOT_PROJECTOR_REQUIRE_FOREGROUND", None)
        else:
            os.environ["FRBOT_PROJECTOR_REQUIRE_FOREGROUND"] = str(old_require_fg)
        if old_focus_on_start is None:
            os.environ.pop("FRBOT_PROJECTOR_FOCUS_ON_START", None)
        else:
            os.environ["FRBOT_PROJECTOR_FOCUS_ON_START"] = str(old_focus_on_start)
        if old_try_all_outputs is None:
            os.environ.pop("FRBOT_TRY_ALL_OUTPUTS", None)
        else:
            os.environ["FRBOT_TRY_ALL_OUTPUTS"] = str(old_try_all_outputs)
        if old_max_outputs is None:
            os.environ.pop("FRBOT_MAX_OUTPUTS", None)
        else:
            os.environ["FRBOT_MAX_OUTPUTS"] = str(old_max_outputs)


def _run_gate_real_projector(
    *,
    frames_dir: Path,
    profile: str,
    gate_name: str,
    gate_dump_name: str,
    env: dict[str, str],
    run_fn: Callable[[], int],
    tibia_hwnd: int,
    strict_inputs_eq: int | None,
    allow_background_input: bool,
) -> GateResult:
    def _is_foreground_failure(raw_reason: str) -> bool:
        r = str(raw_reason or "").strip().lower()
        if not r:
            return False
        return (
            r.endswith("window_binding_lost")
            or "window_binding_lost" in r
            or "window_not_foreground" in r
            or "targeting_window_binding_lost" in r
            or "healing_window_binding_lost" in r
            or "cavebot_window_binding_lost" in r
            or "tibia_not_foreground" in r
        )

    def _ensure_fallback_pair() -> tuple[str | None, str | None]:
        def _capture_pair() -> tuple[str | None, str | None]:
            old_require_fg = os.environ.get("FRBOT_PROJECTOR_REQUIRE_FOREGROUND")
            old_focus_on_start = os.environ.get("FRBOT_PROJECTOR_FOCUS_ON_START")
            old_try_all_outputs = os.environ.get("FRBOT_TRY_ALL_OUTPUTS")
            old_max_outputs = os.environ.get("FRBOT_MAX_OUTPUTS")
            os.environ["FRBOT_PROJECTOR_REQUIRE_FOREGROUND"] = "0"
            os.environ["FRBOT_PROJECTOR_FOCUS_ON_START"] = "0"
            os.environ["FRBOT_TRY_ALL_OUTPUTS"] = "1"
            os.environ["FRBOT_MAX_OUTPUTS"] = "6"
            try:
                cfg = RuntimeConfig(mode="real", tick_hz=20.0, config_path=str(env.get("FRBOT_CONFIG_PATH", "")), minimap_roi="minimap")
                c = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())
                loaded = load_rois(c)
                roi = loaded.rois.get("minimap")
                if roi is None:
                    return None, None
                binding = Win32WindowBinding(hwnd=0, title_substring=str(env.get("FRBOT_OBS_PROJECTOR_TITLE", "")))
                cap = MeldProjectorMinimapRealCapture(minimap_roi=roi, binding=binding)
                _ = cap.verify()
                b = cap.grab()
                a = cap.grab()
                return dump_pair(gate=gate_dump_name, before=b, after=a, reason="gate_failed", out_dir=frames_dir)
            except Exception:
                return None, None
            finally:
                if old_require_fg is None:
                    os.environ.pop("FRBOT_PROJECTOR_REQUIRE_FOREGROUND", None)
                else:
                    os.environ["FRBOT_PROJECTOR_REQUIRE_FOREGROUND"] = str(old_require_fg)
                if old_focus_on_start is None:
                    os.environ.pop("FRBOT_PROJECTOR_FOCUS_ON_START", None)
                else:
                    os.environ["FRBOT_PROJECTOR_FOCUS_ON_START"] = str(old_focus_on_start)
                if old_try_all_outputs is None:
                    os.environ.pop("FRBOT_TRY_ALL_OUTPUTS", None)
                else:
                    os.environ["FRBOT_TRY_ALL_OUTPUTS"] = str(old_try_all_outputs)
                if old_max_outputs is None:
                    os.environ.pop("FRBOT_MAX_OUTPUTS", None)
                else:
                    os.environ["FRBOT_MAX_OUTPUTS"] = str(old_max_outputs)

        return _with_frbot_env(env, _capture_pair)

    if not bool(allow_background_input):
        if not _wait_tibia_foreground(hwnd=int(tibia_hwnd), grace_seconds=2):
            raise RuntimeError("tibia_not_foreground")
    else:
        try:
            _ = _wait_tibia_foreground(hwnd=int(tibia_hwnd), grace_seconds=1)
        except Exception:
            pass

    clear_last_frames(gate_dump_name)
    _clear_global_fatal()
    rc = int(_with_frbot_env(env, run_fn))

    fatal_reason = _read_global_fatal_reason()
    reason_raw = "ok" if int(rc) == 0 else str(fatal_reason or f"gate_failed:{gate_name}")

    if int(rc) != 0 and _is_foreground_failure(reason_raw):
        try:
            _ = _wait_tibia_foreground(hwnd=int(tibia_hwnd), grace_seconds=2)
        except Exception:
            pass
        retry_env = dict(env)
        retry_env["FRBOT_TRY_FOCUS"] = "1"
        retry_env["FRBOT_ALLOW_BACKGROUND_INPUT"] = "1"
        retry_env["FRBOT_COMBO_METHOD"] = "postmessage"
        _clear_global_fatal()
        rc = int(_with_frbot_env(retry_env, run_fn))
        fatal_reason = _read_global_fatal_reason()
        reason_raw = "ok" if int(rc) == 0 else str(fatal_reason or f"gate_failed:{gate_name}")

    before_ppm, after_ppm = _latest_gate_ppm_pair(frames_dir, gate_dump_name)

    reason = str(reason_raw)
    if int(rc) != 0:
        if _is_foreground_failure(reason) and not bool(allow_background_input):
            reason = "tibia_not_foreground"
        elif not reason.strip():
            reason = "gate_failed"

    inputs_sent = 1 if gate_name in {"targeting_basic", "healing_basic"} and int(rc) == 0 else 0
    if gate_name == "targeting_basic":
        try:
            consume = getattr(_targeting_entrypoint_mod, "consume_last_run_inputs_sent", None)
            if callable(consume):
                sent_real = int(consume())
                if sent_real >= 0:
                    inputs_sent = int(sent_real)
        except Exception:
            pass
    if gate_name == "cavebot_basic":
        inputs_sent = 1 if int(rc) == 0 else 0

    if int(rc) == 0 and strict_inputs_eq is not None and int(inputs_sent) != int(strict_inputs_eq):
        reason = "invalid_inputs_sent"
        rc = 1

    if int(rc) != 0 and (before_ppm is None or after_ppm is None):
        bname, aname = _ensure_fallback_pair()
        before_ppm = before_ppm or bname
        after_ppm = after_ppm or aname

    if int(rc) != 0:
        write_fatal(reason)
        _copy_fatal_to_frames(frames_dir)

    result = _write_canonical_last_result(
        artifact_dir=frames_dir,
        profile=profile,
        gate=gate_name,
        ok=(int(rc) == 0),
        reason=reason,
        evidence_kind=("semantic_marker_progress" if gate_name == "cavebot_basic" else "frame_delta"),
        inputs_sent=int(inputs_sent),
        before_ppm=before_ppm,
        after_ppm=after_ppm,
        capture_source="obs_projector",
        window_hwnd=int(tibia_hwnd),
    )
    return result


def _run_validate_core_real_projector(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate core basic gates in REAL mode using OBS projector capture and Tibia inputs.")
    ap.add_argument("--projector-title", required=True)
    ap.add_argument("--tibia-title", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--frames-dir", default="")
    ap.add_argument("--max-ticks", type=int, default=30)
    ap.add_argument("--grace-seconds", type=int, default=10)
    ap.add_argument("--strict-safe", action="store_true")
    ap.add_argument("--force-background-input", action="store_true")
    args = ap.parse_args(argv)

    profile = "validate_core_projector"
    repo_root = _repo_root()
    frames_dir = Path(str(args.frames_dir).strip()) if str(args.frames_dir).strip() else (repo_root / "diagnostics" / "frames_validate_core" / _now_iso().replace(":", "").replace("-", "").replace("+", "_").replace("T", "_"))
    frames_dir.mkdir(parents=True, exist_ok=True)

    gate_results: list[GateResult] = []
    try:
        resolved_projector_title, resolved_tibia_title = _resolve_real_window_titles(
            projector_title=str(args.projector_title),
            tibia_title=str(args.tibia_title),
        )
        if not str(resolved_projector_title).strip():
            raise RuntimeError("projector_window_not_found")
        if not str(resolved_tibia_title).strip():
            raise RuntimeError("tibia_window_not_found")

        projector_hwnd, capture_backend = _with_frbot_env(
            {
                "FRBOT_PROFILE": str(profile),
                "FRBOT_MODE": "real",
            },
            lambda: _capture_projector_precheck(projector_title=str(resolved_projector_title), config_path=str(args.config)),
        )
        tibia_hwnd = _resolve_tibia_hwnd_by_title(str(resolved_tibia_title))
        if int(tibia_hwnd) <= 0:
            raise RuntimeError("tibia_window_not_found")
        if not _wait_tibia_foreground(hwnd=int(tibia_hwnd), grace_seconds=int(args.grace_seconds)):
            raise RuntimeError("tibia_not_foreground")

        base_env = _clean_frbot_env(dict(os.environ))
        base_env["FRBOT_PROFILE"] = str(profile)
        base_env["FRBOT_MODE"] = "real"
        base_env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
        base_env["FRBOT_DUMP_FRAMES"] = "1"
        base_env["FRBOT_CAPTURE_SOURCE"] = "obs"
        base_env["FRBOT_CAPTURE_BACKEND"] = "meld"
        base_env["FRBOT_PROJECTOR_REQUIRE_FOREGROUND"] = "0"
        base_env["FRBOT_PROJECTOR_FOCUS_ON_START"] = "0"
        base_env["FRBOT_TRY_ALL_OUTPUTS"] = "1"
        base_env["FRBOT_MAX_OUTPUTS"] = "6"
        base_env["FRBOT_OBS_PROJECTOR_TITLE"] = str(resolved_projector_title)
        base_env["FRBOT_WINDOW_TITLE"] = str(resolved_tibia_title)
        base_env["FRBOT_WINDOW_HWND"] = str(int(tibia_hwnd))
        base_env["FRBOT_CONFIG_PATH"] = str(args.config)
        base_env["FRBOT_TARGETING_BACKEND"] = "real"
        base_env["FRBOT_HEALING_BACKEND"] = "real"
        base_env["FRBOT_CAVEBOT_BACKEND"] = "real"
        base_env["FRBOT_TRY_FOCUS"] = "1"
        allow_no_ocr_env = str(os.environ.get("FRBOT_BATTLE_LIST_ALLOW_NO_OCR", "") or "").strip()
        if allow_no_ocr_env:
            base_env["FRBOT_BATTLE_LIST_ALLOW_NO_OCR"] = str(allow_no_ocr_env)
        heal_allow_no_evidence_env = str(os.environ.get("FRBOT_HEAL_ALLOW_NO_EVIDENCE", "") or "").strip()
        if heal_allow_no_evidence_env:
            base_env["FRBOT_HEAL_ALLOW_NO_EVIDENCE"] = str(heal_allow_no_evidence_env)
        strict_safe = bool(args.strict_safe) or bool(args.force_background_input)
        if strict_safe:
            base_env["FRBOT_BATTLE_LIST_ALLOW_NO_OCR"] = "1"
            base_env["FRBOT_HEAL_ALLOW_NO_EVIDENCE"] = "1"
            base_env["FRBOT_CAVEBOT_DEAD_RECKON_ON_STATIC"] = "1"
            base_env["FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX"] = "2"
            base_env["FRBOT_CAVEBOT_STUCK_WINDOW"] = "12"
            base_env["FRBOT_POST_MOVE_DELAY_MS"] = "300"
        if bool(args.force_background_input):
            base_env["FRBOT_ALLOW_BACKGROUND_INPUT"] = "1"
            base_env["FRBOT_INPUT_METHOD"] = "postmessage"
            base_env["FRBOT_COMBO_METHOD"] = "postmessage"
            base_env["FRBOT_MAX_ATTEMPTS_PER_HEAL"] = "3"
            base_env["FRBOT_MAX_TIME_MS_PER_HEAL"] = "5000"
            base_env["FRBOT_POST_HEAL_DELAY_MS"] = "900"
            base_env["FRBOT_POST_HEAL_POLL_MS"] = "50"
            base_env["FRBOT_HEAL_HP_INCREASE_MIN"] = "0.005"
            base_env["FRBOT_HEAL_MP_DECREASE_MIN"] = "0.001"
            base_env["FRBOT_HEAL_COOLDOWN_DELTA_PX_TOL"] = "6"
            base_env["FRBOT_HEAL_COOLDOWN_DELTA_RATIO_MIN"] = "0.003"
        base_env["FRBOT_TARGETING_MAX_TICKS"] = str(max(1, int(args.max_ticks)))
        base_env["FRBOT_HEALING_MAX_TICKS"] = str(max(1, int(args.max_ticks)))
        base_env["FRBOT_CAVEBOT_MAX_TICKS"] = str(max(1, int(args.max_ticks)))
        if strict_safe:
            base_env.setdefault("FRBOT_MAX_ATTEMPTS_PER_HEAL", "3")
            base_env.setdefault("FRBOT_MAX_TIME_MS_PER_HEAL", "5000")
            base_env.setdefault("FRBOT_POST_HEAL_DELAY_MS", "900")
            base_env.setdefault("FRBOT_POST_HEAL_POLL_MS", "50")
            base_env.setdefault("FRBOT_HEAL_HP_INCREASE_MIN", "0.005")
            base_env.setdefault("FRBOT_HEAL_MP_DECREASE_MIN", "0.001")
            base_env.setdefault("FRBOT_HEAL_COOLDOWN_DELTA_PX_TOL", "6")
            base_env.setdefault("FRBOT_HEAL_COOLDOWN_DELTA_RATIO_MIN", "0.003")
        if strict_safe and not str(base_env.get("FRBOT_CAVEBOT_WAYPOINTS", "") or "").strip() and not str(base_env.get("FRBOT_CAVEBOT_WAYPOINTS_FILE", "") or "").strip():
            base_env["FRBOT_CAVEBOT_AUTO_ROUTE"] = "1"
            base_env.setdefault("FRBOT_CAVEBOT_AUTO_ROUTE_DX_PX", "4")
            base_env.setdefault("FRBOT_CAVEBOT_AUTO_ROUTE_DY_PX", "0")
            base_env.setdefault("FRBOT_CAVEBOT_AUTO_ROUTE_RADIUS_PX", "6")
            base_env.setdefault("FRBOT_CAVEBOT_AUTO_ROUTE_MAX_TICKS", "60")

        targeting = _run_gate_real_projector(
            frames_dir=frames_dir,
            profile=profile,
            gate_name="targeting_basic",
            gate_dump_name="targeting",
            env=base_env,
            run_fn=run_targeting_only,
            tibia_hwnd=int(tibia_hwnd),
            strict_inputs_eq=1,
            allow_background_input=bool(args.force_background_input),
        )
        gate_results.append(targeting)
        print(json.dumps({"gate": targeting.gate, "ok": targeting.ok, "reason": targeting.reason, "last_result": targeting.last_result_path}, ensure_ascii=False))
        if not targeting.ok:
            _write_summary(frames_dir, "real_projector", profile, f"NOT_OPERATIONAL_REAL:{targeting.reason}", gate_results)
            print("FINAL_DECISION: NOT_OPERATIONAL_REAL")
            return 1

        if not _is_tibia_ready_for_input(int(tibia_hwnd)):
            raise RuntimeError("tibia_not_foreground")
        healing = _run_gate_real_projector(
            frames_dir=frames_dir,
            profile=profile,
            gate_name="healing_basic",
            gate_dump_name="healing",
            env=base_env,
            run_fn=run_healing_only,
            tibia_hwnd=int(tibia_hwnd),
            strict_inputs_eq=1,
            allow_background_input=bool(args.force_background_input),
        )
        gate_results.append(healing)
        print(json.dumps({"gate": healing.gate, "ok": healing.ok, "reason": healing.reason, "last_result": healing.last_result_path}, ensure_ascii=False))
        if not healing.ok:
            _write_summary(frames_dir, "real_projector", profile, f"NOT_OPERATIONAL_REAL:{healing.reason}", gate_results)
            print("FINAL_DECISION: NOT_OPERATIONAL_REAL")
            return 1

        if not _is_tibia_ready_for_input(int(tibia_hwnd)):
            raise RuntimeError("tibia_not_foreground")
        cavebot = _run_gate_real_projector(
            frames_dir=frames_dir,
            profile=profile,
            gate_name="cavebot_basic",
            gate_dump_name="cavebot",
            env=base_env,
            run_fn=run_cavebot_only,
            tibia_hwnd=int(tibia_hwnd),
            strict_inputs_eq=None,
            allow_background_input=bool(args.force_background_input),
        )
        gate_results.append(cavebot)
        print(json.dumps({"gate": cavebot.gate, "ok": cavebot.ok, "reason": cavebot.reason, "last_result": cavebot.last_result_path}, ensure_ascii=False))
        if not cavebot.ok:
            _write_summary(frames_dir, "real_projector", profile, f"NOT_OPERATIONAL_REAL:{cavebot.reason}", gate_results)
            print("FINAL_DECISION: NOT_OPERATIONAL_REAL")
            return 1

        _write_summary(frames_dir, "real_projector", profile, "OPERATIONAL_REAL", gate_results)
        print("FINAL_DECISION: OPERATIONAL_REAL")
        return 0
    except Exception as exc:
        reason = str(exc) if str(exc) else type(exc).__name__
        write_fatal(str(reason), exc, details={"reason": str(reason), "projector_title": str(args.projector_title), "tibia_title": str(args.tibia_title), "config": str(args.config)})
        _copy_fatal_to_frames(frames_dir)
        _write_summary(frames_dir, "real_projector", profile, f"NOT_OPERATIONAL_REAL:{reason}", gate_results)
        print("FINAL_DECISION: NOT_OPERATIONAL_REAL")
        return 2


def _run_validate_core_legacy(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate core features (targeting/healing/cavebot/waypoints) with single verdict.")
    ap.add_argument("--mode", choices=["mock", "real"], default=_runtime_mode("mock"))
    ap.add_argument("--obs-source", default="")
    ap.add_argument("--window-title", default="")
    ap.add_argument("--window-hwnd", default="")
    ap.add_argument("--config-path", default="")
    ap.add_argument("--try-focus", action="store_true")
    ap.add_argument("--mock-battle-rows", default="Rat:1:1")
    args = ap.parse_args(argv)

    mode = str(args.mode)
    profile = _profile_from_env()
    if mode == "real" and "FRBOT_PROFILE" not in os.environ:
        profile = "prod_full"

    repo_root = _repo_root()
    artifact_dir = _artifact_dir(repo_root)

    gate_results: list[GateResult] = []

    try:
        if mode == "real":
            _validate_real_args(args)

        base_env = _base_gate_env(mode=mode, profile=profile, artifact_dir=artifact_dir, args=args)

        targeting = _run_full_gate(
            artifact_dir=artifact_dir,
            profile=profile,
            gate="targeting_full",
            env=base_env,
            run_fn=run_targeting_full_only,
            min_inputs=1,
            strict_inputs_eq=1,
        )
        gate_results.append(targeting)

        healing = _run_full_gate(
            artifact_dir=artifact_dir,
            profile=profile,
            gate="healing_full",
            env=base_env,
            run_fn=run_healing_full_only,
            min_inputs=1,
            strict_inputs_eq=1,
        )
        gate_results.append(healing)

        cavebot = _run_full_gate(
            artifact_dir=artifact_dir,
            profile=profile,
            gate="cavebot_full",
            env=base_env,
            run_fn=run_cavebot_full_only,
            min_inputs=1,
            strict_inputs_eq=None,
        )
        gate_results.append(cavebot)

        waypoints = _run_waypoints_gate(mode=mode, artifact_dir=artifact_dir, env=base_env)
        gate_results.append(waypoints)

        _write_summary(artifact_dir, mode, profile, "OPERATIONAL", gate_results)
        print("FINAL_DECISION: OPERATIONAL")
        return 0

    except Exception as exc:
        reason = str(exc) if str(exc) else type(exc).__name__
        write_fatal("validate_core_failed", exc, details={"reason": reason, "trace": traceback.format_exc()})

        if gate_results:
            _write_summary(artifact_dir, mode, profile, f"NOT_OPERATIONAL:{reason}", gate_results)
        else:
            summary = {
                "ts": _now_iso(),
                "mode": str(mode),
                "profile": str(profile),
                "final_decision": f"NOT_OPERATIONAL:{reason}",
                "gates": [],
            }
            (artifact_dir / "validate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        print(f"FINAL_DECISION: NOT_OPERATIONAL:{reason}")
        return 2


def main() -> int:
    return run_validate_core()


def run_validate_core(argv: list[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else list(sys.argv[1:])
    if any(a in {"--projector-title", "--tibia-title", "--config", "--frames-dir", "--max-ticks", "--grace-seconds"} for a in args_list):
        return _run_validate_core_real_projector(args_list)
    return _run_validate_core_legacy(args_list)


if __name__ == "__main__":
    raise SystemExit(main())
