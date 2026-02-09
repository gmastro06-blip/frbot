from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled
from diagnostics.jsonlog import log as log_json
from diagnostics.logger import configure_logger

from runtime.looting_full_preflight import run as looting_full_preflight_run
from runtime.looting_full_runner import execute_looting_full


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


def _load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    return data if isinstance(data, dict) else {}


def _append_trace(*, gate: str, payload: dict) -> None:
    try:
        out_dir = _frames_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'{str(gate).strip().lower()}_trace.jsonl'
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
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


def _serialize_inventory(inv: InventorySnapshot | None) -> dict:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _latest_success_pair(evidence_dir: Path) -> tuple[str | None, str | None, str]:
    # Prefer inventory-delta evidence frames.
    for reason in ('inventory_delta', 'chat_delta', 'chat_delta_inventory_unreadable'):
        items = sorted(evidence_dir.glob(f'looting_full_*_{reason}_before.ppm'))
        # Guard against substring collisions (e.g. looting_no_inventory_delta ending with inventory_delta).
        if reason == 'inventory_delta':
            items = [p for p in items if '_looting_no_inventory_delta_' not in str(p.name)]
        if not items:
            continue
        before = str(items[-1].name)
        after = before.replace('_before.ppm', '_after.ppm')
        if (evidence_dir / after).exists():
            return before, after, str(reason)
    return None, None, 'none'


def _latest_any_pair(evidence_dir: Path) -> tuple[str | None, str | None, str]:
    # Evidence frames we want to reference for audit/diagnosis even on failure.
    reasons = (
        'inventory_delta',
        'chat_delta',
        'chat_delta_inventory_unreadable',
        'looting_no_inventory_delta',
        'quick_loot_not_effective',
        'looting_inventory_unreadable',
        'looting_click_point_missing',
        'looting_input_emit_failed',
    )

    for reason in reasons:
        items = sorted(evidence_dir.glob(f'looting_full_*_{reason}_before.ppm'))
        if reason == 'inventory_delta':
            items = [p for p in items if '_looting_no_inventory_delta_' not in str(p.name)]
        if not items:
            continue
        before = str(items[-1].name)
        after = before.replace('_before.ppm', '_after.ppm')
        if (evidence_dir / after).exists():
            return before, after, str(reason)
        # Some failures only produce a BEFORE frame.
        return before, None, str(reason)

    return None, None, 'none'


def _write_last_result(
    *,
    evidence_dir: Path,
    ok: bool,
    outcome_kind: str,
    actions_sent: int,
    successes: int,
    before_ppm: str | None,
    after_ppm: str | None,
    evidence_reason: str,
    evidence_kind: str | None = None,
    chat_ok: bool | None = None,
    chat_latency_ms: float | None = None,
    chat_max_latency_ms: float | None = None,
) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            'gate': 'looting_full',
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'reason': str(outcome_kind),
            'actions_sent': int(actions_sent),
            'successes': int(successes),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'evidence_reason': str(evidence_reason),
            'evidence_kind': None if evidence_kind is None else str(evidence_kind),
            'chat_ok': None if chat_ok is None else bool(chat_ok),
            'chat_latency_ms': None if chat_latency_ms is None else float(chat_latency_ms),
            'chat_max_latency_ms': None if chat_max_latency_ms is None else float(chat_max_latency_ms),
        }
        (evidence_dir / 'looting_full_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_LOOTING_FULL_BACKEND', 'real') or 'real').strip().lower()

    # Accept both legacy and Tibia-specific env names.
    quick_loot_key = _env_str('FRBOT_TIBIA_QUICK_LOOT_KEY', _env_str('FRBOT_QUICK_LOOT_KEY', 'R'))

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
        quick_loot_key=str(quick_loot_key),
        looting_max_attempts_per_corpse=1,
        looting_max_ticks=1,
        looting_require_inventory_delta=True,
        looting_mode='premium',

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_looting_full_only() -> int:
    """looting_full feature gate.

    Contract:
    - Each attempt: BEFORE capture -> exactly 1 input -> AFTER capture.
    - Loop is bounded.
    - PASS only if at least one semantic success is observed.
    - In PROD-EMERGENCY: evidence frames are mandatory.
    """

    ctx: RuntimeContext | None = None

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        capture, input_, binding = looting_full_preflight_run(ctx)

        evidence_dir = _frames_dir()
        _write_evidence_manifest(evidence_dir=evidence_dir, capture=capture)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        max_actions = _env_int('FRBOT_LOOTING_FULL_MAX_ACTIONS', 12)
        stop_no_delta = _env_int('FRBOT_LOOTING_FULL_STOP_NO_DELTA', 2)

        outcome = execute_looting_full(
            ctx,
            capture=capture,
            input_=input_,
            binding=binding,
            max_actions=int(max_actions),
            stop_no_delta=int(stop_no_delta),
            gate='looting_full',
        )

        for att in outcome.attempts:
            _append_trace(gate='looting_full', payload={'event': 'attempt', 'gate': 'looting_full', **dict(att)})

        # Prefer explicit evidence pointers from the runner (no globbing on PASS).
        before_ppm = str(outcome.before_ppm) if getattr(outcome, 'before_ppm', None) else None
        after_ppm = str(outcome.after_ppm) if getattr(outcome, 'after_ppm', None) else None
        evidence_kind = str(outcome.evidence_kind) if getattr(outcome, 'evidence_kind', None) else None
        evidence_reason = str(evidence_kind or 'none')

        chat_ok: bool | None = None
        chat_latency_ms: float | None = None
        chat_max_latency_ms: float | None = None
        try:
            # If looting_basic wrote a rich meta for the gate, propagate its chat fields.
            meta_p = evidence_dir / 'looting_full_last_result.json'
            if meta_p.exists():
                meta = _load_json(meta_p)
                if isinstance(meta, dict):
                    if 'chat_ok' in meta:
                        chat_ok = bool(meta.get('chat_ok'))
                    v = meta.get('chat_latency_ms')
                    if isinstance(v, (int, float, str)):
                        chat_latency_ms = float(v)
                    v2 = meta.get('chat_max_latency_ms')
                    if isinstance(v2, (int, float, str)):
                        chat_max_latency_ms = float(v2)
        except Exception:
            pass

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}
        if dump_force and (not before_ppm or not after_ppm):
            raise PreflightFailed('looting_full_missing_evidence_frames')

        _write_last_result(
            evidence_dir=evidence_dir,
            ok=True,
            outcome_kind=str(outcome.stop_reason),
            actions_sent=int(outcome.actions_sent),
            successes=int(outcome.successes),
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            evidence_reason=str(evidence_reason),
            evidence_kind=evidence_kind,
            chat_ok=chat_ok,
            chat_latency_ms=chat_latency_ms,
            chat_max_latency_ms=chat_max_latency_ms,
        )

        log_json(logger, event='success', gate='looting_full', status='SUCCESS', successes=int(outcome.successes))
        return 0

    except PreflightFailed as exc:
        # Best-effort: ensure we leave a meta pointer for audit even on failure,
        # and try to dump at least one BEFORE frame when preflight aborts early.
        evidence_dir = _frames_dir()

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_emergency', 'prod_full'}

        if dump_force or dump_enabled():
            try:
                from diagnostics.emergency_capture import try_dump_window_frame

                try_dump_window_frame(gate='looting_full', reason=str(exc))
            except Exception:
                pass

        before_ppm, after_ppm, evidence_reason = _latest_any_pair(evidence_dir)

        # If we don't have a success pair, point to the most recent preflight-abort BEFORE frame.
        try:
            if not before_ppm:
                dumps = sorted(evidence_dir.glob('looting_full_*_preflight_abort_*_before.ppm'))
                if dumps:
                    before_ppm = str(dumps[-1].name)
            if evidence_reason == 'none' and before_ppm and after_ppm is None:
                evidence_reason = 'preflight_abort'
        except Exception:
            pass

        try:
            _write_last_result(
                evidence_dir=evidence_dir,
                ok=False,
                outcome_kind=str(exc),
                actions_sent=0 if ctx is None else int(getattr(getattr(ctx, 'looting', object()), 'attempts_used', 0) or 0),
                successes=0 if ctx is None else int(getattr(getattr(ctx, 'looting', object()), 'items_looted', 0) or 0),
                before_ppm=before_ppm,
                after_ppm=after_ppm,
                evidence_reason=str(evidence_reason),
            )
        except Exception:
            pass

        # If dumping is enabled, evidence frames may already be persisted by the runner.
        if dump_enabled():
            _append_trace(gate='looting_full', payload={'event': 'abort', 'gate': 'looting_full', 'reason': str(exc)})

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
    raise SystemExit(run_looting_full_only())
