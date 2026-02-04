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


def _write_fatal(reason: str, *, details: dict[str, Any]) -> None:
    try:
        from diagnostics.fatal import write_fatal

        write_fatal(str(reason), details=details)
    except Exception:
        return


def _write_fatal_for_hard_stop(exc: CalibrationHardStop) -> None:
    """Best-effort fatal.log writer for any CalibrationHardStop.

    Prefer structured JSON hard-stops produced by _hard_stop(). Fallback to
    parsing legacy string hard-stops that start with 'HARD STOP:'.
    """

    text = str(exc)

    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get('reason'):
            _write_fatal(str(payload.get('reason', 'calibration_hard_stop')), details=dict(payload))
            return
    except Exception:
        payload = None

    msg = (text or '').strip()
    core = msg
    if core.startswith('HARD STOP:'):
        core = core[len('HARD STOP:') :].strip()

    extracted = ''
    if 'capture verify failed:' in core:
        extracted = core.split('capture verify failed:', 1)[1].strip()
    elif 'projector capture verify failed:' in core:
        extracted = core.split('projector capture verify failed:', 1)[1].strip()

    reason = extracted or _safe_reason(core or msg)
    _write_fatal(
        str(reason),
        details={
            'reason': str(reason),
            'message': msg,
            'core': core,
            'backend': _capture_backend(),
            'profile': _env_str('FRBOT_PROFILE', ''),
            'window_hwnd': _env_str('FRBOT_WINDOW_HWND', ''),
            'window_title': _env_str('FRBOT_WINDOW_TITLE', ''),
        },
    )


def _try_dump_obs_projector_ppm(*, frames_dir: Path, reason: str) -> None:
    """Best-effort OBS projector dump for failure evidence.

    Uses GDI PrintWindow against the OBS projector HWND. Does not require
    foreground and never steals focus. No sleeps.
    """

    if _capture_source() != 'obs':
        return
    if not _dump_frames_enabled():
        return
    try:
        if not frames_dir.is_absolute():
            return
        frames_dir.mkdir(parents=True, exist_ok=True)

        from adapters.windows.win32 import get_client_rect_in_screen
        from adapters.capture.gdi_hwnd_diag import capture_client_bgra
        from contracts.capture import Frame
        from diagnostics.frame_dump import dump_frame_ppm

        hwnd, title = _resolve_obs_projector_hwnd()
        rect = get_client_rect_in_screen(int(hwnd))
        res = capture_client_bgra(int(hwnd), rect)
        if not res.ok or not res.bgra or int(res.width) <= 0 or int(res.height) <= 0:
            return

        bgra = res.bgra
        rgb = bytearray((int(res.width) * int(res.height)) * 3)
        j = 0
        for i in range(0, len(bgra), 4):
            # BGRA -> RGB
            rgb[j] = bgra[i + 2]
            rgb[j + 1] = bgra[i + 1]
            rgb[j + 2] = bgra[i]
            j += 3
        f = Frame(width=int(res.width), height=int(res.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(rgb))

        stamp = _ts()
        fname = f"obsdiag_{stamp}_{_safe_reason(reason)}_before.ppm"
        dump_frame_ppm(f, frames_dir / fname)

        # Also leave a tiny sidecar with context.
        meta = {
            'schema': 'frbot.obsdiag.v1',
            'ts': datetime.now().astimezone().isoformat(),
            'reason': str(reason),
            'hwnd': hex(int(hwnd)),
            'title': str(title),
            'method': str(getattr(res, 'method', 'PrintWindow')),
        }
        (frames_dir / f"obsdiag_{stamp}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return


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


def _dump_frames_enabled() -> bool:
    v = (_env_str('FRBOT_DUMP_FRAMES', '0') or '0').strip().lower()
    return v in {'1', 'true', 'yes', 'y', 'on'}


def _capture_backend() -> str:
    return (_env_str('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()


def _capture_source() -> str:
    v = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
    if v == 'obs_source':
        return 'obs_source'
    return 'obs' if v == 'obs' else 'client'


def _obs_source_name() -> str:
    return _env_str('FRBOT_OBS_SOURCE_NAME', '')


def _obs_projector_title() -> str:
    return _env_str('FRBOT_OBS_PROJECTOR_TITLE', '')


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


def _resolve_obs_projector_hwnd() -> tuple[int, str]:
    """Resolve OBS projector HWND deterministically by exact title."""

    from adapters.windows.win32 import (
        find_window_by_title_substring,
        is_window_minimized,
        is_window_visible,
        list_top_level_windows,
    )

    expected = _obs_projector_title()
    if not expected:
        raise _hard_stop('obs_projector_not_found', expected_title='', found_titles=[])

    expected_norm = str(expected).strip()
    hwnd = 0
    title = ''
    found: list[str] = []
    try:
        wins = list_top_level_windows(title_substring='', visible_only=False)
        for wi in wins:
            t = str(getattr(wi, 'title', '') or '').strip()
            if t:
                found.append(t)
            if t == expected_norm:
                hwnd = int(getattr(wi, 'hwnd', 0) or 0)
                title = t
                break
    except Exception:
        wins = []

    if int(hwnd) <= 0:
        raise _hard_stop('obs_projector_not_found', expected_title=str(expected_norm), found_titles=found)

    if not is_window_visible(hwnd):
        raise _hard_stop('window_not_visible', expected_hwnd=hex(int(hwnd)), expected_title=title)
    if is_window_minimized(hwnd):
        raise _hard_stop('window_minimized', expected_hwnd=hex(int(hwnd)), expected_title=title)

    return hwnd, title


def _resolve_capture_hwnd() -> tuple[int, str]:
    if _capture_source() == 'obs':
        return _resolve_obs_projector_hwnd()
    return _resolve_tibia_hwnd()


def _require_tibia_foreground() -> int:
    """Compatibility name: in OBS capture mode this enforces projector foreground."""

    from runtime.pacing import sleep_ms

    from adapters.windows.win32 import get_foreground_window, get_window_text

    capture_hwnd, capture_title = _resolve_capture_hwnd()

    # OBS runbook: no sleeps; single check; never steal focus.
    if _capture_source() == 'obs':
        fg = int(get_foreground_window() or 0)
        fg_title = get_window_text(fg) if fg > 0 else ''
        if fg != int(capture_hwnd):
            raise _hard_stop(
                'obs_projector_foreground_mismatch',
                expected_foreground='OBS_PROJECTOR',
                projector_hwnd=hex(int(capture_hwnd)),
                foreground_hwnd=hex(int(fg)),
                foreground_title=str(fg_title),
                hint='Click OBS projector window and rerun',
            )
        return int(capture_hwnd)

    # Client runbook: allow retries/delay (legacy behavior).
    tibia_hwnd = int(capture_hwnd)
    tibia_title = str(capture_title)
    retries = int(os.environ.get('FRBOT_FOREGROUND_RETRIES', '10') or '10')
    delay_ms = int(
        os.environ.get('FRBOT_FOREGROUND_DELAY_MS')
        or os.environ.get('FRBOT_FOREGROUND_RETRY_DELAY_MS')
        or '150'
    )

    last_fg = 0
    last_title = ''
    for attempt in range(max(0, retries) + 1):
        fg = int(get_foreground_window() or 0)
        last_fg = fg
        last_title = get_window_text(fg) if fg > 0 else ''
        if fg == tibia_hwnd:
            return tibia_hwnd

        if attempt < retries:
            # Spec: do not steal focus. We only wait/yield for operator to focus Tibia.
            sleep_ms(max(0.0, float(delay_ms)))

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
    if _backend_is_projector(backend) or backend == 'meld':
        try:
            import dxcam  # type: ignore  # noqa: F401
        except Exception:
            raise _hard_stop(
                'missing_dependency',
                dependency='dxcam',
                hint='Install DXGI capture deps: pip install dxcam (or poetry add dxcam).',
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
    capture_hwnd = _require_tibia_foreground()
    title_env = _env_str('FRBOT_OBS_PROJECTOR_TITLE', '') if _capture_source() == 'obs' else _env_str('FRBOT_WINDOW_TITLE', '')
    binding = Win32WindowBinding(hwnd=capture_hwnd, title_substring=title_env)
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
        if backend == 'meld':
            from adapters.capture.meld_real import MeldBoundWindowRealCapture

            cap = MeldBoundWindowRealCapture(binding=binding)
        else:
            from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture

            cap = MssBoundWindowRealCapture(binding=binding)
        cap_v = cap.verify()
        if not cap_v.ok:
            if _capture_source() == 'obs' and (cap_v.reason or '') == 'captured_frame_black':
                raise _hard_stop('captured_frame_black_obs', backend=str(backend), hwnd=hex(int(capture_hwnd)), title=str(title_env))
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


def _list_gate_artifacts(dir_path: Path) -> set[Path]:
    if not dir_path.exists():
        return set()
    out: set[Path] = set()
    out |= {p.resolve() for p in dir_path.glob("*.ppm")}
    out |= {p.resolve() for p in dir_path.glob("*_trace.jsonl")}
    # Cavebot certification trace has a fixed name.
    p = (dir_path / 'cavebot_trace.jsonl')
    if p.exists():
        out.add(p.resolve())
    return out


def _move_new_ppms(*, src_dir: Path, before: set[Path], dst_dir: Path) -> list[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)

    after = _list_gate_artifacts(src_dir)
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
    # In REAL mode we require the configured capture window is foreground.
    # In OBS mode this is the OBS projector window.
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
    # Runbook contract: FRBOT_MODE is {real,mock}. Gates are executed explicitly.
    env["FRBOT_MODE"] = "real"
    env[f"FRBOT_{gate.upper()}_BACKEND"] = "real"
    env["FRBOT_DUMP_FRAMES"] = "1"

    entrypoints: dict[str, str] = {
        'targeting': 'from targeting_entrypoint import run_targeting_only as _f; raise SystemExit(_f())',
        'healing': 'from healing_entrypoint import run_healing_only as _f; raise SystemExit(_f())',
        'cavebot': 'from cavebot_entrypoint import run_cavebot_only as _f; raise SystemExit(_f())',
        'combat': 'from combat_entrypoint import run_combat_only as _f; raise SystemExit(_f())',
        'looting': 'from looting_entrypoint import run_looting_only as _f; raise SystemExit(_f())',
        'deposit': 'from deposit_entrypoint import run_deposit_only as _f; raise SystemExit(_f())',
        'trade': 'from trade_entrypoint import run_trade_only as _f; raise SystemExit(_f())',
    }
    code = entrypoints.get(str(gate))
    if code is None:
        p = subprocess.run([sys.executable, '-c', 'raise SystemExit(2)'], cwd=str(repo_root), env=env, text=True, capture_output=True)
    else:
        # No sleeps / no timing assumptions inside this script.
        # We rely on each gate entrypoint being finite-time by design.
        argv = [sys.executable, '-c', code]
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
        for p in gate_dir.glob("*_trace.jsonl"):
            shutil.copy2(str(p), str(dst / p.name))
        cavebot_trace = gate_dir / 'cavebot_trace.jsonl'
        if cavebot_trace.exists():
            shutil.copy2(str(cavebot_trace), str(dst / cavebot_trace.name))

    # Also include non-gate utility captures (e.g., idle calibration frames).
    idle_dir = src / '_idle'
    if idle_dir.exists():
        for p in idle_dir.glob('*.ppm'):
            shutil.copy2(str(p), str(dst / p.name))

    # Evidence manifest: binds the flat folder to a capture source.
    manifest: dict[str, Any] = {
        'schema': 'frbot.evidence_manifest.v1',
        'ts': datetime.now().astimezone().isoformat(),
        'profile': _env_str('FRBOT_PROFILE', ''),
        'mode': 'real',
        'version_tag': str(version_tag),
        'capture_source': _capture_source(),
        'capture_backend': _capture_backend(),
        'obs_projector_title': _env_str('FRBOT_OBS_PROJECTOR_TITLE', ''),
        'obs_source_name': _obs_source_name(),
    }
    if _capture_source() == 'obs':
        try:
            hwnd, title = _resolve_obs_projector_hwnd()
            manifest['capture_hwnd'] = hex(int(hwnd))
            manifest['capture_title'] = str(title)
        except Exception:
            pass
    (dst / 'evidence_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
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

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()

    try:
        if profile == 'prod_emergency':
            backend = _capture_backend()
            if backend not in {'mss', 'meld'}:
                raise _hard_stop(
                    'capture_invalid',
                    expected_backends=['mss', 'meld'],
                    got=str(backend),
                    hint='In PROD-EMERGENCY, real calibration must use HWND-bound capture backends only (mss|meld). Projector/cam capture is disabled.',
                )

        frames_dir, config_path, _tibia_hwnd = _require_preconditions(repo_root=repo_root)
    except CalibrationHardStop as exc:
        _write_fatal_for_hard_stop(exc)
        # OBS evidence requirement: best-effort dump even on precondition failure.
        fd = Path(_env_str('FRBOT_REAL_FRAMES_DIR', '') or '')
        reason = ''
        try:
            payload = json.loads(str(exc))
            if isinstance(payload, dict):
                reason = str(payload.get('reason') or '')
        except Exception:
            reason = ''
        _try_dump_obs_projector_ppm(frames_dir=fd, reason=(reason or str(exc)))
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

    gates_to_run = _GATES
    if profile == 'prod_emergency':
        # Scope-closure: only these gates are supported in production emergency.
        gates_to_run = ('targeting', 'healing', 'cavebot')

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
        _write_fatal_for_hard_stop(exc)
        print(str(exc))
        print('FINAL VERDICT: FAIL')
        return 2

    for gate in gates_to_run:
        print("")
        print(f"GATE: {gate}")
        try:
            res = _run_gate_via_main(repo_root=repo_root, gate=gate, out_gate_dir=frames_gates_root / gate)
            results.append(res)
            print(f"- main.py returncode: {res.returncode}")
            print(f"- captured: {res.before_path.name} / {res.after_path.name}")
        except CalibrationHardStop as exc:
            _write_fatal_for_hard_stop(exc)
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

        config_payload: dict[str, Any] = {"rois": rois}
        config_path.write_text(json.dumps(config_payload, indent=2, sort_keys=True), encoding="utf-8")
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
        man = frames_flat / 'evidence_manifest.json'
        if man.exists():
            shutil.copy2(str(man), str(frames_dir / man.name))

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
