from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Literal, cast

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import InventorySnapshot, NpcIdentity, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame, try_dump_window_frame_pair
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger
from diagnostics.schema import base_context_fields

from runtime.trade_basic_preflight import run as trade_basic_preflight_run
from runtime.trade_runner import execute_trade_tick


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


TradeAction = Literal['buy', 'sell', 'deposit']


def _env_trade_action(name: str, default: TradeAction) -> TradeAction:
    raw = _env_str(name, default).strip().lower()
    if raw in ('buy', 'sell', 'deposit'):
        return cast(TradeAction, raw)
    return default


def _serialize_inventory(inv: InventorySnapshot | None) -> dict:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_npc(npc: NpcIdentity | None) -> dict:
    if npc is None:
        return {}
    return {'npc_id': int(npc.npc_id), 'open': bool(npc.open)}


def _try_write_last_result(
    *,
    evidence_dir: Path,
    ok: bool,
    outcome_kind: str,
    before_ppm: str | None,
    after_ppm: str | None,
    inputs_sent: int,
    intent_type: str,
    npc: NpcIdentity | None,
    inventory_before: InventorySnapshot | None,
    inventory_after: InventorySnapshot | None,
    event_correlation: dict | None = None,
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
                items = sorted(evidence_dir.glob('trade_full_*_before.ppm'))
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
            'gate': 'trade_full',
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'intent_type': str(intent_type),
            'inputs_sent': int(inputs_sent),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'npc': _serialize_npc(npc),
            'inventory_before': _serialize_inventory(inventory_before),
            'inventory_after': _serialize_inventory(inventory_after),
            'event_correlation': dict(event_correlation or {}),
        }

        (evidence_dir / 'trade_full_last_result.json').write_text(
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
            **base_context_fields(),
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
    backend = (_env_str('FRBOT_TRADE_FULL_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        trade_max_attempts=1,
        trade_max_ticks=1,
        trade_action=_env_trade_action('FRBOT_TRADE_ACTION', 'buy'),
        trade_expected_npc_id=_env_int('FRBOT_TRADE_EXPECTED_NPC_ID', 1),

        trade_inventory_roi=_env_str('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory'),
        trade_npc_roi=_env_str('FRBOT_TRADE_NPC_ROI', 'trade_npc'),
        trade_action_roi=_env_str('FRBOT_TRADE_ACTION_ROI', 'trade_action'),

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_trade_full_only() -> int:
    """trade_full feature gate.

    Contract:
    - BEFORE capture -> exactly 1 input -> AFTER capture.
    - Success requires semantic economic delta matching intent.
    - In prod_full: evidence frames + last_result.json are mandatory.
    """

    ctx: RuntimeContext | None = None

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        capture, input_, binding = trade_basic_preflight_run(ctx)

        evidence_dir = _frames_dir()
        _write_evidence_manifest(evidence_dir=evidence_dir, capture=capture)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        outcome = execute_trade_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=0, gate='trade_full')
        ev = outcome.evidence

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}

        before_ppm = None
        after_ppm = None
        if dump_force or dump_enabled():
            before, after = snapshot('trade_full')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='trade_full', before=before, after=after, reason=str(ev.status), out_dir=str(evidence_dir))

        _append_trace(
            gate='trade_full',
            payload={
                'event': 'tick',
                'gate': 'trade_full',
                'intent_type': str(cfg.trade_action),
                'inputs_sent': int(getattr(ctx.trade, 'inputs_sent', 0)),
                'success': bool(outcome.success),
                'status': str(ev.status),
                'abort_reason': (str(outcome.abort_reason) if outcome.abort_reason is not None else None),
            },
        )

        if outcome.abort_reason is not None:
            raise PreflightFailed(str(outcome.abort_reason))
        if not outcome.success:
            raise PreflightFailed('trade_unverified_action')

        log_json(logger, event='success', gate='trade_full', status='SUCCESS', intent_type=str(cfg.trade_action))

        _try_write_last_result(
            evidence_dir=evidence_dir,
            ok=True,
            outcome_kind=str(ev.status),
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            inputs_sent=int(getattr(ctx.trade, 'inputs_sent', 0)),
            intent_type=str(cfg.trade_action),
            npc=ev.npc,
            inventory_before=ev.inventory_before,
            inventory_after=ev.inventory_after,
            event_correlation=(dict(getattr(getattr(ctx, 'telemetry', object()), 'last_event_correlation', {}) or {}) if ctx is not None else {}),
        )

        return 0

    except PreflightFailed as exc:
        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}

        before_ppm = None
        after_ppm = None
        evidence_dir = _frames_dir()

        if dump_force or dump_enabled():
            before, after = snapshot('trade_full')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='trade_full', before=before, after=after, reason=str(exc), out_dir=str(evidence_dir))
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
                                before_ppm, after_ppm = dump_pair(gate='trade_full', before=b, after=a, reason=str(exc), out_dir=str(evidence_dir))
                                captured = True
                    except Exception:
                        captured = False

                if not captured:
                    if not try_dump_window_frame_pair(gate='trade_full', reason=str(exc)):
                        try_dump_window_frame(gate='trade_full', reason=str(exc))

        npc = getattr(getattr(ctx, 'trade', None), 'last_npc', None) if ctx is not None else None
        inv_b = getattr(getattr(ctx, 'trade', None), 'last_inventory_before', None) if ctx is not None else None
        inv_a = getattr(getattr(ctx, 'trade', None), 'last_inventory_after', None) if ctx is not None else None

        _try_write_last_result(
            evidence_dir=evidence_dir,
            ok=False,
            outcome_kind=str(exc),
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            inputs_sent=int(getattr(getattr(ctx, 'trade', None), 'inputs_sent', 0) if ctx is not None else 0),
            intent_type=str(getattr(getattr(ctx, 'config', None), 'trade_action', '') if ctx is not None else ''),
            npc=npc,
            inventory_before=inv_b,
            inventory_after=inv_a,
        )

        allow_no_delta = ((_env_str('FRBOT_TRADE_FULL_ALLOW_NO_DELTA_PASS', '') or '').strip().lower() in {'1', 'true', 'yes', 'on'})
        if (
            str(profile) == 'prod_full'
            and bool(allow_no_delta)
            and str(exc) == 'trade_no_trade_delta'
            and bool(before_ppm)
            and bool(after_ppm)
        ):
            _try_write_last_result(
                evidence_dir=evidence_dir,
                ok=True,
                outcome_kind='trade_no_delta_tolerated',
                before_ppm=before_ppm,
                after_ppm=after_ppm,
                inputs_sent=int(getattr(getattr(ctx, 'trade', None), 'inputs_sent', 0) if ctx is not None else 0),
                intent_type=str(getattr(getattr(ctx, 'config', None), 'trade_action', '') if ctx is not None else ''),
                npc=npc,
                inventory_before=inv_b,
                inventory_after=inv_a,
            )
            log_json(configure_logger(), event='warning', gate='trade_full', status='TOLERATED', reason='trade_no_trade_delta')
            return 0

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
    raise SystemExit(run_trade_full_only())
