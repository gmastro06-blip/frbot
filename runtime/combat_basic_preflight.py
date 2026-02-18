from __future__ import annotations

import os
from typing import TypeAlias, cast

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockBattleListRow, MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.config_loader import load_rois
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.combat_basic_semantics import feedback_visible, read_target_hp_percent


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _dump_preflight_failure_frames(*, reason: str, before: Frame | None) -> None:
    try:
        from diagnostics.frame_dump import dump_enabled, dump_pair

        if not dump_enabled():
            return
        if before is None:
            return
        dump_pair(
            gate='combat_basic_preflight',
                before=before,
            after=None,
            reason=str(reason),
            out_dir=str(os.path.join('diagnostics', 'frames')),
        )
    except Exception:
        return


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _parse_mock_rows(raw: str) -> tuple[MockBattleListRow, ...]:
    s = (raw or '').strip()
    if not s:
        return ()
    rows: list[MockBattleListRow] = []
    for part in s.split(';'):
        p = part.strip()
        if not p:
            continue
        bits = [b.strip() for b in p.split(':')]
        if len(bits) < 1:
            continue
        name = bits[0]
        hp = bits[1] if len(bits) >= 2 else '1'
        atk = bits[2] if len(bits) >= 3 else '1'
        rows.append(MockBattleListRow(name=str(name), hp_bar_visible=(hp == '1'), is_attackable=(atk == '1')))
    return tuple(rows)


def combat_basic_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Preflight for combat_basic.

    Invariants:
    - Strong adapters verified (capture + input + window binding)
    - Evidence ROI minimal set exists: target_hp_bar OR combat_feedback

    Any ambiguity -> abort with canonical reason.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = str(ctx.config.mode).strip().lower()

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)
    ctx.frame_width = loaded.frame_width
    ctx.frame_height = loaded.frame_height

    hp_name = str(ctx.config.target_hp_bar_roi)
    fb_name = str(ctx.config.combat_feedback_roi)

    hp_roi = ctx.rois.get(hp_name)
    fb_roi = ctx.rois.get(fb_name)

    if hp_roi is None and fb_roi is None:
        raise PreflightFailed('combat_invalid_state')

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

        cap_source = capture_source()

        # Strong input authority is always Tibia HWND.
        binding_hwnd = int(ctx.config.window_hwnd)
        binding_title = str(ctx.config.window_title_substring)
        binding_real = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('combat_window_binding_lost')

        input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=str(ctx.config.window_title_substring))
        if int(input_hwnd) <= 0:
            raise PreflightFailed('combat_window_binding_lost')

        # Capture adapter.
        capture_real: MssBoundWindowRealCapture | ObsSourceRealCapture
        if cap_source == 'obs_source':
            src = (os.environ.get('FRBOT_OBS_SOURCE_NAME', '') or '').strip()
            if not src:
                raise PreflightFailed('obs_source_not_found')
            if loaded.frame_width is None or loaded.frame_height is None:
                raise PreflightFailed('config_invalid_schema')
            capture_real = ObsSourceRealCapture(
                obs_source_name=str(src),
                expected_width=int(loaded.frame_width),
                expected_height=int(loaded.frame_height),
                rois=ctx.rois,
                minimap_roi_name=str(ctx.config.minimap_roi),
            )
        else:
            cap_binding = binding_real
            if cap_source == 'obs':
                obs_hwnd, _obs_title = resolve_obs_projector_hwnd()
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or '')
            capture_real = MssBoundWindowRealCapture(binding=cap_binding)

        try:
            input_real = Win32HwndKeyboard(hwnd=int(input_hwnd))
        except Exception as exc:
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        cap_v = capture_real.verify()
        inp_v = input_real.verify()
        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('combat_window_binding_lost')

        f0 = capture_real.grab()
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=f0)

        if hp_roi is not None:
            hp0 = read_target_hp_percent(frame=f0, roi=hp_roi)
            if hp0 is None:
                _dump_preflight_failure_frames(reason='combat_invalid_state', before=f0)
                raise PreflightFailed('combat_invalid_state')

        if fb_roi is not None:
            # Feedback can be black at rest; only require it to be in-bounds.
            _ = feedback_visible(frame=f0, roi=fb_roi)

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('combat_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    rows = _parse_mock_rows(os.environ.get('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1'))

    selected_raw = os.environ.get('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')
    selected = int(selected_raw) if str(selected_raw).strip().isdigit() else 0

    attack_key = str(ctx.config.attack_key)
    key_kinds = {
        attack_key: 'attack',
    }

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=False,
        battle_list_rows=rows,
        battle_list_selected_row=int(selected) if str(selected_raw).strip() != '' else None,
        battle_list_row_height=16,
        click_behavior='normal',
        target_hp_current=int(os.environ.get('FRBOT_MOCK_TARGET_HP_CURRENT', '100') or '100'),
        target_hp_max=int(os.environ.get('FRBOT_MOCK_TARGET_HP_MAX', '100') or '100'),
        mock_combat_damage=_env_bool('MOCK_COMBAT_DAMAGE', False),
        mock_combat_feedback=_env_bool('MOCK_COMBAT_FEEDBACK', False),
        mock_combat_cooldown=_env_bool('MOCK_COMBAT_COOLDOWN', False),
        mock_combat_permanent_cooldown=_env_bool('MOCK_COMBAT_PERMANENT_COOLDOWN', False),
    )
    world.on_noop()

    capture_mock = MockWorldAnyCapture(world=world, verified=cap_ok)
    input_mock = MockWorldInput(world=world, verified=inp_ok)

    verified_capture = bool(capture_mock.verify().ok)
    verified_input = bool(input_mock.verify().ok)

    ctx.capture = CaptureStatus(backend=capture_mock.name, verified=verified_capture)
    ctx.input = InputStatus(backend=input_mock.name, verified=verified_input)

    if not verified_capture:
        raise PreflightFailed('capture not verified')
    if not verified_input:
        raise PreflightFailed('input not verified')

    f0 = capture_mock.grab()

    if hp_roi is not None:
        hp0 = read_target_hp_percent(frame=f0, roi=hp_roi)
        if hp0 is None:
            raise PreflightFailed('combat_invalid_state')
    if fb_roi is not None:
        _ = feedback_visible(frame=f0, roi=fb_roi)

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return combat_basic_preflight(ctx)
