from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models import Script, Waypoint, WaypointType, now_iso
from storage import save_script


_COORD_RE = re.compile(r"\(([^)]*)\)")
_CALL_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)$")
_LEGACY_ANCHORED_ACTIONS = {
    "buy_ticket",
    "walk_keys",
    "walk_mouse",
    "levitate_north_up",
    "levitate_south_down",
    "check_train",
    "angus",
    "honey",
    "jug",
    "venorean_spice",
    "royal_satin",
    "angus2",
    "wyrdin",
    "rum",
    "karith",
}


@dataclass(slots=True)
class ImportStats:
    total_lines: int = 0
    parsed_waypoints: int = 0
    ignored_empty_or_comments: int = 0
    ignored_unknown: int = 0


@dataclass(slots=True)
class ImportWarning:
    line: int
    reason: str
    content: str


@dataclass(slots=True)
class ImportResult:
    script: Script
    stats: ImportStats
    warnings: list[ImportWarning]


def _parse_coords(payload: str) -> tuple[int, int, int]:
    m = _COORD_RE.search(payload)
    if m is None:
        raise ValueError("missing_coordinates")
    raw = [p.strip() for p in m.group(1).split(",")]
    if len(raw) != 3:
        raise ValueError("invalid_coordinate_arity")
    try:
        x = int(raw[0])
        y = int(raw[1])
        z = int(raw[2])
    except Exception as exc:
        raise ValueError("invalid_coordinate_value") from exc
    return (x, y, z)


def _append_wp(waypoints: list[Waypoint], wp_type: str, x: int, y: int, z: int, options: dict[str, Any] | None = None) -> None:
    waypoints.append(
        Waypoint(
            type=str(wp_type),
            x=int(x),
            y=int(y),
            z=int(z),
            options=dict(options or {}),
            enabled=True,
            created_at=now_iso(),
        )
    )


def _append_anchored_marker(
    *,
    waypoints: list[Waypoint],
    last_tile: tuple[int, int, int] | None,
    stats: ImportStats,
    warnings: list[ImportWarning],
    pending_markers: list[tuple[str, dict[str, Any]]],
    line_no: int,
    line_raw: str,
    options: dict[str, Any],
    wp_type: str = '',
) -> bool:
    resolved_type = str(wp_type or WaypointType.WALK_IGNORE.value)
    if last_tile is None:
        pending_markers.append((resolved_type, dict(options)))
        return True

    _append_wp(
        waypoints,
        resolved_type,
        last_tile[0],
        last_tile[1],
        last_tile[2],
        options=options,
    )
    stats.parsed_waypoints += 1
    return True


def _flush_pending_markers(
    *,
    waypoints: list[Waypoint],
    stats: ImportStats,
    pending_markers: list[tuple[str, dict[str, Any]]],
    anchor_tile: tuple[int, int, int],
) -> None:
    if not pending_markers:
        return
    x, y, z = anchor_tile
    for ptype, opts in pending_markers:
        _append_wp(
            waypoints,
            str(ptype or WaypointType.WALK_IGNORE.value),
            x,
            y,
            z,
            options=dict(opts),
        )
        stats.parsed_waypoints += 1
    pending_markers.clear()


def import_waypoints_in(*, input_path: Path, script_name: str, default_z: int = 7) -> ImportResult:
    text = input_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    stats = ImportStats(total_lines=len(lines))
    warnings: list[ImportWarning] = []
    waypoints: list[Waypoint] = []
    labels: dict[str, int] = {}
    pending_markers: list[tuple[str, dict[str, Any]]] = []

    last_tile: tuple[int, int, int] | None = None

    for idx, raw in enumerate(lines, start=1):
        line = str(raw).strip()
        if not line or line.startswith("#") or line.startswith(";"):
            stats.ignored_empty_or_comments += 1
            continue

        cmd, _, tail = line.partition(" ")
        command = cmd.strip().lower()
        rest = tail.strip()

        if command == "label":
            label_name = rest.strip()
            if label_name:
                labels[label_name] = len(waypoints)
            else:
                warnings.append(ImportWarning(line=idx, reason="empty_label", content=line))
            continue

        if command == "load":
            _append_anchored_marker(
                waypoints=waypoints,
                last_tile=last_tile,
                stats=stats,
                warnings=warnings,
                pending_markers=pending_markers,
                line_no=idx,
                line_raw=line,
                options={"action_kind": "load", "legacy_include": rest},
            )
            continue

        if command in {"node", "stand", "walk"}:
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            if last_tile is None:
                _flush_pending_markers(
                    waypoints=waypoints,
                    stats=stats,
                    pending_markers=pending_markers,
                    anchor_tile=(x, y, z),
                )
            _append_wp(waypoints, WaypointType.WALK.value, x, y, z)
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        if command == "door":
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            _append_wp(
                waypoints,
                WaypointType.OPEN_DOOR.value,
                x,
                y,
                z,
                options={"action_kind": "door"},
            )
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        if command == "ladder":
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            _append_wp(
                waypoints,
                WaypointType.USE_LADDER.value,
                x,
                y,
                z,
                options={"action_kind": "ladder"},
            )
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        if command == "rope":
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            _append_wp(
                waypoints,
                WaypointType.ROPE.value,
                x,
                y,
                z,
                options={"action_kind": "rope"},
            )
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        if command in {"shovel", "pick"}:
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            _append_wp(
                waypoints,
                WaypointType.USE_RIGHT_CLICK.value,
                x,
                y,
                z,
                options={"interaction": "open_hole", "action_kind": command},
            )
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        if command == "action":
            action_name = rest.split(" ", 1)[0].strip().lower()
            if action_name.startswith("travel_") and last_tile is not None:
                _append_wp(
                    waypoints,
                    WaypointType.TRAVEL.value,
                    last_tile[0],
                    last_tile[1],
                    last_tile[2],
                    options={"action_kind": action_name},
                )
                stats.parsed_waypoints += 1
                continue

            if action_name in {"check_time", "end"} and last_tile is not None:
                _append_wp(
                    waypoints,
                    WaypointType.WALK_IGNORE.value,
                    last_tile[0],
                    last_tile[1],
                    last_tile[2],
                    options={"action_kind": action_name, "legacy_action": True},
                )
                stats.parsed_waypoints += 1
                continue

            if action_name == "wait":
                _append_anchored_marker(
                    waypoints=waypoints,
                    last_tile=last_tile,
                    stats=stats,
                    warnings=warnings,
                    pending_markers=pending_markers,
                    line_no=idx,
                    line_raw=line,
                    options={"action_kind": action_name, "legacy_action": True},
                )
                continue

            if action_name in _LEGACY_ANCHORED_ACTIONS:
                _append_anchored_marker(
                    waypoints=waypoints,
                    last_tile=last_tile,
                    stats=stats,
                    warnings=warnings,
                    pending_markers=pending_markers,
                    line_no=idx,
                    line_raw=line,
                    options={"action_kind": action_name, "legacy_action": True},
                )
                continue

            _append_anchored_marker(
                waypoints=waypoints,
                last_tile=last_tile,
                stats=stats,
                warnings=warnings,
                pending_markers=pending_markers,
                line_no=idx,
                line_raw=line,
                options={"action_kind": action_name or "action", "legacy_action": True, "legacy_raw": line},
            )
            continue

        if command == "call":
            call_name = ""
            call_payload = rest
            m = _CALL_RE.match(rest)
            if m is not None:
                call_name = str(m.group(1) or "").strip().lower()
                call_payload = str(m.group(2) or "").strip()

            if call_name in {"talk_npc", "say"}:
                _append_anchored_marker(
                    waypoints=waypoints,
                    last_tile=last_tile,
                    stats=stats,
                    warnings=warnings,
                    pending_markers=pending_markers,
                    line_no=idx,
                    line_raw=line,
                    wp_type=WaypointType.CALL_NPC.value,
                    options={
                        "action_kind": "call_npc",
                        "call": call_name,
                        "payload": call_payload,
                    },
                )
                continue

            if call_name.startswith("conditional_jump") or call_name == "check_kill_count":
                _append_anchored_marker(
                    waypoints=waypoints,
                    last_tile=last_tile,
                    stats=stats,
                    warnings=warnings,
                    pending_markers=pending_markers,
                    line_no=idx,
                    line_raw=line,
                    wp_type=WaypointType.CONDITIONAL_JUMP.value,
                    options={
                        "action_kind": "conditional_jump",
                        "call": call_name,
                        "payload": call_payload,
                    },
                )
                continue

            _append_anchored_marker(
                waypoints=waypoints,
                last_tile=last_tile,
                stats=stats,
                warnings=warnings,
                pending_markers=pending_markers,
                line_no=idx,
                line_raw=line,
                options={
                    "action_kind": "call",
                    "legacy_call": call_name or "unknown_call",
                    "legacy_payload": call_payload,
                },
            )
            continue

        if command == "use":
            try:
                x, y, z = _parse_coords(rest)
            except ValueError as exc:
                warnings.append(ImportWarning(line=idx, reason=str(exc), content=line))
                stats.ignored_unknown += 1
                continue
            _append_wp(
                waypoints,
                WaypointType.USE_RIGHT_CLICK.value,
                x,
                y,
                z,
                options={"action_kind": "use", "legacy_command": "use"},
            )
            last_tile = (x, y, z)
            stats.parsed_waypoints += 1
            continue

        _append_anchored_marker(
            waypoints=waypoints,
            last_tile=last_tile,
            stats=stats,
            warnings=warnings,
            pending_markers=pending_markers,
            line_no=idx,
            line_raw=line,
            options={
                "action_kind": "command",
                "legacy_command": command,
                "legacy_payload": rest,
            },
        )

    if pending_markers:
        _flush_pending_markers(
            waypoints=waypoints,
            stats=stats,
            pending_markers=pending_markers,
            anchor_tile=(0, 0, int(default_z)),
        )

    script = Script(
        name=str(script_name),
        enabled=True,
        run_to_target=False,
        waypoints=waypoints,
        metadata={
            "import": {
                "source": str(input_path),
                "format": "legacy_waypoints_in",
                "default_z": int(default_z),
                "labels": labels,
                "stats": {
                    "total_lines": int(stats.total_lines),
                    "parsed_waypoints": int(stats.parsed_waypoints),
                    "ignored_empty_or_comments": int(stats.ignored_empty_or_comments),
                    "ignored_unknown": int(stats.ignored_unknown),
                },
                "warnings": [
                    {"line": int(w.line), "reason": str(w.reason), "content": str(w.content)}
                    for w in warnings
                ],
            }
        },
    )
    return ImportResult(script=script, stats=stats, warnings=warnings)


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Import legacy waypoints .in into FRBot script JSON")
    ap.add_argument("--input", required=True, help="Path to legacy waypoints .in file")
    ap.add_argument("--output", required=True, help="Output path for FRBot JSON script")
    ap.add_argument("--name", default="imported_legacy_route", help="Name for output script")
    ap.add_argument("--default-z", type=int, default=7, help="Fallback z value metadata")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    input_path = Path(str(args.input)).expanduser().resolve()
    output_path = Path(str(args.output)).expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        raise SystemExit(f"input_not_found:{input_path}")

    result = import_waypoints_in(
        input_path=input_path,
        script_name=str(args.name),
        default_z=int(args.default_z),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_script(output_path, result.script)

    print(f"IMPORTED_WAYPOINTS:{result.stats.parsed_waypoints}")
    print(f"IGNORED_LINES:{result.stats.ignored_unknown}")
    print(f"OUTPUT:{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
