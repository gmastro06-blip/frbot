from __future__ import annotations

import json
import os
import sys
from pathlib import Path


_REQUIRED_GATES: tuple[str, ...] = (
    'targeting_full',
    'healing_full',
    'combat_full',
    'cavebot_full',
    'looting_full',
    'deposit_full',
    'trade_full',
)


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _frames_dir() -> Path:
    raw = _env_str('FRBOT_REAL_FRAMES_DIR', '')
    if raw:
        return Path(raw)
    # Canonical per-profile default.
    prof = _env_str('FRBOT_PROFILE', '').lower()
    if prof == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if prof == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _config_path() -> Path:
    raw = _env_str('FRBOT_CONFIG_PATH', '')
    if raw:
        return Path(raw)
    return Path('config') / 'rois_prod_full.json'


def _has_minimum_evidence(frames_dir: Path) -> bool:
    try:
        has_last_result = any(frames_dir.glob('*_last_result.json'))
        has_ppm = any(frames_dir.glob('*.ppm'))
        return bool(has_last_result and has_ppm)
    except Exception:
        return False


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    if not isinstance(data, dict):
        raise ValueError('invalid_json_shape')
    return data


def _check_manifest(frames_dir: Path) -> list[str]:
    reasons: list[str] = []
    mp = frames_dir / 'evidence_manifest.json'
    if not mp.exists():
        return [f'missing_manifest:{mp.as_posix()}']

    try:
        data = _load_json(mp)
    except Exception as exc:
        return [f'manifest_unreadable:{type(exc).__name__}']

    src = str(data.get('capture_source') or '').strip().lower()
    if src != 'obs_source':
        reasons.append(f'manifest_capture_source_mismatch:{src!r}')

    env_name = _env_str('FRBOT_OBS_SOURCE_NAME', '')
    man_name = str(data.get('obs_source_name') or '').strip()
    if not man_name:
        reasons.append('manifest_obs_source_name_missing')
    if env_name and man_name and env_name != man_name:
        reasons.append('manifest_obs_source_name_mismatch')

    return reasons


def _check_gate_last_result(frames_dir: Path, gate: str) -> list[str]:
    reasons: list[str] = []
    p = frames_dir / f'{gate}_last_result.json'
    if not p.exists():
        return [f'missing_last_result:{p.as_posix()}']

    try:
        data = _load_json(p)
    except Exception as exc:
        return [f'last_result_unreadable:{gate}:{type(exc).__name__}']

    if bool(data.get('ok')) is not True:
        reasons.append(f'gate_not_ok:{gate}')

    # Contract: required fields must always exist for prod_full releases.
    reason = data.get('reason')
    if not reason or not isinstance(reason, str) or not reason.strip():
        reasons.append(f'missing_reason:{gate}')

    evidence_kind = data.get('evidence_kind')
    if not evidence_kind or not isinstance(evidence_kind, str) or not evidence_kind.strip():
        reasons.append(f'missing_evidence_kind:{gate}')

    inputs_sent = data.get('inputs_sent')
    if not isinstance(inputs_sent, int):
        reasons.append(f'missing_inputs_sent:{gate}')

    before_ppm = data.get('before_ppm')
    after_ppm = data.get('after_ppm')

    if not before_ppm or not isinstance(before_ppm, str):
        reasons.append(f'missing_before_ppm:{gate}')
    else:
        if not (frames_dir / before_ppm).exists():
            reasons.append(f'before_ppm_missing:{gate}:{before_ppm}')

    if not after_ppm or not isinstance(after_ppm, str):
        reasons.append(f'missing_after_ppm:{gate}')
    else:
        if not (frames_dir / after_ppm).exists():
            reasons.append(f'after_ppm_missing:{gate}:{after_ppm}')

    return reasons


def main() -> int:
    # Allows running via `python tools/audit_prod_full.py` from any CWD.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from diagnostics.evidence_inventory import _verify_cavebot_trace  # type: ignore

    frames_dir = _frames_dir()
    config_path = _config_path()

    reasons: list[str] = []

    prof = _env_str('FRBOT_PROFILE', '').lower()
    if prof != 'prod_full':
        reasons.append(f'profile_not_prod_full:{prof!r}')

    # Canonical preconditions (fail-fast, do not evaluate gates when missing).
    frames_raw = _env_str('FRBOT_REAL_FRAMES_DIR', '')
    if not frames_raw:
        reasons.append('real_frames_dir_missing')
    else:
        try:
            if not frames_dir.exists():
                frames_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if not frames_dir.exists() or not frames_dir.is_dir():
            reasons.append('real_frames_dir_missing')

    config_raw = _env_str('FRBOT_CONFIG_PATH', '')
    if not config_raw:
        reasons.append('config_missing')
    elif not config_path.exists() or not config_path.is_file():
        reasons.append('config_missing')

    if reasons:
        print('AUDIT: prod_full')
        print(f'Evidence dir: {frames_dir}')
        print(f'Config: {config_path}')
        print('Status: FAIL')
        for r in reasons:
            print(f'- {r}')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        return 1

    # Do not audit gates without fresh/direct evidence.
    if not _has_minimum_evidence(frames_dir):
        print('AUDIT: prod_full')
        print(f'Evidence dir: {frames_dir}')
        print(f'Config: {config_path}')
        print('Status: FAIL')
        print('- real_evidence_missing')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        return 1

    if frames_dir.exists():
        reasons.extend(_check_manifest(frames_dir))

        for gate in _REQUIRED_GATES:
            reasons.extend(_check_gate_last_result(frames_dir, gate))

        trace_reason = _verify_cavebot_trace(frames_dir)
        if trace_reason is not None:
            reasons.append(f'cavebot_trace_invalid:{trace_reason}')

    # Print report
    print('AUDIT: prod_full')
    print(f'Evidence dir: {frames_dir}')
    print(f'Config: {config_path}')
    if reasons:
        print('Status: FAIL')
        for r in reasons:
            print(f'- {r}')
        print('FINAL DECISION: NOT_OPERATIONAL_REAL')
        return 1

    print('Status: PASS')
    print('FINAL DECISION: OPERATIONAL_REAL')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
