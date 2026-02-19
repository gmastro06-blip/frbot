from __future__ import annotations

import os
import time
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.capture.meld_real import MeldBoundWindowRealCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockBattleListRow, MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.combat_runner import _get_locked_target_name, _read_attack_cooldown_active
from runtime.config_loader import load_rois
from runtime.healing_runner import _read_hp_mp
from runtime.combat_semantics import read_target_hp_percent
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.pacing import wait_until_ns
from runtime.error_policy import should_reraise


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MeldBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


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
        rows.append(
            MockBattleListRow(
                name=str(name),
                hp_bar_visible=(hp == '1'),
                is_attackable=(atk == '1'),
            )
        )
    return tuple(rows)


def combat_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Combat-only preflight.

    Invariants:
    - Target must already be locked (battle list highlight + target frame identity)
    - HP/MP must be semantically readable (reuse healing reader)
    - Attack cooldown must be semantically observable
    - Target HP bar must be readable

    Any ambiguity -> combat_invalid_state.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    # Required ROIs.
    required = (
        ctx.config.battle_list_roi,
        ctx.config.target_frame_roi,
        ctx.config.target_hp_bar_roi,
        ctx.config.combat_cooldown_roi,
        ctx.config.combat_feedback_roi,
        ctx.config.hp_bar_roi,
        ctx.config.mp_bar_roi,
        ctx.config.hp_text_roi,
        ctx.config.mp_text_roi,
    )

    for name in required:
        if name and (ctx.rois.get(name) is None):
            raise PreflightFailed('combat_ambiguous_result')

    if mode == 'real':
        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency' and backend not in {'mss', 'meld'}:
            raise PreflightFailed('capture_invalid')

        cap_source = capture_source()

        # InputAuthority (strict): always bind to Tibia HWND/title; capture is separate.
        binding_real = Win32WindowBinding(
            hwnd=int(ctx.config.window_hwnd),
            title_substring=ctx.config.window_title_substring,
        )
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('combat_ambiguous_result')

        capture_real: MssBoundWindowRealCapture | MeldBoundWindowRealCapture | ObsSourceRealCapture

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
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=(os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or ''))

            if backend == 'meld':
                try:
                    capture_real = MeldBoundWindowRealCapture(binding=cap_binding)
                except ImportError as input_exc:
                    raise PreflightFailed('capture_black_or_unavailable') from input_exc
            else:
                try:
                    capture_real = MssBoundWindowRealCapture(binding=cap_binding)
                except ImportError as input_exc:
                    raise PreflightFailed(str(input_exc)) from input_exc

        try:
            input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
            if input_hwnd <= 0:
                raise PreflightFailed('combat_ambiguous_result')
            input_real = Win32HwndKeyboard(hwnd=int(input_hwnd))
        except Exception as input_exc:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise

            raise PreflightFailed(f'failed to initialize win32 input: {type(input_exc).__name__}: {input_exc}') from input_exc

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
        except Exception as exc:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise

            raise PreflightFailed('combat_ambiguous_result') from exc

        f = capture_real.grab()

        # Evidence-or-abort: even if we fail preflight, stash a frame so entrypoints
        # can dump certifiable BEFORE/AFTER artifacts.
        try:
            from diagnostics.last_frames import record_after, record_before

            record_before('combat', f)
            record_after('combat', f)
            record_before('combat_full', f)
            record_after('combat_full', f)
        except Exception:
            if should_reraise():
                raise

        # Target must be locked and identifiable.
        # In REAL, target lock can be lost between gates (e.g., while healing).
        # For semantic next-target keybinds, attempt one acquisition and retry.
        try:
            name = _get_locked_target_name(ctx, f)
        except PreflightFailed as exc:
            if str(exc) != 'combat_target_not_locked':
                raise

            key_norm = str(ctx.config.attack_key or '').strip().lower()
            is_next_target_key = key_norm in {'avpag', 'pgdn', 'pagedown'}
            if not is_next_target_key:
                raise

            try:
                binding_real.assert_bound()
                input_real.press_key(str(ctx.config.attack_key))
            except Exception:
                raise

            # Bounded wait for UI/capture to reflect the new lock.
            wait_until_ns(int(time.monotonic_ns() + (200 * 1_000_000)))
            f2 = capture_real.grab()
            try:
                from diagnostics.last_frames import record_after, record_before

                record_before('combat', f2)
                record_after('combat', f2)
                record_before('combat_full', f2)
                record_after('combat_full', f2)
            except Exception:
                if should_reraise():
                    raise

            name = _get_locked_target_name(ctx, f2)

        if not name:
            raise PreflightFailed('combat_target_not_locked')

        # Store verified state.
        ctx.targeting.target.target_name = str(name)
        ctx.targeting.target.target_id = f'locked:{name}'
        ctx.targeting.target.locked = True
        ctx.targeting.target.confidence = 1.0

        # HP/MP readable.
        _read_hp_mp(ctx, f)

        # Cooldown must be observable (value may be active).
        _read_attack_cooldown_active(ctx, f)

        # Target HP readable.
        hp_roi = ctx.rois.get(ctx.config.target_hp_bar_roi)
        if hp_roi is None or read_target_hp_percent(f, hp_roi) is None:
            raise PreflightFailed('combat_ambiguous_result')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('combat_ambiguous_result')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    rows = _parse_mock_rows(os.environ.get('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1'))

    # For combat mode, target must start locked: default to selected row 0.
    selected_raw = os.environ.get('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')
    selected = int(selected_raw) if selected_raw.strip().isdigit() else 0

    attack_key = str(ctx.config.attack_key)

    key_kinds = {
        attack_key: 'attack',
    }

    def env_bool(name: str, default: bool = False) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return bool(default)
        return raw.strip().lower() in {'1', 'true', 'yes', 'on'}

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=False,
        battle_list_rows=rows,
        battle_list_selected_row=int(selected) if str(selected_raw).strip() != '' else None,
        battle_list_row_height=16,
        click_behavior='normal',
        # Combat target default HP.
        target_hp_current=int(os.environ.get('FRBOT_MOCK_TARGET_HP_CURRENT', '100') or '100'),
        target_hp_max=int(os.environ.get('FRBOT_MOCK_TARGET_HP_MAX', '100') or '100'),
        mock_combat_damage=env_bool('MOCK_COMBAT_DAMAGE', False),
        mock_combat_feedback=env_bool('MOCK_COMBAT_FEEDBACK', False),
        mock_combat_cooldown=env_bool('MOCK_COMBAT_COOLDOWN', False),
        mock_combat_permanent_cooldown=env_bool('MOCK_COMBAT_PERMANENT_COOLDOWN', False),
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

    f = capture_mock.grab()

    name = _get_locked_target_name(ctx, f)
    if not name:
        raise PreflightFailed('combat_target_not_locked')

    ctx.targeting.target.target_name = str(name)
    ctx.targeting.target.target_id = f'locked:{name}'
    ctx.targeting.target.locked = True
    ctx.targeting.target.confidence = 1.0

    _read_hp_mp(ctx, f)
    _read_attack_cooldown_active(ctx, f)

    hp_roi = ctx.rois.get(ctx.config.target_hp_bar_roi)
    if hp_roi is None or read_target_hp_percent(f, hp_roi) is None:
        raise PreflightFailed('combat_ambiguous_result')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return combat_preflight(ctx)
