from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Optional, TypedDict


_FILENAME_RE = re.compile(
    r'^(?P<gate>[a-z0-9-]+)_(?P<stamp>\d{8}-\d{6})_(?P<reason>.+)_(?P<side>before|after)(?P<mini>_minimap)?\.ppm$',
    re.IGNORECASE,
)

_GATES = ('targeting', 'healing', 'combat', 'cavebot', 'looting', 'deposit', 'trade')


@dataclass(frozen=True, slots=True)
class EvidenceInventoryResult:
    """Non-printing inventory result for audit tooling."""

    per_gate_status: dict[str, str]
    missing_preconditions: list[str]
    summary: dict


@dataclass(frozen=True, slots=True)
class GateEvidence:
    gate: str
    status: str
    full_pairs: int
    minimap_pairs: int
    examples: tuple[str, ...]


class _GateStats(TypedDict):
    full_pairs: int
    minimap_pairs: int
    any_keys: int
    examples: list[str]


@dataclass(frozen=True, slots=True)
class _GateScanDetail:
    gate: str
    before_full: int
    after_full: int
    full_pairs: int
    minimap_pairs: int
    any_keys: int
    examples: tuple[str, ...]


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip() or default


def _roi_names_for_gate(gate: str) -> tuple[str, ...]:
    # ROI-name requirements per gate, aligned to runtime env defaults.
    if gate == 'targeting':
        return (
            _env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
            _env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        )
    if gate == 'healing':
        return (
            _env_str('FRBOT_HP_BAR_ROI', 'hp_bar'),
            _env_str('FRBOT_MP_BAR_ROI', 'mp_bar'),
            _env_str('FRBOT_HP_TEXT_ROI', 'hp_text'),
            _env_str('FRBOT_MP_TEXT_ROI', 'mp_text'),
            _env_str('FRBOT_HEAL_COOLDOWN_ROI', 'heal_cooldown'),
        )
    if gate == 'combat':
        return (
            _env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
            _env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
            _env_str('FRBOT_TARGET_HP_BAR_ROI', 'target_hp_bar'),
            _env_str('FRBOT_COMBAT_COOLDOWN_ROI', 'combat_cooldown'),
            _env_str('FRBOT_COMBAT_FEEDBACK_ROI', 'combat_feedback'),
            _env_str('FRBOT_HP_BAR_ROI', 'hp_bar'),
            _env_str('FRBOT_MP_BAR_ROI', 'mp_bar'),
            _env_str('FRBOT_HP_TEXT_ROI', 'hp_text'),
            _env_str('FRBOT_MP_TEXT_ROI', 'mp_text'),
        )
    if gate == 'cavebot':
        return (_env_str('FRBOT_MINIMAP_ROI', 'minimap'),)
    if gate == 'looting':
        # Strict: require the maximum set to avoid hidden assumptions.
        return (
            _env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
            _env_str('FRBOT_LOOT_CONTAINER_OPEN_ROI', 'loot_container_open'),
            _env_str('FRBOT_LOOT_CORPSE_ROI', 'loot_corpse'),
            _env_str('FRBOT_LOOT_TAKE_ROI', 'loot_take'),
        )
    if gate == 'deposit':
        return (
            _env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
            _env_str('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container'),
        )
    if gate == 'trade':
        return (
            _env_str('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory'),
            _env_str('FRBOT_TRADE_NPC_ROI', 'trade_npc'),
            _env_str('FRBOT_TRADE_ACTION_ROI', 'trade_action'),
        )
    return ()


def _load_rois_from_config(config_path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        data = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return None, f'config_read_failed:{type(exc).__name__}:{exc}'

    rois_node = data.get('rois') if isinstance(data, dict) else None
    if not isinstance(rois_node, dict):
        return None, 'config_missing_rois'
    return rois_node, None


def _scan_detailed(frames_dir: Path) -> tuple[dict[str, _GateScanDetail], int]:
    pairs: DefaultDict[tuple[str, str, str], dict[str, bool]] = defaultdict(
        lambda: {'before_full': False, 'after_full': False, 'before_mini': False, 'after_mini': False}
    )

    for p in frames_dir.glob('*.ppm'):
        m = _FILENAME_RE.match(p.name)
        if not m:
            continue
        gate = (m.group('gate') or '').lower()
        stamp = m.group('stamp') or ''
        reason = m.group('reason') or ''
        side = (m.group('side') or '').lower()
        is_mini = bool(m.group('mini'))

        k = (gate, stamp, reason)
        if side == 'before' and not is_mini:
            pairs[k]['before_full'] = True
        if side == 'after' and not is_mini:
            pairs[k]['after_full'] = True
        if side == 'before' and is_mini:
            pairs[k]['before_mini'] = True
        if side == 'after' and is_mini:
            pairs[k]['after_mini'] = True

    per_gate: dict[str, dict] = {g: {'before_full': 0, 'after_full': 0, 'full_pairs': 0, 'minimap_pairs': 0, 'any_keys': 0, 'examples': []} for g in _GATES}

    for (gate, stamp, reason), flags in pairs.items():
        if gate not in per_gate:
            continue
        per_gate[gate]['any_keys'] += 1
        if flags['before_full']:
            per_gate[gate]['before_full'] += 1
        if flags['after_full']:
            per_gate[gate]['after_full'] += 1
        if flags['before_full'] and flags['after_full']:
            per_gate[gate]['full_pairs'] += 1
            if len(per_gate[gate]['examples']) < 3:
                per_gate[gate]['examples'].append(f'{stamp} {reason}')
        if flags['before_mini'] and flags['after_mini']:
            per_gate[gate]['minimap_pairs'] += 1

    details: dict[str, _GateScanDetail] = {}
    for g in _GATES:
        d = per_gate[g]
        details[g] = _GateScanDetail(
            gate=g,
            before_full=int(d['before_full']),
            after_full=int(d['after_full']),
            full_pairs=int(d['full_pairs']),
            minimap_pairs=int(d['minimap_pairs']),
            any_keys=int(d['any_keys']),
            examples=tuple(d['examples']),
        )

    unparsed = 0
    for p in frames_dir.glob('*.ppm'):
        if not _FILENAME_RE.match(p.name):
            unparsed += 1

    return details, int(unparsed)


def collect_evidence_inventory(frames_dir: Path, config_path: Path | None) -> EvidenceInventoryResult:
    """Collect evidence inventory for audit tooling.

    Returns:
      - per_gate_status: dict[GateName, GateEvidenceStatus]
      - missing_preconditions: list[str]
      - summary: dict
    Does not print and does not abort.
    """

    missing: list[str] = []

    if not frames_dir.exists():
        missing.append('frames_dir_missing')
        return EvidenceInventoryResult(
            per_gate_status={g: 'MISSING' for g in _GATES},
            missing_preconditions=missing,
            summary={'frames_dir': str(frames_dir), 'unparsed_ppm': 0},
        )

    details, unparsed = _scan_detailed(frames_dir)

    rois_node: Optional[dict] = None
    if config_path is None:
        missing.append('config_path_missing')
    else:
        if not config_path.exists():
            missing.append('config_path_missing')
        else:
            rois_node, err = _load_rois_from_config(config_path)
            if err is not None:
                missing.append(err)
                rois_node = None

    per_gate_status: dict[str, str] = {}
    missing_rois: dict[str, list[str]] = {}

    for g in _GATES:
        d = details.get(g)
        if d is None:
            per_gate_status[g] = 'MISSING'
            continue

        status = 'PASS'
        if d.before_full == 0 and d.after_full == 0:
            status = 'MISSING'
        elif d.before_full > 0 and d.after_full == 0:
            status = 'NO_AFTER'
        elif d.after_full > 0 and d.before_full == 0:
            status = 'NO_BEFORE'
        elif d.full_pairs == 0:
            status = 'UNVERIFIED'

        required = _roi_names_for_gate(g)
        if required and rois_node is None:
            status = 'UNVERIFIED'
        elif required and rois_node is not None:
            miss = [name for name in required if name not in rois_node]
            if miss:
                missing_rois[g] = miss
                status = 'UNVERIFIED'

        per_gate_status[g] = status

    summary = {
        'frames_dir': str(frames_dir),
        'config_path': (str(config_path) if config_path is not None else ''),
        'unparsed_ppm': int(unparsed),
        'missing_rois': missing_rois,
        'per_gate': {
            g: {
                'before_full': int(details[g].before_full),
                'after_full': int(details[g].after_full),
                'full_pairs': int(details[g].full_pairs),
                'minimap_pairs': int(details[g].minimap_pairs),
            }
            for g in _GATES
        },
    }

    return EvidenceInventoryResult(per_gate_status=per_gate_status, missing_preconditions=missing, summary=summary)


def _scan(frames_dir: Path) -> tuple[list[GateEvidence], int]:
    # Preserve CLI output and behavior: legacy statuses are computed the same.
    details, unparsed = _scan_detailed(frames_dir)

    out: list[GateEvidence] = []
    for g in _GATES:
        d = details[g]
        full_pairs = int(d.full_pairs)
        mini_pairs = int(d.minimap_pairs)
        any_keys = int(d.any_keys)

        # Strict sufficiency rule (no assumptions): require >= 1 full BEFORE+AFTER pair.
        status = 'MISSING'
        if any_keys > 0 and full_pairs == 0:
            status = 'INSUFFICIENT'
        if full_pairs > 0:
            status = 'EVIDENCE_PRESENT'

        out.append(
            GateEvidence(
                gate=g,
                status=status,
                full_pairs=full_pairs,
                minimap_pairs=mini_pairs,
                examples=tuple(d.examples),
            )
        )

    return out, int(unparsed)


def main() -> int:
    ap = argparse.ArgumentParser(description='Inventory evidence frames (PPM) per gate')
    ap.add_argument('--frames', default='diagnostics/frames', help='frames directory containing *.ppm')
    args = ap.parse_args()

    frames_dir = Path(str(args.frames))
    if not frames_dir.exists():
        print('EVIDENCE INVENTORY')
        print('DECISION: NO_EVIDENCE_FRAMES')
        print(f'FRAMES_DIR: {frames_dir}')
        return 2

    evidence, unparsed = _scan(frames_dir)

    print('EVIDENCE INVENTORY')
    print(f'FRAMES_DIR: {frames_dir}')
    print('---')
    for e in evidence:
        extras = ''
        if e.gate == 'cavebot':
            extras = f' minimap_pairs={e.minimap_pairs}'
        print(f'{e.gate}: {e.status} full_pairs={e.full_pairs}{extras}')
        for ex in e.examples:
            print(f'  example: {ex}')

    if unparsed:
        print('---')
        print(f'WARNING: {unparsed} .ppm files did not match naming convention')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
