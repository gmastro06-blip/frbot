from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.looting_basic_preflight import looting_basic_preflight
from runtime.looting_basic_runner import execute_looting_basic_once


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # MockWorld encodes inventory_text via _roi_bytes_view(), which only supports height==1.
            # read_inventory() requires at least 6 bytes => width*3*height >= 6 => width >= 2.
            'inventory_text': {'x': 0, 'y': 0, 'width': 2, 'height': 1},
            # Supporting-only semantic ROI for looting chat verification.
            'chat_loot_area': {'x': 0, 'y': 1, 'width': 60, 'height': 10},
            # PROD_EMERGENCY contract: looting_basic requires a detectable corpse ROI.
            # Unit tests patch the detector to return True, but the ROI must exist.
            'loot_corpse': {'x': 0, 'y': 20, 'width': 20, 'height': 20},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _make_ctx(tmp_path: Path) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=_write_rois(tmp_path),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        inventory_text_roi='inventory_text',
        quick_loot_key='R',
        looting_max_attempts_per_corpse=1,
        looting_max_ticks=1,
        looting_require_inventory_delta=True,
        looting_mode='premium',
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )


def test_looting_basic_success_inventory_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '1')
    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_basic_preflight(ctx)

    out = execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert out.ok is True
    assert out.evidence_kind == 'inventory_delta'
    assert int(ctx.looting.attempts_used) == 1


def test_looting_basic_runner_aborts_on_unverified_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_basic_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert str(e.value) == 'looting_no_inventory_delta'
    assert int(ctx.looting.attempts_used) == 1


def test_looting_basic_chat_evidence_not_sufficient_when_inventory_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')
    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_LOOTING_ALLOW_CHAT_FALLBACK', '1')

    # Force chat detector to say OK, regardless of pixels.
    from runtime import looting_basic_runner
    from runtime.chat_loot_semantics import LootEvidence

    def _ok_chat(*_a: object, **_kw: object) -> LootEvidence:
        return LootEvidence(ok=True, delta_items=2, delta_gold=0, source='chat', debug={'reason': 'test'})

    monkeypatch.setattr(looting_basic_runner, 'detect_loot_from_chat', _ok_chat)

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_basic_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    # Chat changes alone must not PASS when inventory is readable.
    assert str(e.value) == 'looting_no_inventory_delta'
    assert int(ctx.looting.attempts_used) == 1


def test_looting_basic_chat_evidence_requires_fallback_flag_when_inventory_unreadable_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')
    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_LOOTING_ALLOW_CHAT_FALLBACK', '0')
    monkeypatch.setenv('FRBOT_REAL_FRAMES_DIR', str(tmp_path))

    from runtime import looting_basic_runner
    from runtime.chat_loot_semantics import LootEvidence
    from runtime import inventory_semantics
    from contracts.capture import Frame
    from contracts.evidence import Roi

    real_read_pair = inventory_semantics.read_inventory_pair_binary

    def _read_pair_before_only(before: Frame, after: Frame, roi: Roi) -> object | None:
        # Allow BEFORE read (before==after), but simulate unreadable AFTER snapshot.
        if before is after:
            return real_read_pair(before, after, roi)
        return None

    def _ok_chat(*_a: object, **_kw: object) -> LootEvidence:
        return LootEvidence(ok=True, delta_items=2, delta_gold=0, source='chat', debug={'reason': 'test'})

    monkeypatch.setattr(inventory_semantics, 'read_inventory_pair_binary', _read_pair_before_only)
    monkeypatch.setattr(looting_basic_runner, 'read_inventory_pair_binary', _read_pair_before_only)
    monkeypatch.setattr(looting_basic_runner, 'detect_loot_from_chat', _ok_chat)

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_basic_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert str(e.value) == 'looting_inventory_unreadable'
    assert int(ctx.looting.attempts_used) == 1


def test_looting_basic_chat_evidence_allows_pass_with_warning_when_inventory_unreadable_and_fallback_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')
    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_LOOTING_ALLOW_CHAT_FALLBACK', '1')
    monkeypatch.setenv('FRBOT_REAL_FRAMES_DIR', str(tmp_path))

    from runtime import looting_basic_runner
    from runtime.chat_loot_semantics import LootEvidence
    from runtime import inventory_semantics
    from contracts.capture import Frame
    from contracts.evidence import Roi
    from tools.audit_emergency import audit_looting_basic_verdict

    real_read_pair = inventory_semantics.read_inventory_pair_binary

    def _read_pair_before_only(before: Frame, after: Frame, roi: Roi) -> object | None:
        # Allow BEFORE read (before==after), but simulate unreadable AFTER snapshot.
        if before is after:
            return real_read_pair(before, after, roi)
        return None

    def _ok_chat(*_a: object, **_kw: object) -> LootEvidence:
        return LootEvidence(ok=True, delta_items=2, delta_gold=0, source='chat', debug={'reason': 'test'})

    monkeypatch.setattr(inventory_semantics, 'read_inventory_pair_binary', _read_pair_before_only)
    monkeypatch.setattr(looting_basic_runner, 'read_inventory_pair_binary', _read_pair_before_only)
    monkeypatch.setattr(looting_basic_runner, 'detect_loot_from_chat', _ok_chat)

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_basic_preflight(ctx)

    out = execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert out.ok is True
    assert out.evidence_kind == 'chat_delta_inventory_unreadable'
    assert int(ctx.looting.attempts_used) == 1

    meta_path = tmp_path / 'looting_basic_last_result.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    assert bool(meta.get('chat_ok')) is True
    assert bool(meta.get('used_chat_fallback')) is True

    ok_audit, warnings = audit_looting_basic_verdict(
        inventory_delta_ok=False,
        inventory_unreadable=True,
        chat_ok=True,
        allow_chat_fallback=True,
        used_chat_fallback=True,
    )
    assert ok_audit is True
    assert 'looting_chat_fallback_used' in warnings


def test_looting_basic_preflight_requires_inventory_roi(tmp_path: Path) -> None:
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps({'rois': {}}, sort_keys=True), encoding='utf-8')

    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=str(p),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        inventory_text_roi='inventory_text',
        quick_loot_key='R',
        looting_max_attempts_per_corpse=1,
        looting_max_ticks=1,
        looting_require_inventory_delta=True,
        looting_mode='premium',
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    with pytest.raises(PreflightFailed) as e:
        looting_basic_preflight(ctx)

    assert str(e.value) == 'looting_inventory_unreadable'


def test_looting_basic_preflight_rejects_unreadable_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_READ_FAIL', '1')

    ctx = _make_ctx(tmp_path)

    with pytest.raises(PreflightFailed) as e:
        looting_basic_preflight(ctx)

    assert str(e.value) == 'looting_inventory_unreadable'
