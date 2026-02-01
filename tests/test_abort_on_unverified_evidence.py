from __future__ import annotations

import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from combat_entrypoint import run_combat_only
from looting_entrypoint import run_looting_only
from trade_entrypoint import run_trade_only


def _write_rois(tmp_path: Path, rois: dict[str, dict[str, int]]) -> str:
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps({'rois': rois}), encoding='utf-8')
    return str(p)


def test_trade_abort_unverified_evidence_is_abort_first(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    rois = {
        'trade_npc': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
        'trade_inventory': {'x': 2, 'y': 6, 'width': 3, 'height': 1},
        'trade_action': {'x': 2, 'y': 10, 'width': 1, 'height': 1},
    }

    monkeypatch.setenv('FRBOT_MODE', 'trade')
    monkeypatch.setenv('FRBOT_TRADE_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, rois))
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('FRBOT_TRADE_MAX_TICKS', '5')
    # Even if attempts are high, trade must still do 1 input then abort.
    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '5')
    monkeypatch.setenv('FRBOT_TRADE_EXPECTED_NPC_ID', '7')

    monkeypatch.setenv('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory')
    monkeypatch.setenv('FRBOT_TRADE_NPC_ROI', 'trade_npc')
    monkeypatch.setenv('FRBOT_TRADE_ACTION_ROI', 'trade_action')

    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    monkeypatch.setenv('FRBOT_MOCK_TRADE_GOLD', '100')
    monkeypatch.setenv('FRBOT_MOCK_TRADE_ITEMS', '0')
    monkeypatch.setenv('FRBOT_MOCK_TRADE_CAP_USED', '10')

    monkeypatch.setenv('MOCK_TRADE_NPC_PRESENT', 'true')
    monkeypatch.setenv('MOCK_TRADE_WRONG_NPC', 'false')
    monkeypatch.setenv('MOCK_TRADE_BUY_OK', 'false')
    monkeypatch.setenv('MOCK_TRADE_SELL_OK', 'false')
    monkeypatch.setenv('MOCK_TRADE_NO_DELTA', 'false')
    # Incomplete delta => must abort after the single input.
    monkeypatch.setenv('MOCK_TRADE_GOLD_ONLY', 'true')
    monkeypatch.setenv('MOCK_TRADE_ITEM_ONLY', 'false')

    assert run_trade_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log


def test_combat_abort_unverified_evidence_is_abort_first(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    rois = {
        'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
        'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
        'target_hp_bar': {'x': 2, 'y': 95, 'width': 80, 'height': 6},
        'combat_cooldown': {'x': 2, 'y': 104, 'width': 6, 'height': 3},
        'combat_feedback': {'x': 10, 'y': 104, 'width': 6, 'height': 3},
        'hp_bar': {'x': 2, 'y': 112, 'width': 60, 'height': 6},
        'mp_bar': {'x': 2, 'y': 120, 'width': 60, 'height': 6},
        'hp_text': {'x': 2, 'y': 128, 'width': 4, 'height': 1},
        'mp_text': {'x': 2, 'y': 130, 'width': 4, 'height': 1},
    }

    monkeypatch.setenv('FRBOT_MODE', 'combat')
    monkeypatch.setenv('FRBOT_COMBAT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, rois))
    monkeypatch.setenv('FRBOT_ENABLE_COMBAT', '1')

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_COMBAT_MAX_TICKS', '10')

    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')

    monkeypatch.setenv('FRBOT_ATTACK_KEY', 'F2')

    # Even if attempts are high, unverified evidence must abort immediately.
    monkeypatch.setenv('FRBOT_MAX_ATTEMPTS_PER_TARGET', '5')
    monkeypatch.setenv('FRBOT_MAX_TIME_MS_PER_TARGET', '2500')

    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')

    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_CURRENT', '100')
    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_MAX', '100')

    monkeypatch.setenv('MOCK_COMBAT_DAMAGE', 'false')
    monkeypatch.setenv('MOCK_COMBAT_FEEDBACK', 'false')
    monkeypatch.setenv('MOCK_COMBAT_COOLDOWN', 'false')
    monkeypatch.setenv('MOCK_COMBAT_PERMANENT_COOLDOWN', 'false')

    assert run_combat_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert 'inputs_sent=1' in runtime_log
    assert 'inputs_sent=2' not in runtime_log


def test_looting_premium_unverified_evidence_is_abort_first(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    rois = {
        'inventory_text': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
        'loot_container_open': {'x': 2, 'y': 6, 'width': 1, 'height': 1},
        'loot_corpse': {'x': 2, 'y': 10, 'width': 10, 'height': 10},
        'loot_take': {'x': 20, 'y': 10, 'width': 10, 'height': 10},
    }

    monkeypatch.setenv('FRBOT_MODE', 'looting')
    monkeypatch.setenv('FRBOT_LOOTING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, rois))

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_TICKS', '5')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_ATTEMPTS', '5')

    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'premium')
    monkeypatch.setenv('FRBOT_QUICK_LOOT_KEY', 'R')

    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    monkeypatch.setenv('FRBOT_MOCK_INV_GOLD', '0')
    monkeypatch.setenv('FRBOT_MOCK_INV_CAP_USED', '0')

    monkeypatch.setenv('MOCK_LOOT_PREMIUM', 'true')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', 'false')
    monkeypatch.setenv('MOCK_LOOT_CONTAINER_OPENS', 'false')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_READ_FAIL', 'false')

    assert run_looting_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # Bounded: must not exceed max attempts.
    assert '"attempts_used":5' in runtime_log
    assert '"attempts_used":6' not in runtime_log
    # Final abort must be explicit.
    assert '"abort_reason":"looting_unverified_loot"' in runtime_log
