from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REQUIRED_GATES: tuple[str, ...] = (
    "targeting",
    "healing",
    "combat",
    "cavebot",
    "looting",
    "deposit",
    "trade",
)

_REQUIRED_ROIS: tuple[str, ...] = (
    "minimap",
    "battle_list",
    "hp_mp",
    "target_frame",
)


@dataclass(frozen=True, slots=True)
class _CheckResult:
    missing: list[str]
    invalid: list[str]


def _env_required_abs_path(name: str) -> tuple[Path | None, str | None]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None, f"env_missing:{name}"
    p = Path(str(raw).strip())
    if not p.is_absolute():
        return None, f"env_not_absolute:{name}"
    return p, None


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        # Windows PowerShell "utf8" writes a UTF-8 BOM by default; tolerate it.
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except Exception as exc:
        return None, f"json_read_failed:{path.name}:{type(exc).__name__}"


def _safe_join_under(base: Path, ref: str) -> tuple[Path | None, str | None]:
    # Manifest must reference files inside the frames dir (no absolute, no traversal).
    r = (ref or "").strip()
    if not r:
        return None, "manifest_ref_empty"
    rp = Path(r)
    if rp.is_absolute():
        return None, f"manifest_ref_absolute:{r}"
    joined = (base / rp).resolve()
    base_resolved = base.resolve()
    try:
        joined.relative_to(base_resolved)
    except Exception:
        return None, f"manifest_ref_outside_frames_dir:{r}"
    return joined, None


def _validate_manifest(frames_dir: Path, *, expected_version: str) -> _CheckResult:
    missing: list[str] = []
    invalid: list[str] = []

    manifest_path = frames_dir / "_evidence_manifest.json"
    if not manifest_path.exists():
        missing.append(f"missing_manifest:{manifest_path}")
        return _CheckResult(missing=missing, invalid=invalid)

    data, err = _read_json(manifest_path)
    if err is not None:
        invalid.append(f"invalid_manifest:{err}")
        return _CheckResult(missing=missing, invalid=invalid)

    if not isinstance(data, dict):
        invalid.append("invalid_manifest:not_object")
        return _CheckResult(missing=missing, invalid=invalid)

    ver = str(data.get("version") or "").strip()
    if ver != expected_version:
        invalid.append(f"invalid_manifest_version:expected={expected_version}:got={ver}")

    ts = str(data.get("timestamp") or "").strip()
    if not ts:
        missing.append("manifest_missing:timestamp")

    hwnd = str(data.get("window_hwnd") or "").strip()
    if not hwnd:
        missing.append("manifest_missing:window_hwnd")

    gates = data.get("gates")
    if not isinstance(gates, dict):
        invalid.append("invalid_manifest:gates_not_object")
        return _CheckResult(missing=missing, invalid=invalid)

    gate_keys = tuple(sorted(str(k) for k in gates.keys()))
    required_keys = tuple(sorted(_REQUIRED_GATES))
    if gate_keys != required_keys:
        invalid.append(f"invalid_manifest_gates:expected={','.join(required_keys)}:got={','.join(gate_keys)}")
        return _CheckResult(missing=missing, invalid=invalid)

    for gate in _REQUIRED_GATES:
        pairs = gates.get(gate)
        if not isinstance(pairs, list) or not pairs:
            missing.append(f"missing_gate_pairs:{gate}")
            continue

        # At least one BEFORE/AFTER pair.
        ok_any = False
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                invalid.append(f"invalid_gate_pair_shape:{gate}")
                continue
            before_ref = str(pair[0] or "").strip()
            after_ref = str(pair[1] or "").strip()
            before_path, e1 = _safe_join_under(frames_dir, before_ref)
            after_path, e2 = _safe_join_under(frames_dir, after_ref)
            if e1 is not None:
                invalid.append(f"invalid_manifest_ref:{gate}:{e1}")
                continue
            if e2 is not None:
                invalid.append(f"invalid_manifest_ref:{gate}:{e2}")
                continue
            assert before_path is not None and after_path is not None
            if not before_path.exists():
                missing.append(f"missing_ppm:{gate}:{before_ref}")
                continue
            if not after_path.exists():
                missing.append(f"missing_ppm:{gate}:{after_ref}")
                continue
            if before_path.suffix.lower() != ".ppm" or after_path.suffix.lower() != ".ppm":
                invalid.append(f"invalid_pair_not_ppm:{gate}")
                continue
            ok_any = True

        if not ok_any:
            missing.append(f"missing_gate_before_after_pair:{gate}")

    return _CheckResult(missing=missing, invalid=invalid)


def _validate_config(config_path: Path) -> _CheckResult:
    missing: list[str] = []
    invalid: list[str] = []

    if not config_path.exists():
        missing.append(f"missing_config:{config_path}")
        return _CheckResult(missing=missing, invalid=invalid)

    data, err = _read_json(config_path)
    if err is not None:
        invalid.append(f"invalid_config:{err}")
        return _CheckResult(missing=missing, invalid=invalid)

    if not isinstance(data, dict) or set(data.keys()) != {"rois"}:
        invalid.append("invalid_config_schema:top_level_must_be_only_rois")
        return _CheckResult(missing=missing, invalid=invalid)

    rois = data.get("rois")
    if not isinstance(rois, dict):
        invalid.append("invalid_config_schema:rois_must_be_object")
        return _CheckResult(missing=missing, invalid=invalid)

    for name in _REQUIRED_ROIS:
        if name not in rois:
            missing.append(f"missing_required_roi:{name}")

    return _CheckResult(missing=missing, invalid=invalid)


def _emit_hard_stop(*, missing: list[str], invalid: list[str]) -> int:
    payload = {
        "reason": "phase1_preconditions_failed",
        "missing": sorted(set(missing)),
        "invalid": sorted(set(invalid)),
        "hint": "Run bootstrap_real_evidence.ps1 for both versions",
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 2


def main() -> int:
    missing: list[str] = []
    invalid: list[str] = []

    old_dir, e_old = _env_required_abs_path("FRBOT_REAL_FRAMES_DIR_OLD")
    new_dir, e_new = _env_required_abs_path("FRBOT_REAL_FRAMES_DIR_NEW")
    cfg_path, e_cfg = _env_required_abs_path("FRBOT_CONFIG_PATH")

    for e in (e_old, e_new, e_cfg):
        if e is None:
            continue
        # Missing vs invalid is part of the structured report.
        if e.startswith("env_missing"):
            missing.append(e)
        else:
            invalid.append(e)

    if old_dir is None or new_dir is None or cfg_path is None:
        return _emit_hard_stop(missing=missing, invalid=invalid)

    if not old_dir.exists():
        missing.append(f"frames_dir_missing:old:{old_dir}")
    if not new_dir.exists():
        missing.append(f"frames_dir_missing:new:{new_dir}")

    if missing:
        return _emit_hard_stop(missing=missing, invalid=invalid)

    # Validate manifests.
    old_res = _validate_manifest(old_dir, expected_version="15.x")
    new_res = _validate_manifest(new_dir, expected_version="15.y")
    missing.extend(old_res.missing)
    missing.extend(new_res.missing)
    invalid.extend(old_res.invalid)
    invalid.extend(new_res.invalid)

    # Validate config.
    cfg_res = _validate_config(cfg_path)
    missing.extend(cfg_res.missing)
    invalid.extend(cfg_res.invalid)

    if missing or invalid:
        return _emit_hard_stop(missing=missing, invalid=invalid)

    print("PHASE 1 READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
