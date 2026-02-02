from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


_ALL_GATES: tuple[str, ...] = ('targeting', 'healing', 'combat', 'cavebot', 'looting', 'deposit', 'trade')


def _profile() -> str:
    return _env_str('FRBOT_PROFILE', '').lower()


def _gates_for_profile() -> tuple[tuple[str, ...], list[str], list[str]]:
    """Return (required_gates, enabled_features, disabled_features)."""

    if _profile() == 'prod_emergency':
        enabled = ['capture_real', 'targeting_basic', 'cavebot_basic', 'healing_basic']
        disabled = ['combat', 'looting', 'deposit', 'trade']
        # Only require evidence for the allowed gates.
        return ('targeting', 'cavebot', 'healing'), enabled, disabled

    return _ALL_GATES, [], []


@dataclass(frozen=True, slots=True)
class _Preconditions:
    mode: str
    frames_dir: Path
    config_path: Path


def _ensure_repo_root_on_syspath() -> None:
    # Allows running via `python tools/audit_all.py` from any CWD.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _check_preconditions() -> tuple[_Preconditions | None, list[str]]:
    reasons: list[str] = []
    canonical_reason: str | None = None

    mode = _env_str('FRBOT_MODE', '').lower()
    if mode not in {'real', 'mock'}:
        reasons.append('FRBOT_MODE invalid (expected real|mock)')
        return None, reasons

    frames_raw = _env_str('FRBOT_REAL_FRAMES_DIR', '')
    config_raw = _env_str('FRBOT_CONFIG_PATH', '')
    frames_dir = Path(frames_raw) if frames_raw else Path('.')
    config_path = Path(config_raw) if config_raw else Path('.')

    if mode == 'real':
        if not frames_raw:
            canonical_reason = canonical_reason or 'real_evidence_missing'
            reasons.append('FRBOT_REAL_FRAMES_DIR missing')
        elif not frames_dir.exists():
            canonical_reason = canonical_reason or 'real_evidence_missing'
            reasons.append(f'FRBOT_REAL_FRAMES_DIR does not exist: {frames_dir}')
        else:
            # Hard precondition: must have at least one PPM.
            if not any(frames_dir.glob('*.ppm')):
                canonical_reason = canonical_reason or 'real_evidence_missing'
                reasons.append(f'No .ppm evidence found in: {frames_dir}')

        if not config_raw:
            canonical_reason = canonical_reason or 'config_invalid_schema'
            reasons.append('FRBOT_CONFIG_PATH missing')
        elif not config_path.exists():
            canonical_reason = canonical_reason or 'config_invalid_schema'
            reasons.append(f'FRBOT_CONFIG_PATH does not exist: {config_path}')

        if reasons:
            if canonical_reason is not None:
                reasons.insert(0, f'reason: {canonical_reason}')
            return None, reasons

    # In mock mode, frames/config are not required.
    if mode == 'mock':
        if not frames_raw:
            frames_dir = Path('.')
        if not config_raw:
            config_path = Path('.')

    return _Preconditions(mode=mode, frames_dir=frames_dir, config_path=config_path), []


def _run_pytest_subset() -> tuple[bool, str]:
    # Tests are reinforcement only. Keep subset explicit and deterministic.
    tests = [
        'tests/test_runtime_log_only_after_preflight.py',
        'tests/test_abort_on_unverified_evidence.py',
        'tests/test_one_intent_one_input.py',
        'tests/test_no_retries_allowed_trade.py',
        'tests/test_window_binding_lost_abort.py',
        'tests/test_real_mode_never_runs_unbound.py',
        'tests/test_hwnd_bound_capture.py',
        'tests/test_preflight_abort.py',
        'tests/test_minimap_missing_abort.py',
        'tests/test_minimap_delta_without_position_change_aborts.py',
        'tests/test_loop_with_visual_noise_aborts.py',
    ]

    argv = [sys.executable, '-m', 'pytest', '-q', *tests]
    repo_root = Path(__file__).resolve().parents[1]
    p = subprocess.run(argv, text=True, capture_output=True, cwd=str(repo_root))
    out = (p.stdout or '') + (p.stderr or '')
    return (p.returncode == 0), out.strip()


def _print_header(*, mode: str, frames_dir: Path, config_path: Path) -> None:
    print('FRBOT AUDIT - FINAL VERDICT')
    print('--------------------------')
    print(f'Mode: {mode}')
    if mode == 'real':
        print(f'Evidence dir: {frames_dir}')
        print(f'Config: {config_path}')


def main() -> int:
    _ensure_repo_root_on_syspath()

    from diagnostics.evidence_inventory import collect_evidence_inventory
    from diagnostics.real_mode_audit import run_real_mode_audit

    pre, pre_reasons = _check_preconditions()
    if pre is None:
        # HARD FAIL preconditions
        print('FRBOT AUDIT - FINAL VERDICT')
        print('--------------------------')
        mode = _env_str('FRBOT_MODE', '') or 'UNKNOWN'
        print(f"Mode: {mode}")
        print('')
        print('Preconditions: FAIL')
        for r in pre_reasons:
            print(f'- {r}')
        print('')
        if str(mode).strip().lower() == 'real':
            print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        else:
            print('FINAL DECISION: NOT OPERATIONAL')
        print('Exit code: 2')
        return 2

    required_gates, enabled_features, disabled_features = _gates_for_profile()
    _print_header(mode=pre.mode, frames_dir=pre.frames_dir, config_path=pre.config_path)

    if enabled_features or disabled_features:
        print('Profile:')
        print(f"  FRBOT_PROFILE={_env_str('FRBOT_PROFILE','')}")
        if enabled_features:
            print(f"  ENABLED: {', '.join(enabled_features)}")
        if disabled_features:
            print(f"  DISABLED: {', '.join(disabled_features)}")

    if pre.mode == 'mock':
        ok, out = _run_pytest_subset()
        print('')
        print('Inventory: SKIPPED (mode=mock)')
        print('Semantic audit: SKIPPED (mode=mock)')
        print('')
        if not ok:
            print('Tests: FAIL')
            if out:
                print(out)
            print('')
            print('FINAL DECISION: NOT OPERATIONAL')
            print('Exit code: 5')
            return 5

        print('Tests: PASS')
        print('')
        print('FINAL DECISION: OPERATIONAL')
        print('Exit code: 0')
        return 0

    # REAL mode
    inv = collect_evidence_inventory(frames_dir=pre.frames_dir, config_path=pre.config_path)

    print('')
    print('Gates:')

    def _map_gate_status(raw: str) -> str:
        r = (raw or '').strip().upper()
        if r == 'PASS':
            return 'OK'
        if r in {'MISSING', 'UNVERIFIED', 'NO_AFTER', 'NO_BEFORE'}:
            return 'UNVERIFIED'
        return 'FAIL'

    gate_table: dict[str, str] = {}
    first_unverified: str | None = None
    for g in required_gates:
        raw = inv.per_gate_status.get(g, 'MISSING')
        st = _map_gate_status(str(raw))
        gate_table[g] = st
        print(f'  {g}: {st}')
        if first_unverified is None and st != 'OK':
            first_unverified = g

    if inv.missing_preconditions:
        # If inventory itself cannot validate, treat as evidence insufficient.
        print('')
        print('Blocking reasons:')
        for r in inv.missing_preconditions:
            print(f' - {r}')
        print('')
        print('Semantic audit: SKIPPED (blocked earlier)')
        print('')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        print('Exit code: 3')
        return 3

    if first_unverified is not None:
        print('')
        print(f'STOP: gate evidence not OK (first={first_unverified})')
        print('Semantic audit: SKIPPED (insufficient gate evidence)')
        print('')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        print('Exit code: 3')
        return 3

    sem = run_real_mode_audit(frames_dir=pre.frames_dir, config_path=pre.config_path, max_pairs=50)

    print('')
    if not sem.passed:
        print('Semantic audit: BLOCKED')
        if sem.report_lines:
            for line in sem.report_lines:
                print(line)
        elif sem.blocking_reasons:
            print('Blocking reasons:')
            for r in sem.blocking_reasons:
                print(f' - {r}')
        print('')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        print('Exit code: 4')
        return 4

    print('Semantic audit: PASS')
    if sem.report_lines:
        for line in sem.report_lines:
            print(line)

    print('')
    print('FINAL DECISION: OPERATIONAL_REAL')
    print('Exit code: 0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
