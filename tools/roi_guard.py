from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Guard: hard-stop unless ROI config contains every ROI required by
# - evidence inventory gates (diagnostics/evidence_inventory.py)
# - semantic calibration prerequisites (diagnostics/real_mode_audit.py)


# Critical ROIs required by diagnostics/real_mode_audit.py calibration.
# Keep in sync with that module.
_CRITICAL_ROIS: tuple[str, ...] = (
    "minimap",
    "battle_list",
    "inventory_text",
    "hp_bar",
    "mp_bar",
    "trade_npc",
    "trade_inventory",
)


@dataclass(frozen=True, slots=True)
class _CheckResult:
    missing: list[str]
    invalid: list[str]


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        # Tolerate UTF-8 BOM (PowerShell default "utf8").
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except Exception as exc:
        return None, f"json_read_failed:{path.name}:{type(exc).__name__}"


def _resolve_config_path(raw: str | None) -> tuple[Path | None, str | None]:
    if raw is None or str(raw).strip() == "":
        return None, "env_missing:FRBOT_CONFIG_PATH"
    p = Path(str(raw).strip())
    if not p.is_absolute():
        return None, "env_not_absolute:FRBOT_CONFIG_PATH"
    return p, None


def _load_rois_only_config(config_path: Path) -> tuple[dict[str, Any] | None, _CheckResult]:
    missing: list[str] = []
    invalid: list[str] = []

    if not config_path.exists():
        missing.append(f"missing_config:{config_path}")
        return None, _CheckResult(missing=missing, invalid=invalid)

    data, err = _read_json(config_path)
    if err is not None:
        invalid.append(f"invalid_config:{err}")
        return None, _CheckResult(missing=missing, invalid=invalid)

    if not isinstance(data, dict):
        invalid.append("invalid_config_schema:top_level_must_be_object")
        return None, _CheckResult(missing=missing, invalid=invalid)

    keys = set(data.keys())
    allowed = ({"rois"}, {"rois", "frame"}, {"rois", "certified_manifest"}, {"rois", "frame", "certified_manifest"})
    if keys not in allowed:
        invalid.append("invalid_config_schema:top_level_keys")
        return None, _CheckResult(missing=missing, invalid=invalid)

    rois_node = data.get("rois")
    if not isinstance(rois_node, dict):
        invalid.append("invalid_config_schema:rois_must_be_object")
        return None, _CheckResult(missing=missing, invalid=invalid)

    return rois_node, _CheckResult(missing=missing, invalid=invalid)


def _emit_hard_stop(*, missing: list[str], invalid: list[str], details: dict[str, Any]) -> int:
    payload = {
        "reason": "roi_preconditions_failed",
        "missing": sorted(set(missing)),
        "invalid": sorted(set(invalid)),
        "details": details,
        "hint": "Populate FRBOT_CONFIG_PATH ROIs required by evidence inventory and semantic calibration",
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--config",
        dest="config",
        default=None,
        help="Absolute path to ROI config JSON (defaults to FRBOT_CONFIG_PATH)",
    )

    args = parser.parse_args(argv)

    missing: list[str] = []
    invalid: list[str] = []

    cfg_path: Path

    if args.config is not None:
        cfg_path = Path(str(args.config).strip())
        if not cfg_path.is_absolute():
            invalid.append("arg_not_absolute:--config")
            return _emit_hard_stop(
                missing=missing,
                invalid=invalid,
                details={"config_path": str(cfg_path)},
            )
    else:
        cfg_path_opt, e = _resolve_config_path(os.environ.get("FRBOT_CONFIG_PATH"))
        if e is not None:
            if e.startswith("env_missing"):
                missing.append(e)
            else:
                invalid.append(e)
            return _emit_hard_stop(missing=missing, invalid=invalid, details={})
        assert cfg_path_opt is not None
        cfg_path = cfg_path_opt

    rois_node, res = _load_rois_only_config(cfg_path)
    missing.extend(res.missing)
    invalid.extend(res.invalid)
    if rois_node is None:
        return _emit_hard_stop(missing=missing, invalid=invalid, details={"config_path": str(cfg_path)})

    # Import here so the guard can still print a deterministic hard-stop if
    # config path resolution fails before imports.
    from diagnostics import evidence_inventory

    required_by_gate: dict[str, list[str]] = {}
    for gate in getattr(evidence_inventory, "_GATES", ()):  # noqa: SLF001
        required_names_tuple = tuple(str(x) for x in evidence_inventory._roi_names_for_gate(str(gate)))  # noqa: SLF001
        if required_names_tuple:
            required_by_gate[str(gate)] = sorted(set(required_names_tuple))

    missing_by_gate: dict[str, list[str]] = {}
    for gate, required_names in required_by_gate.items():
        miss = []
        for name in required_names:
            # Check both flat format (e.g., "healing:hp_mp") and nested format
            if name in rois_node:
                continue
            # Try nested format: "healing:hp_mp" -> rois_node["healing"]["hp_mp"]
            if ':' in name:
                parts = name.split(':', 1)
                if (parts[0] in rois_node and
                    isinstance(rois_node[parts[0]], dict) and
                    parts[1] in rois_node[parts[0]]):
                    continue
            miss.append(name)
        if miss:
            missing_by_gate[gate] = miss

    missing_critical = [name for name in _CRITICAL_ROIS if name not in rois_node]

    details = {
        "config_path": str(cfg_path),
        "missing_rois_by_gate": missing_by_gate,
        "missing_critical_rois": missing_critical,
        "required_critical_rois": list(_CRITICAL_ROIS),
    }

    if missing_by_gate or missing_critical:
        # Make the high-signal missing items also show up in the top-level missing list.
        for gate, names in sorted(missing_by_gate.items()):
            for name in names:
                missing.append(f"missing_required_roi:{gate}:{name}")
        for name in missing_critical:
            missing.append(f"missing_critical_roi:{name}")
        return _emit_hard_stop(missing=missing, invalid=invalid, details=details)

    print("ROI READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
