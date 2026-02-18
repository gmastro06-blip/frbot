from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import DepotSnapshot, InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger
from runtime.env_bootstrap import load_repo_env

from runtime.deposit_basic_preflight import run as deposit_basic_preflight_run
from runtime.deposit_runner import execute_deposit_tick


load_repo_env()


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
    return Path('diagnostics') / 'frames'


def _append_trace(*, gate: str, payload: dict[str, Any]) -> None:
    try:
        out_dir = _frames_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'{str(gate).strip().lower()}_trace.jsonl'
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
    except Exception:
        return


def _serialize_inventory(inv: InventorySnapshot | None) -> dict[str, object]:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_depot(d: DepotSnapshot | None) -> dict[str, object]:
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
    event_correlation: dict[str, Any] | None = None,
) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)

        if not before_ppm or not after_ppm:
            before_scan = None
            after_scan = None
            try:
                items = sorted(evidence_dir.glob('deposit_basic_*_before.ppm'))
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
            'gate': 'deposit_basic',
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'inputs_sent': int(inputs_sent),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'inventory_before': _serialize_inventory(inventory_before),
            'inventory_after': _serialize_inventory(inventory_after),
            'depot_before': _serialize_depot(depot_before),
            'depot_after': _serialize_depot(depot_after),
            'event_correlation': dict(event_correlation or {}),
        }

        (evidence_dir / 'deposit_basic_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_DEPOSIT_BASIC_BACKEND', 'real') or 'real').strip().lower()

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


def run_deposit_basic_only() -> int:
    """deposit_basic feature gate.

    Contract:
    - BEFORE capture -> exactly 1 input -> AFTER capture.
    - Success requires consistent inventory+depot semantic deltas.
    - In PROD-EMERGENCY: binary inventory encoding only (no visual/OCR fallback).
    """

    ctx: RuntimeContext | None = None

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        capture, input_, binding = deposit_basic_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        outcome = execute_deposit_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=0, gate='deposit_basic')
        ev = outcome.evidence

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile == 'prod_emergency'

        before_ppm = None
        after_ppm = None
        if dump_force or dump_enabled():
            before, after = snapshot('deposit_basic')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='deposit_basic', before=before, after=after, reason=str(ev.status))

        _append_trace(
            gate='deposit_basic',
            payload={
                'event': 'tick',
                'gate': 'deposit_basic',
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

        log_json(logger, event='success', gate='deposit_basic', status='SUCCESS')

        evidence_dir = _frames_dir()
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
            event_correlation=dict(getattr(getattr(ctx, 'telemetry', object()), 'last_event_correlation', {}) or {}),
        )

        return 0

    except PreflightFailed as exc:
        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile == 'prod_emergency'

        before_ppm = None
        after_ppm = None
        if dump_force or dump_enabled():
            before, after = snapshot('deposit_basic')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='deposit_basic', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='deposit_basic', reason=str(exc))

        evidence_dir = _frames_dir()
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
            event_correlation=(dict(getattr(getattr(ctx, 'telemetry', object()), 'last_event_correlation', {}) or {}) if ctx is not None else {}),
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
    raise SystemExit(run_deposit_basic_only())
