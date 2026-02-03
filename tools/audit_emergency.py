from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _ensure_repo_root_on_syspath() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(str(raw).strip(), 10) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(str(raw).strip()) if raw is not None else float(default)
    except Exception:
        return float(default)


@dataclass(frozen=True, slots=True)
class _Result:
    ok: bool
    verdict: str
    reasons: list[str]
    features_enabled: list[str]
    features_disabled: list[str]


def _crop_rgb(*, rgb: bytes, img_w: int, img_h: int, x: int, y: int, w: int, h: int) -> bytes:
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return b''
    if x + w > img_w or y + h > img_h:
        return b''
    row_bytes = img_w * 3
    out = bytearray(w * h * 3)
    dst = 0
    for yy in range(y, y + h):
        src0 = yy * row_bytes + x * 3
        src1 = src0 + w * 3
        out[dst : dst + (w * 3)] = rgb[src0:src1]
        dst += w * 3
    return bytes(out)


def _luma_var(rgb: bytes) -> tuple[float, bool, bool]:
    # Returns (variance, all_zero, all_same).
    if not rgb or (len(rgb) % 3) != 0:
        return 0.0, True, True

    n = len(rgb) // 3
    mean = 0.0
    m2 = 0.0

    all_zero = True
    first = rgb[0:3]
    all_same = True

    for i in range(n):
        r = rgb[i * 3 + 0]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]

        if r or g or b:
            all_zero = False
        if all_same and rgb[i * 3 : i * 3 + 3] != first:
            all_same = False

        # integer luma approx, then cast to float
        y = (r * 2126 + g * 7152 + b * 722) / 10000.0
        delta = y - mean
        mean += delta / float(i + 1)
        m2 += delta * (y - mean)

    if n <= 1:
        return 0.0, all_zero, all_same
    return float(m2 / float(n - 1)), all_zero, all_same


def _check_real() -> _Result:
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from contracts.errors import PreflightFailed
    from diagnostics.frame_dump import dump_frame_ppm
    from runtime.preflight import preflight
    from runtime.profile import is_prod_emergency
    from runtime.env import parse_window_hwnd_env

    reasons: list[str] = []

    if not is_prod_emergency():
        return _Result(
            ok=False,
            verdict='NOT_READY',
            reasons=['FRBOT_PROFILE must be prod_emergency'],
            features_enabled=[],
            features_disabled=[],
        )

    config_path = Path(_env_str('FRBOT_CONFIG_PATH', ''))
    if not str(config_path):
        reasons.append('FRBOT_CONFIG_PATH missing')
    elif not config_path.exists():
        reasons.append(f'FRBOT_CONFIG_PATH not found: {config_path}')

    required_rois = ['minimap', 'battle_list', 'target_frame', 'hp_mp']

    if reasons:
        return _Result(ok=False, verdict='NOT_READY', reasons=reasons, features_enabled=[], features_disabled=[])

    # Live preflight: verifies binding + capture + minimap marker.
    try:
        cfg = RuntimeConfig(
            mode='real',
            tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
            config_path=str(config_path),
            enable_cavebot=True,
            minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap',
            window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
            window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
            player_marker_rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,255,0'),
            player_marker_tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 10),
            player_marker_min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 3),
        )
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )
        capture, _input, binding = preflight(ctx)
        binding.assert_bound()
        frame = capture.grab()
    except PreflightFailed as exc:
        reasons.append(f'preflight_failed: {exc}')
        # Best-effort dump to help debugging.
        try:
            out_dir = Path('diagnostics') / 'frames_emergency'
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_dir = None
        return _Result(ok=False, verdict='NOT_READY', reasons=reasons, features_enabled=[], features_disabled=[])

    # Validate required ROI presence + basic non-black evidence.
    enabled: list[str] = []
    disabled: list[str] = ['combat', 'looting', 'deposit', 'trade']

    out_dir = Path('diagnostics') / 'frames_emergency'
    out_dir.mkdir(parents=True, exist_ok=True)

    dump_frame_ppm(frame, out_dir / 'emergency_full.ppm')

    rois = dict(getattr(ctx, 'rois', {}) or {})
    for name in required_rois:
        roi = rois.get(name)
        if roi is None:
            reasons.append(f'missing_required_roi: {name}')
            continue

        crop = _crop_rgb(
            rgb=bytes(frame.rgb),
            img_w=int(frame.width),
            img_h=int(frame.height),
            x=int(getattr(roi, 'x', 0)),
            y=int(getattr(roi, 'y', 0)),
            w=int(getattr(roi, 'width', 0)),
            h=int(getattr(roi, 'height', 0)),
        )
        if not crop:
            reasons.append(f'roi_invalid_or_oob: {name}')
            continue

        var, all_zero, all_same = _luma_var(crop)

        # Dump ROI crop for auditing.
        try:
            from contracts.capture import Frame

            dump_frame_ppm(
                Frame(width=int(getattr(roi, 'width')), height=int(getattr(roi, 'height')), monotonic_ts_ns=0, digest_hex='', rgb=crop),
                out_dir / f'emergency_{name}.ppm',
            )
        except Exception:
            pass

        if all_zero or all_same or var < 5.0:
            reasons.append(f'roi_low_contrast_or_black: {name} var={var:.2f} all_zero={all_zero} all_same={all_same}')
            continue

        # Feature mapping.
        if name == 'minimap':
            enabled.append('cavebot_basic')
        if name == 'battle_list':
            enabled.append('targeting_basic')
        if name == 'hp_mp':
            enabled.append('healing_basic')

    ok = not reasons
    return _Result(ok=ok, verdict=('READY_FOR_PROD_EMERGENCY' if ok else 'NOT_READY'), reasons=reasons, features_enabled=sorted(set(enabled)), features_disabled=sorted(set(disabled)))


def _check_mock(repo_root: Path) -> _Result:
    # For CI: require deterministic unit tests.
    import subprocess

    tests = [
        'tests/test_runtime_log_only_after_preflight.py',
        'tests/test_abort_on_unverified_evidence.py',
        'tests/test_one_intent_one_input.py',
        'tests/test_window_binding_lost_abort.py',
        'tests/test_real_mode_never_runs_unbound.py',
    ]

    env = dict(os.environ)
    env.setdefault('FRBOT_MODE', 'mock')

    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', *tests], cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=60)
    if p.returncode == 0:
        return _Result(
            ok=True,
            verdict='READY_FOR_PROD_EMERGENCY',
            reasons=[],
            features_enabled=['mock_contracts'],
            features_disabled=['combat', 'looting', 'deposit', 'trade'],
        )

    out = (p.stdout or '') + (p.stderr or '')
    return _Result(
        ok=False,
        verdict='NOT_READY',
        reasons=['mock_audit_tests_failed', out.strip()[:2000]],
        features_enabled=[],
        features_disabled=['combat', 'looting', 'deposit', 'trade'],
    )


def main() -> int:
    repo_root = _ensure_repo_root_on_syspath()

    mode = _env_str('FRBOT_MODE', 'real').lower()

    if mode == 'mock':
        res = _check_mock(repo_root)
    elif mode == 'real':
        res = _check_real()
    else:
        res = _Result(ok=False, verdict='NOT_READY', reasons=[f'FRBOT_MODE invalid: {mode}'], features_enabled=[], features_disabled=[])

    report = {
        'ok': bool(res.ok),
        'verdict': str(res.verdict),
        'mode': str(mode),
        'profile': _env_str('FRBOT_PROFILE', ''),
        'features': {'enabled': list(res.features_enabled), 'disabled': list(res.features_disabled)},
        'reasons': list(res.reasons),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if res.ok:
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
