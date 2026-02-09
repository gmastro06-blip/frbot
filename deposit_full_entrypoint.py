from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import DepotSnapshot, InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame, try_dump_window_frame_pair
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger

from runtime.deposit_basic_preflight import run as deposit_basic_preflight_run
from runtime.deposit_runner import execute_deposit_tick


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if name == 'FRBOT_WINDOW_HWND' and raw is not None and str(raw).strip() != '':
        s = str(raw).strip()
        if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
            return int(default)
        try:
            return int(s, 0)
        except Exception as exc:
            raise PreflightFailed('window_hwnd_invalid') from exc
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _frames_dir() -> Path:
    raw = (_env_str('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(str(raw))
    profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if profile == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _append_trace(*, gate: str, payload: dict) -> None:
    try:
        out_dir = _frames_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'{str(gate).strip().lower()}_trace.jsonl'
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
    except Exception:
        return


def _serialize_inventory(inv: InventorySnapshot | None) -> dict:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_depot(d: DepotSnapshot | None) -> dict:
    if d is None:
        return {}
    return {'item_count': int(d.item_count), 'open': bool(d.open)}


def _try_write_last_result(
    *,
    evidence_dir: Path,
    ok: bool,
    outcome_kind: str,
    before_ppm: str | None,
    after_ppm: str | None,
    inputs_sent: int,
    inventory_before: InventorySnapshot | None,
    inventory_after: InventorySnapshot | None,
    depot_before: DepotSnapshot | None,
    depot_after: DepotSnapshot | None,
) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # Only attempt to auto-fill *missing* pointers when at least one pointer
        # exists for this attempt. If both are missing, scanning can accidentally
        # pick stale files from older runs and mislead auditors.
        if (before_ppm is not None or after_ppm is not None) and (not before_ppm or not after_ppm):
            before_scan = None
            after_scan = None
            try:
                items = sorted(evidence_dir.glob('deposit_full_*_before.ppm'))
                if items:
                    before_scan = str(items[-1].name)
                    after_candidate = str(items[-1].name).replace('_before.ppm', '_after.ppm')
                    if (evidence_dir / after_candidate).exists():
                        after_scan = str(after_candidate)
            except Exception:
                before_scan = None
                after_scan = None

            before_ppm = before_ppm or before_scan
            after_ppm = after_ppm or after_scan

        payload: dict[str, object] = {
            'gate': 'deposit_full',
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'inputs_sent': int(inputs_sent),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'inventory_before': _serialize_inventory(inventory_before),
            'inventory_after': _serialize_inventory(inventory_after),
            'depot_before': _serialize_depot(depot_before),
            'depot_after': _serialize_depot(depot_after),
        }

        (evidence_dir / 'deposit_full_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _write_evidence_manifest(*, evidence_dir: Path, capture: object) -> None:
    try:
        src = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
        payload = {
            'capture_source': ('obs_source' if src == 'obs_source' else ('obs' if src == 'obs' else 'client')),
            'obs_source_name': str(getattr(capture, 'obs_source_name', '') or _env_str('FRBOT_OBS_SOURCE_NAME', '') or ''),
            'obs_projector_title': str(_env_str('FRBOT_OBS_PROJECTOR_TITLE', '') or ''),
            'ts': int(time.time()),
        }
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / 'evidence_manifest.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception:
        return


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_DEPOSIT_FULL_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        deposit_max_attempts=1,
        deposit_max_ticks=1,
        deposit_key=_env_str('FRBOT_DEPOSIT_KEY', 'D'),

        inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
        depot_container_roi=_env_str('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container'),

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_deposit_full_only() -> int:
    """deposit_full feature gate.

    Contract:
    - BEFORE capture -> exactly 1 input -> AFTER capture.
    - Success requires consistent inventory+depot semantic deltas.
    - In prod_full: evidence frames + last_result.json are mandatory.
    """

    ctx: RuntimeContext | None = None

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        capture, input_, binding = deposit_basic_preflight_run(ctx)

        evidence_dir = _frames_dir()
        _write_evidence_manifest(evidence_dir=evidence_dir, capture=capture)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        outcome = execute_deposit_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=0, gate='deposit_full')
        ev = outcome.evidence

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}

        before_ppm = None
        after_ppm = None
        if dump_force or dump_enabled():
            before, after = snapshot('deposit_full')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='deposit_full', before=before, after=after, reason=str(ev.status), out_dir=str(evidence_dir))

        _append_trace(
            gate='deposit_full',
            payload={
                'event': 'tick',
                'gate': 'deposit_full',
                'inputs_sent': int(getattr(ctx.deposit, 'inputs_sent', 0)),
                'attempts_used': int(getattr(ctx.deposit, 'attempts_used', 0)),
                'success': bool(outcome.success),
                'status': str(ev.status),
                'abort_reason': (str(outcome.abort_reason) if outcome.abort_reason is not None else None),
            },
        )

        if outcome.abort_reason is not None:
            raise PreflightFailed(str(outcome.abort_reason))
        if not outcome.success:
            raise PreflightFailed('deposit_unverified_action')

        log_json(logger, event='success', gate='deposit_full', status='SUCCESS')

        _try_write_last_result(
            evidence_dir=evidence_dir,
            ok=True,
            outcome_kind=str(ev.status),
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            inputs_sent=int(getattr(ctx.deposit, 'inputs_sent', 0)),
            inventory_before=ev.inventory_before,
            inventory_after=ev.inventory_after,
            depot_before=ev.depot_before,
            depot_after=ev.depot_after,
        )

        return 0

    except PreflightFailed as exc:
        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}

        before_ppm = None
        after_ppm = None
        evidence_dir = _frames_dir()

        if dump_force or dump_enabled():
            before, after = snapshot('deposit_full')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='deposit_full', before=before, after=after, reason=str(exc), out_dir=str(evidence_dir))
            else:
                # When preflight fails early, last_frames may be empty. In prod_full we still
                # need BEFORE+AFTER evidence. Prefer direct OBS source identity capture.
                src = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
                obs_name = (_env_str('FRBOT_OBS_SOURCE_NAME', '') or '').strip()
                captured = False
                if src == 'obs_source' and obs_name and ctx is not None:
                    try:
                        from adapters.capture.obs_source_real import ObsSourceRealCapture
                        from runtime.config_loader import load_rois

                        loaded = load_rois(ctx)
                        rois = dict(loaded.rois)
                        if loaded.frame_width is not None and loaded.frame_height is not None:
                            cap = ObsSourceRealCapture(
                                obs_source_name=str(obs_name),
                                expected_width=int(loaded.frame_width),
                                expected_height=int(loaded.frame_height),
                                rois=rois,
                                minimap_roi_name=str(getattr(ctx.config, 'minimap_roi', 'minimap') or 'minimap'),
                            )
                            if bool(cap.verify().ok):
                                b = cap.grab()
                                a = cap.grab()
                                before_ppm, after_ppm = dump_pair(gate='deposit_full', before=b, after=a, reason=str(exc), out_dir=str(evidence_dir))
                                captured = True
                    except Exception:
                        captured = False

                if not captured:
                    # Fallback: best-effort window capture via generic preflight.
                    if not try_dump_window_frame_pair(gate='deposit_full', reason=str(exc)):
                        try_dump_window_frame(gate='deposit_full', reason=str(exc))

        _append_trace(
            gate='deposit_full',
            payload={
                'event': 'preflight_failed',
                'gate': 'deposit_full',
                'profile': str(profile),
                'dump_force': bool(dump_force),
                'dump_enabled': bool(dump_enabled()),
                'capture_source_env': str(_env_str('FRBOT_CAPTURE_SOURCE', '')), 
                'obs_source_name_env': str(_env_str('FRBOT_OBS_SOURCE_NAME', '')),
                'outcome_kind': str(exc),
                'before_ppm': before_ppm,
                'after_ppm': after_ppm,
            },
        )

        inv_b = getattr(getattr(ctx, 'deposit', None), 'last_inventory_before', None) if ctx is not None else None
        inv_a = getattr(getattr(ctx, 'deposit', None), 'last_inventory_after', None) if ctx is not None else None
        dep_b = getattr(getattr(ctx, 'deposit', None), 'last_depot_before', None) if ctx is not None else None
        dep_a = getattr(getattr(ctx, 'deposit', None), 'last_depot_after', None) if ctx is not None else None

        _try_write_last_result(
            evidence_dir=evidence_dir,
            ok=False,
            outcome_kind=str(exc),
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            inputs_sent=int(getattr(getattr(ctx, 'deposit', None), 'inputs_sent', 0) if ctx is not None else 0),
            inventory_before=inv_b,
            inventory_after=inv_a,
            depot_before=dep_b,
            depot_after=dep_a,
        )

        write_fatal(str(exc), exc)
        return 1

    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run_deposit_full_only())
