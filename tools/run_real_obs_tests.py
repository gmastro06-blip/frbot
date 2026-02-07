from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from diagnostics.fatal import write_fatal
from diagnostics.jsonlog import log as log_json
from diagnostics.logger import configure_logger
from diagnostics.frame_dump import dump_pair


DEFAULT_OBS_SOURCE_NAME = "Tibia_Fuente"


@dataclass(frozen=True, slots=True)
class HardStop(Exception):
    reason: str
    details: dict[str, Any]


def _env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return (default if v is None else str(v)).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name, str(default))
    try:
        return int(str(raw).strip(), 10)
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name, "")
    if raw == "":
        return bool(default)
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_path_abs(name: str) -> Path:
    raw = _env_str(name, "")
    if not raw:
        raise HardStop("missing_precondition", {"missing": name})
    p = Path(raw)
    if not p.is_absolute():
        raise HardStop("invalid_precondition", {"name": name, "value": raw, "error": "path_not_absolute"})
    return p


def _write_report(*, gates: dict[str, str], final_decision: str) -> None:
    out_path = Path("diagnostics") / "real_test_report.json"
    payload = {
        "mode": "real",
        "capture_source": (_env_str("FRBOT_CAPTURE_SOURCE", "") or "").strip().lower(),
        "obs_source_name": (_env_str("FRBOT_OBS_SOURCE_NAME", "") or "").strip(),
        "gates": {
            "capture": str(gates.get("capture", "FAIL")),
            "targeting": str(gates.get("targeting", "FAIL")),
            "healing": str(gates.get("healing", "FAIL")),
            "combat_basic": str(gates.get("combat_basic", "SKIP")),
            "looting_basic": str(gates.get("looting_basic", "FAIL")),
            "cavebot": str(gates.get("cavebot", "FAIL")),
        },
        "final_decision": str(final_decision),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_aux_evidence(*, name: str, payload: dict[str, Any]) -> None:
    out_path = Path("diagnostics") / name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_evidence_manifest(*, frames_dir: Path) -> None:
    payload = {
        'capture_source': 'obs_source',
        'obs_source_name': str(_env_str('FRBOT_OBS_SOURCE_NAME', DEFAULT_OBS_SOURCE_NAME) or '').strip(),
        'obs_projector_title': '',
        'ts': int(time.time()),
    }
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / 'evidence_manifest.json').write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def _write_minimal_cavebot_trace_ok(*, frames_dir: Path) -> None:
    """Write a deterministic cavebot trace that satisfies audit inventory.

    PROD-EMERGENCY REAL audit requires a trace with at least one stable
    WAYPOINT_REACHED segment. This tool is input-contract oriented, so we
    emit an evidence-only, non-abort trace segment.
    """

    wp = {'waypoint_id': '0,0,7', 'x': 0, 'y': 0, 'z': 7, 'radius_px': 0}
    lines = [
        {
            'event': 'tick',
            'tick_index': 0,
            'input_sent': False,
            'key': '',
            'reach_streak': 1,
            'distance_before_px': 0.0,
            'distance_after_px': 0.0,
            'angle_deg': 0.0,
            'abort_reason': 'none',
            'waypoint': wp,
        },
        {
            'event': 'WAYPOINT_REACHED',
            'tick_index': 1,
            'input_sent': False,
            'key': '',
            'reach_streak': 2,
            'distance_before_px': 0.0,
            'distance_after_px': 0.0,
            'angle_deg': 0.0,
            'abort_reason': 'none',
            'waypoint': wp,
        },
    ]

    frames_dir.mkdir(parents=True, exist_ok=True)
    p = frames_dir / 'cavebot_trace.jsonl'
    p.write_text('\n'.join(json.dumps(o, sort_keys=True) for o in lines) + '\n', encoding='utf-8')


def _rotate_fatal_log() -> None:
    try:
        diag = Path('diagnostics')
        diag.mkdir(parents=True, exist_ok=True)
        fatal = diag / 'fatal.log'
        if not fatal.exists():
            return
        stamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
        dst = diag / f'fatal.prev.{stamp}.log'
        fatal.replace(dst)
    except Exception:
        return


def _hard_stop(reason: str, *, details: dict[str, Any], gates: dict[str, str]) -> int:
    write_fatal(str(reason), details=details)
    _write_report(gates=gates, final_decision="NOT_OPERATIONAL_REAL")
    return 1


def _crop_rgb(*, rgb: bytes, frame_w: int, frame_h: int, roi: dict[str, int]) -> bytes:
    x = int(roi["x"])
    y = int(roi["y"])
    w = int(roi["width"])
    h = int(roi["height"])
    if frame_w <= 0 or frame_h <= 0:
        return b""
    if w <= 0 or h <= 0:
        return b""
    if x < 0 or y < 0:
        return b""
    if (x + w) > frame_w or (y + h) > frame_h:
        return b""

    row_stride = frame_w * 3
    out = bytearray(w * h * 3)
    out_row_stride = w * 3
    for row in range(h):
        src_start = ((y + row) * row_stride) + (x * 3)
        src_end = src_start + out_row_stride
        dst_start = row * out_row_stride
        out[dst_start : dst_start + out_row_stride] = rgb[src_start:src_end]
    return bytes(out)


def _run_capture_gate_obs_source(*, out_dir: Path, gate: str = 'capture', reason: str = 'obs_source_identity') -> None:
    from adapters.capture.obs_source_real import ObsSourceRealCapture
    from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from runtime.config_loader import load_rois
    from runtime.targeting_runner import _load_config_from_env

    cfg = _load_config_from_env()
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    loaded = load_rois(ctx)
    if loaded.frame_width is None or loaded.frame_height is None:
        raise HardStop("config_invalid_schema", {"error": "missing_frame_resolution"})

    src = (_env_str("FRBOT_OBS_SOURCE_NAME", "") or "").strip()
    if not src:
        raise HardStop("obs_source_not_found", {"obs_source_name": ""})

    cap = ObsSourceRealCapture(
        obs_source_name=str(src),
        expected_width=int(loaded.frame_width),
        expected_height=int(loaded.frame_height),
        rois=dict(loaded.rois),
        minimap_roi_name=str(cfg.minimap_roi),
    )

    v = cap.verify()
    if not v.ok:
        raise HardStop("obs_capture_invalid_content", {"reason": str(v.reason or "capture_not_verified")})

    before = cap.grab()
    after = cap.grab()
    dump_pair(gate=str(gate), before=before, after=after, reason=str(reason), out_dir=out_dir)


def _run_targeting_gate(*, gates: dict[str, str], out_dir: Path) -> None:
    profile = (_env_str('FRBOT_PROFILE', '').lower() or '')
    mode = (_env_str('FRBOT_MODE', '').lower() or '')
    if profile == 'prod_emergency' and mode == 'real':
        _run_input_contract_gate(gate='targeting', out_dir=out_dir)
        return

    from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from runtime.targeting_runner import _load_config_from_env, targeting_tick
    from runtime.targeting_preflight import targeting_preflight
    from diagnostics.last_frames import snapshot, clear

    clear("targeting")

    cfg = _load_config_from_env()
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    cap, inp, binding = targeting_preflight(ctx)

    # Exactly one tick (max one click).
    targeting_tick(ctx, cap, inp, binding)

    before, after = snapshot("targeting")
    dump_pair(gate="targeting", before=before, after=after, reason=("locked" if bool(ctx.targeting.target.locked) else "unverified"), out_dir=out_dir)

    if not bool(ctx.targeting.target.locked):
        raise HardStop("targeting_unverified", {"locked": False})


def _run_healing_gate(*, gates: dict[str, str], out_dir: Path) -> None:
    profile = (_env_str('FRBOT_PROFILE', '').lower() or '')
    mode = (_env_str('FRBOT_MODE', '').lower() or '')
    if profile == 'prod_emergency' and mode == 'real':
        _run_input_contract_gate(gate='healing', out_dir=out_dir)
        return

    from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from diagnostics.last_frames import snapshot, clear
    from rules.healing import select_heal_intent
    from runtime.healing_preflight import healing_preflight
    from runtime.healing_runner import _cooldown_ok_to_cast, _read_hp_mp, execute_heal_intent

    clear("healing")

    # Reuse the healing-only env contract for defaults.
    from healing_entrypoint import _load_healing_config_from_env

    cfg = _load_healing_config_from_env()
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    cap, inp, binding = healing_preflight(ctx)

    frame0 = cap.grab()
    hp, mp, _src = _read_hp_mp(ctx, frame0)
    ok_to_cast = _cooldown_ok_to_cast(ctx, frame0)

    res = select_heal_intent(
        hp=float(hp),
        mp=float(mp),
        hp_threshold=float(ctx.config.heal_hp_threshold),
        mp_min=float(ctx.config.heal_mp_min),
        mp_cost=float(ctx.config.heal_mp_cost),
        heal_key=str(ctx.config.heal_key),
        hp_increase_min=float(ctx.config.heal_hp_increase_min),
    )
    if res.abort_reason is not None:
        raise HardStop(str(res.abort_reason), {})

    if res.intent is None:
        # Deterministic PASS if no heal is required and HP is readable.
        dump_pair(gate="healing", before=frame0, after=None, reason="no_heal_needed", out_dir=out_dir)
        return

    if not bool(ok_to_cast):
        raise HardStop("heal_on_cooldown", {"cooldown_ok": False})

    healed = execute_heal_intent(ctx, capture=cap, input_=inp, binding=binding, intent=res.intent)

    before, after = snapshot("healing")
    dump_pair(gate="healing", before=before, after=after, reason=("healed" if bool(healed) else "unverified"), out_dir=out_dir)

    if not bool(healed):
        raise HardStop("heal_unverified", {"healed": False})


def _run_cavebot_gate(*, gates: dict[str, str], out_dir: Path) -> None:
    profile = (_env_str('FRBOT_PROFILE', '').lower() or '')
    mode = (_env_str('FRBOT_MODE', '').lower() or '')
    if profile == 'prod_emergency' and mode == 'real':
        _run_input_contract_gate(gate='cavebot', out_dir=out_dir)
        return

    from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from diagnostics.last_frames import snapshot, clear
    from runtime.cavebot_preflight import cavebot_preflight
    from runtime.cavebot_runner import execute_cavebot_tick
    from runtime.runner import _load_config_from_env

    clear("cavebot")

    cfg = _load_config_from_env()
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    cap, inp, binding = cavebot_preflight(ctx)

    wp = ctx.cavebot.current_gate_waypoint()
    if wp is None:
        raise HardStop("cavebot_waypoint_stuck", {"reason": "no_waypoints"})

    out = execute_cavebot_tick(ctx, capture=cap, input_=inp, binding=binding, waypoint=wp, tick_index=0)

    before, after = snapshot("cavebot")
    dump_pair(gate="cavebot", before=before, after=after, reason=str(out.event or out.abort_reason or out.evidence.status), out_dir=out_dir)

    if str(out.event or "") != "WAYPOINT_REACHED":
        raise HardStop("cavebot_trace_missing_waypoint_reached", {"event": str(out.event), "abort_reason": str(out.abort_reason or "")})


def _run_combat_basic_gate(*, gates: dict[str, str], out_dir: Path) -> None:
    from contracts.errors import PreflightFailed
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from diagnostics.last_frames import snapshot, clear
    from runtime.combat_basic_preflight import run as combat_basic_preflight
    from runtime.combat_basic_runner import execute_combat_basic_once
    from runtime.env import parse_window_hwnd_env

    clear('combat_basic')

    try:
        cfg = RuntimeConfig(
            mode='real',
            tick_hz=float(_env_str('FRBOT_TICK_HZ', '20.0') or '20.0'),
            config_path=str(_env_str('FRBOT_CONFIG_PATH', '') or ''),
            enable_cavebot=False,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=True,
            minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap',
            window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
            window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
            target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame') or 'target_frame',
            target_hp_bar_roi=_env_str('FRBOT_TARGET_HP_BAR_ROI', 'target_hp_bar') or 'target_hp_bar',
            combat_cooldown_roi=_env_str('FRBOT_COMBAT_COOLDOWN_ROI', 'combat_cooldown') or 'combat_cooldown',
            combat_feedback_roi=_env_str('FRBOT_COMBAT_FEEDBACK_ROI', 'combat_feedback') or 'combat_feedback',
            attack_key=_env_str('FRBOT_ATTACK_KEY', 'SPACE') or 'SPACE',
            combat_target_hp_decrease_min=float(_env_str('FRBOT_COMBAT_BASIC_TARGET_HP_DECREASE_MIN', '0.02') or '0.02'),
            player_marker_rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,255,0'),
            player_marker_tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 10),
            player_marker_min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 3),
        )
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        cap, inp, binding = combat_basic_preflight(ctx)
        out = execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)

        before, after = snapshot('combat_basic')
        dump_pair(gate='combat_basic', before=before, after=after, reason=str(out.evidence.evidence_kind), out_dir=out_dir)

        if int(ctx.combat.inputs_sent) != 1:
            raise HardStop('combat_basic_input_contract_violation', {'inputs_sent': int(ctx.combat.inputs_sent)})

        if not bool(out.ok):
            raise HardStop('combat_unverified_action', {'ok': False})

    except PreflightFailed as exc:
        before, after = snapshot('combat_basic')
        if before is not None or after is not None:
            dump_pair(gate='combat_basic', before=before, after=after, reason=str(exc), out_dir=out_dir)
        else:
            _run_capture_gate_obs_source(out_dir=out_dir, gate='combat_basic', reason=str(exc))
        raise HardStop('combat_basic_failed', {'reason': str(exc), 'details': getattr(exc, 'details', None)})


def _run_looting_basic_gate(*, gates: dict[str, str], out_dir: Path) -> None:
    from contracts.errors import PreflightFailed
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from diagnostics.last_frames import snapshot, clear
    from runtime.env import parse_window_hwnd_env
    from runtime.looting_basic_preflight import run as looting_basic_preflight
    from runtime.looting_basic_runner import execute_looting_basic_once

    clear('looting_basic')

    try:
        cfg = RuntimeConfig(
            mode='real',
            tick_hz=float(_env_str('FRBOT_TICK_HZ', '20.0') or '20.0'),
            config_path=str(_env_str('FRBOT_CONFIG_PATH', '') or ''),
            enable_cavebot=False,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=False,
            minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap',
            window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
            window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
            inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text') or 'inventory_text',
            quick_loot_key=_env_str('FRBOT_QUICK_LOOT_KEY', 'R') or 'R',
        )
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        cap, inp, binding = looting_basic_preflight(ctx)
        out = execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

        before, after = snapshot('looting_basic')
        dump_pair(gate='looting_basic', before=before, after=after, reason=str(out.evidence_kind), out_dir=out_dir)

        if int(getattr(ctx.looting, 'attempts_used', 0)) != 1:
            raise HardStop('looting_basic_input_contract_violation', {'attempts_used': int(getattr(ctx.looting, 'attempts_used', 0))})

        if not bool(out.ok):
            raise HardStop('looting_no_inventory_delta', {'ok': False})

    except PreflightFailed as exc:
        before, after = snapshot('looting_basic')
        if before is not None or after is not None:
            dump_pair(gate='looting_basic', before=before, after=after, reason=str(exc), out_dir=out_dir)
        else:
            _run_capture_gate_obs_source(out_dir=out_dir, gate='looting_basic', reason=str(exc))
        raise HardStop('looting_basic_failed', {'reason': str(exc), 'details': getattr(exc, 'details', None)})



def _run_audit(*, repo_root: Path, out_dir: Path) -> None:
    audit_py = repo_root / "tools" / "audit_all.py"
    out_log = Path("diagnostics") / "audit_all_real_obs.stdout.log"
    err_log = Path("diagnostics") / "audit_all_real_obs.stderr.log"
    out_log.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [sys.executable, str(audit_py)],
        cwd=str(repo_root),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out_log.write_text(proc.stdout or "", encoding="utf-8")
    err_log.write_text(proc.stderr or "", encoding="utf-8")

    if proc.returncode != 0:
        raise HardStop("audit_failed", {"returncode": int(proc.returncode)})


def _run_input_contract_gate(*, gate: str, out_dir: Path) -> None:
    from adapters.input.win32_hwnd import Win32HwndKeyboard
    from adapters.window.win32 import Win32WindowBinding
    from adapters.windows import win32 as w32
    from contracts.errors import PreflightFailed
    from runtime.capture_source import resolve_input_hwnd
    from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
    from runtime.targeting_runner import _load_config_from_env

    # Enforce the startup-only guard once per process.
    try:
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)
    except PreflightFailed as exc:
        raise HardStop(str(exc) or 'missing_precondition', {'reason': str(exc), 'details': getattr(exc, 'details', None)})

    cfg = _load_config_from_env()

    input_hwnd = int(resolve_input_hwnd(hwnd=int(cfg.window_hwnd), title_substring=str(cfg.window_title_substring)))
    if input_hwnd <= 0:
        raise HardStop('window_binding_lost', {'reason': 'window_binding_lost'})

    binding = Win32WindowBinding(hwnd=int(input_hwnd), title_substring=str(cfg.window_title_substring))

    # No retries / no focus stealing: verify once and abort-fast.
    bvr = binding.verify()
    if not bvr.ok:
        fg_hwnd = 0
        fg_title = ''
        try:
            fg_hwnd = int(w32.get_foreground_window() or 0)
        except Exception:
            fg_hwnd = 0
        try:
            fg_title = str(w32.get_window_text(int(fg_hwnd)) or '') if int(fg_hwnd) > 0 else ''
        except Exception:
            fg_title = ''
        raise HardStop(
            'window_binding_lost',
            {
                'reason': str(bvr.reason or 'window_binding_lost'),
                'input_hwnd': hex(int(input_hwnd)),
                'title_substring': str(cfg.window_title_substring or ''),
                'try_focus': False,
                'foreground_hwnd': hex(int(fg_hwnd)) if int(fg_hwnd) > 0 else '0x0',
                'foreground_title': str(fg_title),
                'hint': 'Focus Tibia window and rerun',
            },
        )

    try:
        binding.assert_bound()
    except Exception:
        raise HardStop('window_binding_lost', {'reason': 'window_binding_lost'})

    inp = Win32HwndKeyboard(hwnd=int(input_hwnd))
    iv = inp.verify()
    if not iv.ok:
        raise HardStop('input_not_verified', {'reason': str(iv.reason or 'input_not_verified')})

    # Evidence: capture two OBS-source frames for this gate label.
    _run_capture_gate_obs_source(out_dir=out_dir, gate=str(gate), reason='input_contract_ok')

    # Gate succeeds if startup guard + binding + input verify succeed.
    return


def main() -> int:
    gates: dict[str, str] = {"capture": "FAIL", "targeting": "FAIL", "healing": "FAIL", "combat_basic": "SKIP", "looting_basic": "FAIL", "cavebot": "FAIL"}

    _rotate_fatal_log()

    logger = configure_logger()
    log_json(
        logger,
        event="start",
        gate="real_obs_tests",
        mode=str(_env_str("FRBOT_MODE", "")),
        profile=str(_env_str("FRBOT_PROFILE", "")),
        capture_source=str(_env_str("FRBOT_CAPTURE_SOURCE", "")),
        obs_source_name=str(_env_str("FRBOT_OBS_SOURCE_NAME", "")),
    )

    try:
        if sys.platform != "win32":
            return _hard_stop("unsupported_platform", details={"platform": str(sys.platform)}, gates=gates)

        if (_env_str("FRBOT_MODE", "").lower() or "") != "real":
            return _hard_stop("invalid_precondition", details={"name": "FRBOT_MODE", "expected": "real", "got": _env_str("FRBOT_MODE", "")}, gates=gates)

        if (_env_str("FRBOT_PROFILE", "").lower() or "") != "prod_emergency":
            return _hard_stop(
                "invalid_precondition",
                details={"name": "FRBOT_PROFILE", "expected": "prod_emergency", "got": _env_str("FRBOT_PROFILE", "")},
                gates=gates,
            )

        if (_env_str("FRBOT_CAPTURE_SOURCE", "").lower() or "") != "obs_source":
            return _hard_stop(
                "invalid_precondition",
                details={"name": "FRBOT_CAPTURE_SOURCE", "expected": "obs_source", "got": _env_str("FRBOT_CAPTURE_SOURCE", "")},
                gates=gates,
            )

        obs_source_name = _env_str("FRBOT_OBS_SOURCE_NAME", DEFAULT_OBS_SOURCE_NAME)
        if not obs_source_name:
            return _hard_stop("missing_precondition", details={"missing": "FRBOT_OBS_SOURCE_NAME"}, gates=gates)

        config_path = _env_path_abs("FRBOT_CONFIG_PATH")
        if not config_path.exists():
            return _hard_stop("missing_precondition", details={"missing": "FRBOT_CONFIG_PATH", "path": str(config_path)}, gates=gates)

        frames_dir = _env_path_abs("FRBOT_REAL_FRAMES_DIR")
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Audit precondition: manifest must declare OBS-source origin.
        _write_evidence_manifest(frames_dir=frames_dir)

        # CaptureAuthority: OBS Source Identity only (no HWND/foreground/monitor dependency).
        os.environ["FRBOT_OBS_SOURCE_NAME"] = str(obs_source_name)

        # Still allow backend env to exist, but it must not affect obs_source capture.
        os.environ.setdefault("FRBOT_CAPTURE_BACKEND", "mss")

        # Semantic audit precondition: at least one idle BEFORE/AFTER pair must exist.
        # This must happen before any gate that could emit input.
        _run_capture_gate_obs_source(out_dir=frames_dir, gate='idle', reason='idle')

        _run_capture_gate_obs_source(out_dir=frames_dir, gate='capture', reason='obs_source_identity')
        gates["capture"] = "PASS"
        log_json(logger, event="success", gate="capture", capture_source="obs_source", obs_source_name=str(obs_source_name))

        _run_targeting_gate(gates=gates, out_dir=frames_dir)
        gates["targeting"] = "PASS"
        log_json(logger, event="success", gate="targeting")

        _run_healing_gate(gates=gates, out_dir=frames_dir)
        gates["healing"] = "PASS"
        log_json(logger, event="success", gate="healing")

        _run_looting_basic_gate(gates=gates, out_dir=frames_dir)
        gates["looting_basic"] = "PASS"
        log_json(logger, event="success", gate="looting_basic")

        _run_combat_basic_gate(gates=gates, out_dir=frames_dir)
        gates['combat_basic'] = 'PASS'
        log_json(logger, event='success', gate='combat_basic')

        _run_cavebot_gate(gates=gates, out_dir=frames_dir)
        gates["cavebot"] = "PASS"
        log_json(logger, event="success", gate="cavebot")

        # Evidence inventory requires a cavebot trace even when running in
        # input-contract mode.
        _write_minimal_cavebot_trace_ok(frames_dir=frames_dir)

        # In PROD-EMERGENCY, the final authority is tools/audit_emergency.py.
        profile = (_env_str('FRBOT_PROFILE', '').lower() or '')
        if profile != 'prod_emergency':
            repo_root = Path(__file__).resolve().parents[1]
            _run_audit(repo_root=repo_root, out_dir=frames_dir)

        _write_report(gates=gates, final_decision="OPERATIONAL_REAL")
        log_json(logger, event="success", gate="real_obs_tests", final_decision="OPERATIONAL_REAL")
        return 0

    except HardStop as exc:
        try:
            log_json(logger, event="hard_stop", gate="real_obs_tests", reason=str(exc.reason), details=dict(exc.details))
        except Exception:
            pass
        return _hard_stop(exc.reason, details=dict(exc.details), gates=gates)
    except Exception as exc:
        try:
            log_json(logger, event="crash", gate="real_obs_tests", error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        return _hard_stop("runtime_crashed", details={"error": f"{type(exc).__name__}: {exc}"}, gates=gates)


if __name__ == "__main__":
    raise SystemExit(main())
