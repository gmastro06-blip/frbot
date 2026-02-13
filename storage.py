from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from models import Script, Waypoint, WaypointType


class SchemaError(Exception):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


def _as_int(v: Any, *, field: str) -> int:
    _require(isinstance(v, int) and not isinstance(v, bool), f"{field}: expected int")
    return int(v)


def _as_bool(v: Any, *, field: str) -> bool:
    _require(isinstance(v, bool), f"{field}: expected bool")
    return bool(v)


def _as_str(v: Any, *, field: str) -> str:
    _require(isinstance(v, str), f"{field}: expected str")
    return str(v)


def _as_dict(v: Any, *, field: str) -> dict[str, Any]:
    _require(isinstance(v, dict), f"{field}: expected object")
    return dict(v)


def _as_list(v: Any, *, field: str) -> list[Any]:
    _require(isinstance(v, list), f"{field}: expected array")
    return list(v)


def waypoint_to_dict(wp: Waypoint) -> dict[str, Any]:
    return {
        "type": str(wp.type),
        "x": int(wp.x),
        "y": int(wp.y),
        "z": int(wp.z),
        "options": dict(wp.options or {}),
        "enabled": bool(wp.enabled),
        "created_at": str(wp.created_at),
    }


def script_to_dict(script: Script) -> dict[str, Any]:
    return {
        "name": str(script.name),
        "enabled": bool(script.enabled),
        "run_to_target": bool(script.run_to_target),
        "metadata": dict(script.metadata or {}),
        "waypoints": [waypoint_to_dict(wp) for wp in list(script.waypoints or [])],
    }


def canonical_json(script: Script) -> str:
    return json.dumps(script_to_dict(script), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def waypoint_from_dict(data: Any) -> Waypoint:
    _require(isinstance(data, dict), "waypoint: expected object")

    t = _as_str(data.get("type"), field="waypoint.type").strip()
    _require(t in set(WaypointType.values()), f"waypoint.type: invalid value '{t}'")

    x = _as_int(data.get("x"), field="waypoint.x")
    y = _as_int(data.get("y"), field="waypoint.y")
    z = _as_int(data.get("z"), field="waypoint.z")

    options = data.get("options", {})
    if options is None:
        options = {}
    options = _as_dict(options, field="waypoint.options")

    enabled = data.get("enabled", True)
    if enabled is None:
        enabled = True
    enabled = _as_bool(enabled, field="waypoint.enabled")

    created_at = data.get("created_at", "")
    if created_at is None:
        created_at = ""
    created_at = _as_str(created_at, field="waypoint.created_at")
    _require(created_at.strip() != "", "waypoint.created_at: required")

    return Waypoint(type=t, x=x, y=y, z=z, options=options, enabled=enabled, created_at=created_at)


def script_from_dict(data: Any) -> Script:
    _require(isinstance(data, dict), "script: expected object")

    name = _as_str(data.get("name"), field="script.name").strip()
    _require(name != "", "script.name: required")

    enabled = _as_bool(data.get("enabled", True), field="script.enabled")
    run_to_target = _as_bool(data.get("run_to_target", False), field="script.run_to_target")

    metadata = data.get("metadata", {})
    if metadata is None:
        metadata = {}
    metadata = _as_dict(metadata, field="script.metadata")

    waypoints_node = _as_list(data.get("waypoints", []), field="script.waypoints")
    waypoints = [waypoint_from_dict(wp) for wp in waypoints_node]

    return Script(
        name=name,
        enabled=enabled,
        run_to_target=run_to_target,
        metadata=metadata,
        waypoints=waypoints,
    )


def save_script(path: str | Path, script: Script) -> None:
    p = Path(path)
    payload = canonical_json(script)
    p.write_text(payload, encoding="utf-8")


def load_script(path: str | Path) -> Script:
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise SchemaError(f"Failed to read file: {exc}") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise SchemaError(f"Invalid JSON: {exc}") from exc

    return script_from_dict(data)
