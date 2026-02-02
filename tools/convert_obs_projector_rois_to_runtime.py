from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast


@dataclass(frozen=True, slots=True)
class RoiDef:
    name: str
    x: int
    y: int
    width: int
    height: int


_REQUIRED_REAL_ROIS: tuple[str, ...] = (
    # Critical evidence/audit ROIs (union of evidence_inventory + semantic audit).
    'minimap',
    'battle_list',
    'inventory_text',
    'hp_bar',
    'mp_bar',
    'trade_npc',
    'trade_inventory',

    # Gate-required ROIs (defaults).
    'target_frame',
    'target_hp_bar',
    'combat_cooldown',
    'combat_feedback',
    'hp_text',
    'mp_text',
    'heal_cooldown',
    'loot_container_open',
    'loot_corpse',
    'loot_take',
    'depot_container',
    'trade_action',

    # Legacy / convenience ROIs (kept required because other tools rely on them).
    'hp_mp',
    'inventory',
    'npc_dialog',
    'trade',
)


def _load_runtime_rois(path: Path) -> dict[str, dict[str, int]]:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _hard_fail(f'merge_runtime_invalid_json: {type(exc).__name__}: {exc}')

    if not isinstance(obj, dict) or set(obj.keys()) != {'rois'}:
        _hard_fail('merge_runtime_invalid_schema: root must be {"rois": {...}}')

    rois_raw = obj.get('rois')
    if not isinstance(rois_raw, dict):
        _hard_fail('merge_runtime_invalid_schema: rois must be object')

    out: dict[str, dict[str, int]] = {}
    for name, item in rois_raw.items():
        if not isinstance(name, str) or not name.strip():
            _hard_fail('merge_runtime_invalid_schema: roi name must be non-empty string')
        if not isinstance(item, dict):
            _hard_fail(f'merge_runtime_invalid_schema: roi {name} must be object')
        x = _json_int(item.get('x'), field=f'merge.rois.{name}.x')
        y = _json_int(item.get('y'), field=f'merge.rois.{name}.y')
        w = _json_int(item.get('width'), field=f'merge.rois.{name}.width')
        h = _json_int(item.get('height'), field=f'merge.rois.{name}.height')
        out[str(name)] = {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
    return out


def _hard_fail(msg: str) -> NoReturn:
    raise SystemExit(msg)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _aspect_ratio_pair(w: int, h: int) -> tuple[int, int]:
    if w <= 0 or h <= 0:
        return (0, 0)
    g = math.gcd(int(w), int(h))
    return (int(w) // g, int(h) // g)


def _require_exact_keys(obj: dict[str, Any], *, expected: set[str], context: str) -> None:
    actual = set(obj.keys())
    if actual != set(expected):
        _hard_fail(f'roi_json_invalid: {context} keys mismatch: expected={sorted(expected)} got={sorted(actual)}')


def _json_int(value: Any, *, field: str) -> int:
    if value is None:
        _hard_fail(f'roi_json_invalid: {field} required')
    if isinstance(value, bool):
        _hard_fail(f'roi_json_invalid: {field} must be int')
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        _hard_fail(f'roi_json_invalid: {field} must be int')
    if isinstance(value, str):
        try:
            return int(value, 10)
        except Exception:
            _hard_fail(f'roi_json_invalid: {field} must be int')
    _hard_fail(f'roi_json_invalid: {field} must be int')


def _load_image_size(image_path: Path) -> tuple[int, int, str, str]:
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        _hard_fail(f'missing_dependency: pillow: {type(exc).__name__}: {exc}')

    try:
        im = Image.open(image_path)
        im.load()
        w, h = int(im.size[0]), int(im.size[1])
        return w, h, str(im.mode), str(im.format or '')
    except Exception as exc:
        _hard_fail(f'image_open_failed: {type(exc).__name__}: {exc}')


def _parse_rename(rule: str) -> tuple[str, str]:
    raw = str(rule or '').strip()
    if not raw or ':' not in raw:
        _hard_fail(f'rename_invalid: {rule!r} (expected from:to)')
    src, dst = raw.split(':', 1)
    src = src.strip()
    dst = dst.strip()
    if not src or not dst:
        _hard_fail(f'rename_invalid: {rule!r} (empty from/to)')
    return src, dst


def _validate_roi(r: RoiDef, *, img_w: int, img_h: int) -> None:
    if not r.name.strip():
        _hard_fail('roi_invalid: empty name')
    if int(r.x) < 0 or int(r.y) < 0:
        _hard_fail(f'roi_invalid: negative origin: {r.name}')
    if int(r.width) <= 0 or int(r.height) <= 0:
        _hard_fail(f'roi_invalid: non-positive size: {r.name}')
    if int(r.x) + int(r.width) > int(img_w) or int(r.y) + int(r.height) > int(img_h):
        _hard_fail(f'roi_invalid: out_of_bounds: {r.name}')


def _load_obs_projector_rois(path: Path) -> tuple[dict[str, Any], list[RoiDef]]:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _hard_fail(f'roi_json_invalid: {type(exc).__name__}: {exc}')

    if not isinstance(obj, dict):
        _hard_fail('roi_json_invalid: root must be object')

    obj = cast(dict[str, Any], obj)
    _require_exact_keys(obj, expected={'source', 'roi_mode', 'reference_resolution', 'rois'}, context='root')

    if str(obj.get('source') or '') != 'OBS Projector':
        _hard_fail('roi_json_invalid: source must be "OBS Projector"')
    if str(obj.get('roi_mode') or '') != 'absolute_px':
        _hard_fail('roi_json_invalid: roi_mode must be "absolute_px"')

    ref_raw = obj.get('reference_resolution')
    if not isinstance(ref_raw, dict):
        _hard_fail('roi_json_invalid: reference_resolution must be object')
    ref = cast(dict[str, Any], ref_raw)
    _require_exact_keys(ref, expected={'width', 'height'}, context='reference_resolution')

    rois_raw = obj.get('rois')
    if not isinstance(rois_raw, list) or len(rois_raw) < 1:
        _hard_fail('roi_json_invalid: rois must be non-empty list')

    rois: list[RoiDef] = []
    seen_names: set[str] = set()
    for i, item in enumerate(rois_raw):
        if not isinstance(item, dict):
            _hard_fail(f'roi_json_invalid: rois[{i}] must be object')
        item = cast(dict[str, Any], item)
        _require_exact_keys(item, expected={'name', 'x', 'y', 'width', 'height'}, context=f'rois[{i}]')

        r = RoiDef(
            name=str(item.get('name') or '').strip(),
            x=_json_int(item.get('x'), field=f'rois[{i}].x'),
            y=_json_int(item.get('y'), field=f'rois[{i}].y'),
            width=_json_int(item.get('width'), field=f'rois[{i}].width'),
            height=_json_int(item.get('height'), field=f'rois[{i}].height'),
        )
        if not r.name:
            _hard_fail(f'roi_json_invalid: rois[{i}].name empty')
        if r.name in seen_names:
            _hard_fail(f'roi_json_invalid: duplicate roi name: {r.name}')
        seen_names.add(r.name)
        rois.append(r)

    return obj, rois


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Convert obs_projector_rois.json into runtime ROI config schema {"rois": {...}}')
    ap.add_argument('--in-json', default='obs_projector_rois.json', help='Input ROI JSON (OBS Projector format)')
    ap.add_argument('--image', default='diagnostics/obs_projector_full.ppm', help='Reference capture used for calibration (PPM)')
    ap.add_argument('--out-json', default='obs_projector_rois_runtime.json', help='Output runtime config path')
    ap.add_argument('--out-log', default='obs_projector_rois_runtime_convert.log.json', help='Conversion log path')
    ap.add_argument('--mode', default='real', choices=['real', 'mock'], help='If real: require minimal ROI inventory')
    ap.add_argument(
        '--merge-runtime',
        default='',
        help='Optional runtime ROI config (schema {"rois": {...}}). Missing ROI names are copied from this file.',
    )
    ap.add_argument(
        '--rename',
        action='append',
        default=[],
        help='Explicit ROI rename mapping from:to (repeatable). Example: --rename battlelist:battle_list',
    )
    args = ap.parse_args(argv)

    in_json = Path(str(args.in_json)).resolve()
    image_path = Path(str(args.image)).resolve()
    out_json = Path(str(args.out_json)).resolve()
    out_log = Path(str(args.out_log)).resolve()
    merge_runtime = Path(str(args.merge_runtime)).resolve() if str(args.merge_runtime or '').strip() else None

    if not in_json.exists():
        _hard_fail(f'roi_json_not_found: {in_json}')
    if not image_path.exists():
        _hard_fail(f'image_not_found: {image_path}')
    if merge_runtime is not None and not merge_runtime.exists():
        _hard_fail(f'merge_runtime_not_found: {merge_runtime}')

    src_obj, rois = _load_obs_projector_rois(in_json)

    ref = cast(dict[str, Any], src_obj['reference_resolution'])
    ref_w = _json_int(ref.get('width'), field='reference_resolution.width')
    ref_h = _json_int(ref.get('height'), field='reference_resolution.height')

    cur_w, cur_h, cur_mode, cur_format = _load_image_size(image_path)
    ref_ar = _aspect_ratio_pair(ref_w, ref_h)
    cur_ar = _aspect_ratio_pair(cur_w, cur_h)

    drift_ok = (cur_w == ref_w) and (cur_h == ref_h) and (cur_ar == ref_ar)
    if not drift_ok:
        _hard_fail('resolution_or_aspect_drift_detected: full recalibration required')

    rename_map: dict[str, str] = {}
    for rule in list(args.rename or []):
        a, b = _parse_rename(str(rule))
        if a in rename_map and rename_map[a] != b:
            _hard_fail(f'rename_conflict: {a} -> {rename_map[a]} and {b}')
        rename_map[a] = b

    # Apply explicit renames deterministically.
    mapped: list[RoiDef] = []
    seen_after: set[str] = set()
    for r in rois:
        new_name = rename_map.get(r.name, r.name)
        rr = RoiDef(name=str(new_name), x=int(r.x), y=int(r.y), width=int(r.width), height=int(r.height))
        if rr.name in seen_after:
            _hard_fail(f'roi_json_invalid: duplicate roi name after rename: {rr.name}')
        seen_after.add(rr.name)
        mapped.append(rr)

    # Validate ROI bounds against image.
    for r in mapped:
        _validate_roi(r, img_w=cur_w, img_h=cur_h)

    runtime_rois: dict[str, dict[str, int]] = {}
    for r in mapped:
        runtime_rois[r.name] = {
            'x': int(r.x),
            'y': int(r.y),
            'width': int(r.width),
            'height': int(r.height),
        }

    merged_from: list[str] = []
    if merge_runtime is not None:
        merge_rois = _load_runtime_rois(merge_runtime)
        for name, roi in merge_rois.items():
            if name not in runtime_rois:
                runtime_rois[name] = dict(roi)
                merged_from.append(str(name))

    # Validate ROI bounds against image (including merged ROIs).
    for name, roi in runtime_rois.items():
        r = RoiDef(name=str(name), x=int(roi['x']), y=int(roi['y']), width=int(roi['width']), height=int(roi['height']))
        _validate_roi(r, img_w=cur_w, img_h=cur_h)

    # Hard invariant: runtime config schema is ONLY {"rois": {...}}
    out_payload: dict[str, Any] = {'rois': runtime_rois}

    # Enforce required ROIs in real mode.
    required: tuple[str, ...] = ()
    if str(args.mode).strip().lower() == 'real':
        required = _REQUIRED_REAL_ROIS
        missing = [name for name in required if name not in runtime_rois]
        if missing:
            _hard_fail(f'missing_required_rois_for_real_mode: {missing}')

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding='utf-8')

    log_obj = {
        'input': {
            'rois_json': {'path': str(in_json), 'sha256': _sha256_file(in_json), 'bytes': int(in_json.stat().st_size)},
            'image': {
                'path': str(image_path),
                'sha256': _sha256_file(image_path),
                'bytes': int(image_path.stat().st_size),
                'width': int(cur_w),
                'height': int(cur_h),
                'aspect_ratio': {'w': int(cur_ar[0]), 'h': int(cur_ar[1])},
                'pixel_format': str(cur_mode),
                'container_format': str(cur_format),
            },
        },
        'validation': {
            'source': str(src_obj.get('source') or ''),
            'roi_mode': str(src_obj.get('roi_mode') or ''),
            'reference_resolution': {'width': int(ref_w), 'height': int(ref_h), 'aspect_ratio': {'w': int(ref_ar[0]), 'h': int(ref_ar[1])}},
            'drift_ok': bool(drift_ok),
            'mode': str(args.mode),
            'required_rois': list(required),
        },
        'mapping': {
            'rename_map': dict(rename_map),
            'roi_names_in': [r.name for r in rois],
            'roi_names_out': [r.name for r in mapped],
            'merge_runtime': (str(merge_runtime) if merge_runtime is not None else ''),
            'merged_from_runtime': list(sorted(merged_from)),
        },
        'output': {
            'runtime_config': {'path': str(out_json), 'sha256': _sha256_file(out_json), 'bytes': int(out_json.stat().st_size)},
        },
    }

    out_log.parent.mkdir(parents=True, exist_ok=True)
    out_log.write_text(json.dumps(log_obj, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps({'ok': True, 'out_json': str(out_json)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
