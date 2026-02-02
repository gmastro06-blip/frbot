from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


_GATES: tuple[str, ...] = ("targeting", "healing", "combat", "cavebot", "looting", "deposit", "trade")


class CalibrationHardStop(RuntimeError):
    pass


def _hard_stop(reason: str, **extra: Any) -> CalibrationHardStop:
    payload: dict[str, Any] = {"reason": str(reason)}
    payload.update(extra)
    # Deterministic structured evidence.
    return CalibrationHardStop(json.dumps(payload, indent=2, sort_keys=True))


def _ts() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _safe_reason(reason: str) -> str:
    import re

    s = (reason or "unknown").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] if s else "unknown"


def _ensure_repo_root_on_syspath() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        # base=0 accepts decimal and 0x... hex.
        return int(str(raw).strip(), 0) if raw is not None else int(default)
    except Exception:
        return int(default)


def _capture_backend() -> str:
    return (_env_str('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()


def _backend_is_projector(backend: str) -> bool:
    b = (backend or '').strip().lower()
    return b in {'projector', 'meld-projector', 'obs-projector'}


def _require_env_path(name: str) -> Path:
    raw = _env_str(name, "")
    if not raw:
        raise _hard_stop("missing_precondition", missing=name)
    p = Path(raw)
    if not p.is_absolute():
        raise _hard_stop("invalid_precondition", name=name, value=raw, error="path_not_absolute")
    return p


def _resolve_tibia_hwnd() -> tuple[int, str]:
    """Resolve the Tibia HWND deterministically.

    Priority:
      1) FRBOT_WINDOW_HWND (explicit)
      2) Find window by FRBOT_WINDOW_TITLE substring (default: 'Tibia')
    """

    from adapters.windows.win32 import (
        find_window_by_title_substring,
        get_window_text,
        is_window,
        is_window_minimized,
        is_window_visible,
    )

    hwnd_raw = _env_str("FRBOT_WINDOW_HWND", "")
    title_raw = _env_str("FRBOT_WINDOW_TITLE", "")

    # Spec: require an explicit selector (HWND or title). No silent defaults.
    if not hwnd_raw and not title_raw:
        raise _hard_stop("missing_precondition", missing="FRBOT_WINDOW_HWND_or_FRBOT_WINDOW_TITLE")

    hwnd = _env_int("FRBOT_WINDOW_HWND", 0)
    if hwnd > 0:
        if not is_window(hwnd):
            raise _hard_stop("invalid_hwnd", expected_hwnd=hex(int(hwnd)))
        if not is_window_visible(hwnd):
            raise _hard_stop("hwnd_not_visible", expected_hwnd=hex(int(hwnd)))
        if is_window_minimized(hwnd):
            raise _hard_stop("hwnd_minimized", expected_hwnd=hex(int(hwnd)))
        return hwnd, (get_window_text(hwnd) or "")

    needle = title_raw
    match = find_window_by_title_substring(needle)
    if match is None:
        raise _hard_stop("window_not_found", expected_title_substring=needle)
    if not is_window_visible(int(match.hwnd)):
        raise _hard_stop("hwnd_not_visible", expected_hwnd=hex(int(match.hwnd)), expected_title=match.title)
    if is_window_minimized(int(match.hwnd)):
        raise _hard_stop("hwnd_minimized", expected_hwnd=hex(int(match.hwnd)), expected_title=match.title)
    return int(match.hwnd), str(match.title or "")


def _require_tibia_foreground() -> int:
    import time

    from adapters.windows.win32 import get_foreground_window, get_window_text, try_focus_window

    tibia_hwnd, tibia_title = _resolve_tibia_hwnd()
    retries = int(os.environ.get('FRBOT_FOREGROUND_RETRIES', '10') or '10')
    delay_ms = int(os.environ.get('FRBOT_FOREGROUND_RETRY_DELAY_MS', '150') or '150')

    last_fg = 0
    last_title = ''
    for attempt in range(max(0, retries) + 1):
        fg = int(get_foreground_window() or 0)
        last_fg = fg
        last_title = get_window_text(fg) if fg > 0 else ''
        if fg == tibia_hwnd:
            return tibia_hwnd

        if attempt < retries:
            try_focus_window(tibia_hwnd)
            time.sleep(max(0.0, float(delay_ms)) / 1000.0)

    raise _hard_stop(
        "foreground_mismatch",
        expected_hwnd=hex(int(tibia_hwnd)),
        expected_title=tibia_title,
        foreground_hwnd=hex(int(last_fg)),
        foreground_title=last_title,
        retries=retries,
        delay_ms=delay_ms,
        hint="Terminal/IDE may have stolen focus. Run via tools/run_calibration_hidden.ps1, and keep Tibia focused until capture starts.",
    )


def _require_preconditions(*, repo_root: Path) -> tuple[Path, Path, int]:
    # Required by spec: explicit output paths.
    frames_dir = _require_env_path("FRBOT_REAL_FRAMES_DIR")
    config_path = _require_env_path("FRBOT_CONFIG_PATH")

    # Strict window binding precondition (do this before checking capture deps so
    # a console-foreground mismatch is reported deterministically).
    tibia_hwnd = _require_tibia_foreground()

    # Ensure we can capture in real mode.
    backend = _capture_backend()
    if _backend_is_projector(backend):
        try:
            import dxcam  # type: ignore  # noqa: F401
        except Exception:
            raise _hard_stop(
                'missing_dependency',
                dependency='dxcam',
                hint='Install projector capture deps: pip install dxcam (or poetry add dxcam).',
            )
    else:
        try:
            import mss  # noqa: F401
        except Exception:
            raise _hard_stop(
                "missing_dependency",
                dependency="mss",
                hint="Install real capture deps: pip install mss (or poetry add mss).",
            )

    # Do not create any outputs until all preconditions are met.
    frames_dir.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    return frames_dir, config_path, tibia_hwnd


def _normalize_version_tag(version: str) -> str:
    v = (version or '').strip().lower()
    if v in {'15.x', '15x', 'x'}:
        return '15x'
    if v in {'15.y', '15y', 'y'}:
        return '15y'
    raise _hard_stop('invalid_version', expected='15.x|15.y', got=version)


def _frames_gates_root(*, repo_root: Path, version_tag: str) -> Path:
    # Versioned, per-gate evidence tree.
    # IMPORTANT: must NOT live under diagnostics/frames_<tag>/, because the flat
    # aggregation step clears diagnostics/frames_<tag>/ deterministically.
    return repo_root / 'diagnostics' / f'frames_{version_tag}_gates'


def _validate_canonical_rois_config(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise _hard_stop('config_invalid_schema', path=str(path), error=f'{type(exc).__name__}:{exc}')
    if not isinstance(data, dict) or set(data.keys()) != {'rois'}:
        raise _hard_stop('config_invalid_schema', path=str(path), error='top_level_must_be_only_rois')
    rois = data.get('rois')
    if not isinstance(rois, dict):
        raise _hard_stop('config_invalid_schema', path=str(path), error='rois_must_be_object')



@dataclass(frozen=True, slots=True)
class GateRunResult:
    gate: str
    returncode: int
    before_path: Path
    after_path: Path
    stdout_tail: str


class _VerifiedFrameCapture(Protocol):
    def verify(self) -> Any: ...
    def grab(self) -> Any: ...


def _capture_full_frame_ppm(*, gate: str, side: str, reason: str, out_dir: Path, stamp: str | None = None) -> Path:
    # Capture via the selected backend.
    # Default is HWND-bound MSS; projector mode uses dxcam screen capture.
    from adapters.window.win32 import Win32WindowBinding
    from diagnostics.frame_dump import dump_frame_ppm

    backend = _capture_backend()

    # Bind explicitly to the resolved Tibia HWND (and require it is foreground).
    # NOTE: In projector mode, the capture itself is output-based, but we still keep
    # a strict foreground requirement here (Tibia foreground), unless the user
    # disables it via FRBOT_FOREGROUND_RETRIES/DELAY or projector adapter env.
    tibia_hwnd = _require_tibia_foreground()
    binding = Win32WindowBinding(hwnd=tibia_hwnd, title_substring=_env_str("FRBOT_WINDOW_TITLE", ""))
    v = binding.verify()
    if not v.ok:
        raise CalibrationHardStop("HARD STOP: window binding not verified (Tibia not present/bound)")

    try:
        binding.assert_bound()
    except Exception:
        raise CalibrationHardStop("HARD STOP: Tibia window binding lost (not foreground or moved)")

    cap: _VerifiedFrameCapture

    if _backend_is_projector(backend):
        # Projector capture verifies contrast and writes baseline frames to diagnostics/.
        # For evidence capture we don't want to force the projector window to be foreground
        # (users often keep Tibia focused); allow strictness to be re-enabled by explicitly
        # setting FRBOT_PROJECTOR_REQUIRE_FOREGROUND.
        old_req = os.environ.get('FRBOT_PROJECTOR_REQUIRE_FOREGROUND')
        if old_req is None:
            os.environ['FRBOT_PROJECTOR_REQUIRE_FOREGROUND'] = '0'
        try:
            from runtime.env import parse_window_hwnd_env
            from runtime.config_loader import load_rois
            from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
            from adapters.capture.meld_projector_real import MeldProjectorMinimapRealCapture

            # Load ROIs so we can provide minimap ROI for the projector adapter verification.
            cfg = RuntimeConfig(
                mode='real',
                tick_hz=1.0,
                config_path=_env_str('FRBOT_CONFIG_PATH', ''),
                enable_cavebot=False,
                window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
                window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
                minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),
            )
            ctx = RuntimeContext(
                config=cfg,
                status=RuntimeStatus(state=RuntimeState.INIT),
                telemetry=RuntimeTelemetry(),
            )
            loaded = load_rois(ctx)
            ctx.rois = dict(loaded.rois)
            minimap_roi = ctx.rois.get(cfg.minimap_roi)
            if minimap_roi is None:
                raise CalibrationHardStop('HARD STOP: minimap ROI missing in config')

            cap = MeldProjectorMinimapRealCapture(minimap_roi=minimap_roi, binding=binding)
            cap_v = cap.verify()
            if not cap_v.ok:
                raise CalibrationHardStop(f"HARD STOP: projector capture verify failed: {cap_v.reason}")
            frame = cap.grab()
        finally:
            if old_req is None:
                try:
                    del os.environ['FRBOT_PROJECTOR_REQUIRE_FOREGROUND']
                except Exception:
                    pass
            else:
                os.environ['FRBOT_PROJECTOR_REQUIRE_FOREGROUND'] = old_req
    else:
        from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture

        cap = MssBoundWindowRealCapture(binding=binding)
        cap_v = cap.verify()
        if not cap_v.ok:
            raise CalibrationHardStop(f"HARD STOP: capture verify failed: {cap_v.reason}")
        frame = cap.grab()

    use_stamp = stamp or _ts()
    fname = f"{gate}_{use_stamp}_{_safe_reason(reason)}_{side}.ppm"
    out_path = out_dir / fname
    ok = dump_frame_ppm(frame, out_path)
    if not ok:
        raise CalibrationHardStop("HARD STOP: failed to write PPM")
    return out_path


def _list_ppms(dir_path: Path) -> set[Path]:
    if not dir_path.exists():
        return set()
    return {p.resolve() for p in dir_path.glob("*.ppm")}


def _move_new_ppms(*, src_dir: Path, before: set[Path], dst_dir: Path) -> list[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)

    after = _list_ppms(src_dir)
    new_files = sorted(p for p in after if p not in before)

    moved: list[Path] = []
    for p in new_files:
        target = dst_dir / p.name
        try:
            shutil.move(str(p), str(target))
        except Exception:
            # If move fails (e.g. across volumes), fall back to copy+unlink.
            shutil.copy2(str(p), str(target))
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        moved.append(target)
    return moved


def _tail(s: str, max_chars: int = 2000) -> str:
    s = s or ""
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _run_gate_via_main(*, repo_root: Path, gate: str, out_gate_dir: Path) -> GateRunResult:
    # Keep Tibia foreground for the gate run itself (gate preflights bind to Tibia HWND).
    _require_tibia_foreground()

    frames_root = repo_root / "diagnostics" / "frames"
    frames_root.mkdir(parents=True, exist_ok=True)

    out_gate_dir.mkdir(parents=True, exist_ok=True)

    # Use a single shared stamp + reason so bootstrap can deterministically pair BEFORE/AFTER.
    stamp = _ts()
    pair_reason = f"calibrate_{gate}"

    # Capture BEFORE frame ourselves (evidence even if runtime preflight aborts).
    before_ppm = _capture_full_frame_ppm(gate=gate, side="before", reason=pair_reason, out_dir=out_gate_dir, stamp=stamp)

    # Track runtime-dumped frames (if any) to relocate under frames_15x/<gate>/.
    existing = _list_ppms(frames_root)

    env = dict(os.environ)
    env["FRBOT_MODE"] = gate
    env[f"FRBOT_{gate.upper()}_BACKEND"] = "real"
    env["FRBOT_DUMP_FRAMES"] = "1"

    # No sleeps / no timing assumptions inside this script.
    # We rely on each gate entrypoint being finite-time by design.
    argv = [sys.executable, str(repo_root / "main.py")]
    p = subprocess.run(argv, cwd=str(repo_root), env=env, text=True, capture_output=True)

    # Always capture AFTER frame ourselves.
    after_ppm = _capture_full_frame_ppm(gate=gate, side="after", reason=pair_reason, out_dir=out_gate_dir, stamp=stamp)

    # Move any runtime-dumped PPMs for this run into the gate folder.
    _move_new_ppms(src_dir=frames_root, before=existing, dst_dir=out_gate_dir)

    out = _tail((p.stdout or "") + (p.stderr or ""))
    return GateRunResult(gate=gate, returncode=int(p.returncode), before_path=before_ppm, after_path=after_ppm, stdout_tail=out)


def _write_rois_15x(*, repo_root: Path, rois: dict[str, dict[str, int]], gate_status: dict[str, dict[str, Any]]) -> Path:
    path = repo_root / "rois_15x.json"
    payload: dict[str, Any] = {
        "rois": rois,
        "gates": gate_status,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _detect_rois_from_frames_very_strict(*, repo_root: Path, frames_gates_root: Path) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, Any]]]:
    # Evidence-only, deterministic, abort > guess.
    # Current implementation is intentionally conservative: it only emits ROIs when
    # they can be derived unambiguously from the available frames.
    # Anything else is marked UNVERIFIED explicitly.

    rois: dict[str, dict[str, int]] = {}
    gates: dict[str, dict[str, Any]] = {}

    frames_15x = frames_gates_root
    for gate in _GATES:
        gate_dir = frames_15x / gate
        if not gate_dir.exists():
            gates[gate] = {"status": "UNVERIFIED", "reason": "no_frames"}
            continue

        # Placeholder: strict mode does not guess ROIs for this gate.
        gates[gate] = {"status": "UNVERIFIED", "reason": "roi_auto_detection_not_supported_without unambiguous evidence"}

    return rois, gates


def _aggregate_frames_flat(*, repo_root: Path, frames_gates_root: Path, version_tag: str) -> Path:
    src = frames_gates_root
    dst = repo_root / 'diagnostics' / f'frames_{version_tag}'
    if dst.exists():
        # Keep the flat directory deterministic.
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for gate in _GATES:
        gate_dir = src / gate
        if not gate_dir.exists():
            continue
        for p in gate_dir.glob("*.ppm"):
            shutil.copy2(str(p), str(dst / p.name))

    # Also include non-gate utility captures (e.g., idle calibration frames).
    idle_dir = src / '_idle'
    if idle_dir.exists():
        for p in idle_dir.glob('*.ppm'):
            shutil.copy2(str(p), str(dst / p.name))
    return dst


def _run_audit_all(*, repo_root: Path, frames_dir: Path, config_path: Path) -> int:
    env = dict(os.environ)
    env["FRBOT_MODE"] = "real"
    env["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)
    env["FRBOT_CONFIG_PATH"] = str(config_path)

    argv = [sys.executable, str(repo_root / "tools" / "audit_all.py")]
    p = subprocess.run(argv, cwd=str(repo_root), env=env)
    return int(p.returncode)


def main() -> int:
    repo_root = _ensure_repo_root_on_syspath()

    ap = argparse.ArgumentParser(description='Calibrate + capture REAL evidence for a specific Tibia client version')
    ap.add_argument('--version', default=os.environ.get('FRBOT_TIBIA_VERSION', '15.x'), help='Client version tag (15.x or 15.y)')
    args = ap.parse_args()
    version_tag = _normalize_version_tag(str(args.version))

    try:
        frames_dir, config_path, _tibia_hwnd = _require_preconditions(repo_root=repo_root)
    except CalibrationHardStop as exc:
        print(str(exc))
        return 2

    # Spec: do not allow manual/ambiguous configs. Only canonical schema is accepted.
    # Also: never overwrite an existing config.
    config_preexisting = config_path.exists()
    if config_preexisting:
        _validate_canonical_rois_config(config_path)

    print(f"FRBOT CALIBRATE - REAL CLIENT (Tibia {version_tag})")
    print("-----------------------------------------")
    print(f"FRBOT_REAL_FRAMES_DIR: {frames_dir}")
    print(f"FRBOT_CONFIG_PATH: {config_path}")

    results: list[GateRunResult] = []
    frames_gates_root = _frames_gates_root(repo_root=repo_root, version_tag=version_tag)

    # Keep evidence deterministic: old PPMs in frames_gates_root can poison the
    # flattened frames directory and cause semantic audit to pick stale pairs.
    if frames_gates_root.exists():
        shutil.rmtree(frames_gates_root)
    frames_gates_root.mkdir(parents=True, exist_ok=True)

    # Capture at least one idle BEFORE/AFTER pair for semantic audit calibration.
    # This is evidence-only and does not perform any in-game actions.
    try:
        idle_dir = frames_gates_root / '_idle'
        stamp = _ts()
        _capture_full_frame_ppm(gate='idle', side='before', reason='idle', out_dir=idle_dir, stamp=stamp)
        _capture_full_frame_ppm(gate='idle', side='after', reason='idle', out_dir=idle_dir, stamp=stamp)
    except CalibrationHardStop as exc:
        print(str(exc))
        print('FINAL VERDICT: FAIL')
        return 2

    for gate in _GATES:
        print("")
        print(f"GATE: {gate}")
        try:
            res = _run_gate_via_main(repo_root=repo_root, gate=gate, out_gate_dir=frames_gates_root / gate)
            results.append(res)
            print(f"- main.py returncode: {res.returncode}")
            print(f"- captured: {res.before_path.name} / {res.after_path.name}")
        except CalibrationHardStop as exc:
            print(str(exc))
            print("FINAL VERDICT: FAIL")
            return 2

    wrote_config = False
    if not config_preexisting:
        # Config generation is evidence-only. If we cannot derive ROIs deterministically,
        # we HARD STOP rather than writing an empty/invalid config.
        rois, _gate_status = _detect_rois_from_frames_very_strict(repo_root=repo_root, frames_gates_root=frames_gates_root)
        if not rois:
            raise _hard_stop(
                'roi_generation_unavailable',
                hint='Provide a canonical ROI config via a deterministic calibration process; auto ROI detection is intentionally disabled in strict mode.',
            )

        payload: dict[str, Any] = {"rois": rois}
        config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        _validate_canonical_rois_config(config_path)
        wrote_config = True

    # Always keep per-gate evidence in diagnostics/frames_15x/<gate>/, but aggregate
    # a flat folder into FRBOT_REAL_FRAMES_DIR for audit_all.
    frames_flat = _aggregate_frames_flat(repo_root=repo_root, frames_gates_root=frames_gates_root, version_tag=version_tag)
    # Mirror flat frames into the requested FRBOT_REAL_FRAMES_DIR.
    # IMPORTANT: frames_flat may already be the requested dir.
    if frames_dir.resolve() != frames_flat.resolve():
        if frames_dir.exists():
            for p in frames_dir.glob("*.ppm"):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        for p in frames_flat.glob("*.ppm"):
            shutil.copy2(str(p), str(frames_dir / p.name))

    print("")
    if wrote_config:
        print(f"Wrote ROI config: {config_path}")
    else:
        print(f"Using ROI config: {config_path}")
    print(f"Frames (flat, internal): {frames_flat}")
    print(f"Frames (audit dir): {frames_dir}")

    audit_rc = _run_audit_all(repo_root=repo_root, frames_dir=frames_dir, config_path=config_path)

    print("")
    print("CALIBRATION COMPLETE")
    print(f"audit_all.py exit code: {audit_rc}")
    # IMPORTANT: this tool's job is to CAPTURE declared evidence. Operational certification
    # is handled separately by audit_all.py and the Phase 1 guard/diff tooling.
    # Therefore we do not fail calibration just because audit_all does not certify.
    print("FINAL VERDICT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
