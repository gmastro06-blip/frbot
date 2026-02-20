from __future__ import annotations

import json
from dataclasses import asdict
import re
from pathlib import Path
from typing import Any

from models import Script, Waypoint, WaypointType, now_iso


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

    created_at = data.get("created_at")
    if created_at is None:
        # tolerate missing created_at from legacy/converted files by using now
        created_at = now_iso()
    created_at = _as_str(created_at, field="waypoint.created_at")

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
    try:
        # Also write a human-friendly .in DSL next to the JSON
        in_path = p.with_suffix('.in')
        in_payload = script_to_in(script)
        in_path.write_text(in_payload, encoding='utf-8')
    except Exception:
        # Non-fatal: if writing the .in fails, don't prevent JSON save
        pass


def _fmt_list_words(words: list[str]) -> str:
    items = [f'"{str(w)}"' for w in list(words or [])]
    return "[" + ", ".join(items) + "]"


def script_to_in(script: Script) -> str:
    """Convert a Script into the simple `waypoints.in` DSL.

    The format is a sequence of lines such as:
      label <name>
      node (x, y, z)
      stand (x, y, z)
      ladder (x, y, z)
      rope (x, y, z)
      call talk_npc("list_words":["a", "b"])
      action travel_carlin
    """
    lines: list[str] = []
    name = (script.name or "").strip()
    if name:
        lines.append(f"label {name}")

    # Helper to format key/value options for call(...) style lines
    def _fmt_kv_opts(d: dict[str, Any]) -> str:
        parts: list[str] = []
        for k, v in d.items():
            if isinstance(v, list):
                parts.append(f'"{k}":{_fmt_list_words(v)}')
            else:
                parts.append(f'"{k}":"{str(v)}"')
        return ", ".join(parts)

    wps = list(script.waypoints or [])
    # Heuristic tuning: require a minimal tile distance to emit `stand`
    MIN_STAND_DISTANCE = 2
    for idx, wp in enumerate(wps):
        t = str(wp.type or "").strip()
        x = int(getattr(wp, 'x', 0) or 0)
        y = int(getattr(wp, 'y', 0) or 0)
        z = int(getattr(wp, 'z', 0) or 0)
        opts = dict(getattr(wp, 'options', {}) or {})

        # Decide whether this movement should be a 'stand' (stop) or 'node'
        is_movement = t in {"walk", "walk_ignore", "single_move", "move_up", "move_down"}
        next_is_action = False
        if idx + 1 < len(wps):
            next_t = str(wps[idx + 1].type or "").strip()
            next_is_action = next_t not in {"walk", "walk_ignore", "single_move", "move_up", "move_down"}

        if is_movement:
            # Also consider small movement distance as a stand (player paused)
            small_distance = False
            if idx + 1 < len(wps):
                try:
                    nx = int(getattr(wps[idx + 1], 'x', 0) or 0)
                    ny = int(getattr(wps[idx + 1], 'y', 0) or 0)
                    from math import hypot

                    dist = hypot(nx - x, ny - y)
                    small_distance = dist < float(MIN_STAND_DISTANCE)
                except Exception:
                    small_distance = False

            if bool(opts.get("stand", False)) or next_is_action or small_distance:
                lines.append(f"stand ({x}, {y}, {z})")
            else:
                lines.append(f"node ({x}, {y}, {z})")
            continue

        if t == "use_ladder":
            lines.append(f"ladder ({x}, {y}, {z})")
            continue

        if t == "rope":
            lines.append(f"rope ({x}, {y}, {z})")
            continue

        if t == "use_right_click":
            ak = str(opts.get('action_kind') or '').strip()
            interaction = str(opts.get('interaction') or '').strip()
            if name:
                lines.append(f"label {name}")

            # Include comments with metadata to preserve non-mapped fields
            meta = dict(script.metadata or {})
            if meta:
                lines.append(f"# metadata: {json.dumps(meta, ensure_ascii=False)}")
            # created_at of the script (best-effort)
            if script.waypoints:
                lines.append(f"# recorded_at: {script.waypoints[0].created_at}")
            lines.append(f"open_door ({x}, {y}, {z})")
            continue

        if t == "call_npc":
            # Prefer list_words, else sentence. Include other options if present.
            if 'list_words' in opts and isinstance(opts['list_words'], list):
                lw = _fmt_list_words(opts['list_words'])
                lines.append(f"call talk_npc(\"list_words\":{lw})")
            elif 'sentence' in opts:
                lines.append(f"call say(\"sentence\":\"{opts['sentence']}\")")
            else:
                extra = _fmt_kv_opts(opts) if opts else ""
                if extra:
                    lines.append(f"call talk_npc({extra})")
                else:
                    lines.append(f"call talk_npc(\"list_words\":[])")
            continue

        if t == "conditional_jump":
            # Emit as call conditional_jump_script_options("var_name":"x", "label_jump":"a", "label_skip":"b")
            extra = _fmt_kv_opts(opts)
            lines.append(f"call conditional_jump_script_options({extra})")
            continue

        if t in {"travel", "refill", "deposit", "trade"}:
            action_name = opts.get('action') or opts.get('name') or t
            # Support optional args like destination/time in opts
            extra = _fmt_kv_opts(opts) if opts else ""
            if extra:
                lines.append(f"action {action_name}({extra})")
            else:
                lines.append(f"action {action_name}")
            continue

        if t == "move_up":
            lines.append(f"action levitate_north_up")
            continue
        if t == "move_down":
            lines.append(f"action levitate_south_down")
            continue

        # Fallback: include type and coords
        extra = _fmt_kv_opts(opts) if opts else ""
        if extra:
            lines.append(f"action {t}({extra})")
        else:
            lines.append(f"action {t} ({x}, {y}, {z})")

    return "\n".join(lines) + "\n"


def _parse_kv_args(s: str) -> dict[str, Any]:
    # Try a tolerant JSON-parse approach first (handles nested lists and strings)
    out: dict[str, Any] = {}
    if not s or not s.strip():
        return out

    candidate = s.strip()
    # Normalize single quotes to double quotes for JSON compatibility
    candidate = candidate.replace("'", '"')
    # Quote unquoted keys: e.g. var_name: -> "var_name":
    candidate = re.sub(r'(?<!["\w])([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', candidate)
    try:
        obj = json.loads("{" + candidate + "}")
        if isinstance(obj, dict):
            return obj
    except Exception:
        # Fall back to permissive regex parsing
        pass

    # Regex fallback for lists and simple kv pairs
    list_re = re.compile(r'"(?P<k>[^"]+)"\s*:\s*\[(?P<v>[^\]]*)\]')
    for m in list_re.finditer(candidate):
        k = m.group('k')
        raw = m.group('v').strip()
        if not raw:
            out[k] = []
        else:
            parts = [p.strip().strip('"') for p in raw.split(',') if p.strip()]
            out[k] = parts

    kv_re = re.compile(r'"(?P<k>[^"]+)"\s*:\s*"(?P<v>[^"]*)"')
    for m in kv_re.finditer(candidate):
        k = m.group('k')
        v = m.group('v')
        if k in out:
            continue
        out[k] = v
    return out


def parse_in_to_script(path: str | Path) -> Script:
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    # Remove block comments and normalize line comments
    try:
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    except Exception:
        pass
    # Remove '//' comments
    text = re.sub(r'//.*$', '', text, flags=re.M)
    # Split into non-empty lines and strip inline comments starting with '#'
    raw_lines = text.splitlines()
    lines: list[str] = []
    for ln in raw_lines:
        s = ln.split('#', 1)[0].strip()
        if s:
            lines.append(s)

    name = "imported"
    waypoints: list[Waypoint] = []

    for ln in lines:
        if ln.startswith('#'):
            continue
        if ln.startswith('label '):
            name = ln.split(' ', 1)[1].strip()
            continue
        # node / stand / ladder / rope patterns
        m = re.match(r'^(?P<cmd>node|stand|ladder|rope)\s*\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*(?P<z>-?\d+)\s*\)$', ln)
        if m:
            cmd = m.group('cmd')
            x = int(m.group('x'))
            y = int(m.group('y'))
            z = int(m.group('z'))
            if cmd == 'node':
                wp = Waypoint(type=WaypointType.WALK.value, x=max(0, x), y=max(0, y), z=z, options={}, enabled=True, created_at=now_iso())
            else:
                opts = {}
                if cmd == 'stand':
                    opts['stand'] = True
                if cmd == 'ladder':
                    wp = Waypoint(type=WaypointType.USE_LADDER.value, x=max(0, x), y=max(0, y), z=z, options=opts, enabled=True, created_at=now_iso())
                    waypoints.append(wp)
                    continue
                if cmd == 'rope':
                    wp = Waypoint(type=WaypointType.ROPE.value, x=max(0, x), y=max(0, y), z=z, options=opts, enabled=True, created_at=now_iso())
                    waypoints.append(wp)
                    continue
                wp = Waypoint(type=WaypointType.WALK.value, x=max(0, x), y=max(0, y), z=z, options=opts, enabled=True, created_at=now_iso())
            waypoints.append(wp)
            continue

        # call ... patterns
        m = re.match(r'^call\s+(?P<name>[^\(\s]+)\s*(?:\((?P<args>.*)\))?$', ln)
        if m:
            callname = m.group('name').strip()
            args = m.group('args') or ''
            opts = _parse_kv_args(args)
            if callname == 'talk_npc' or callname == 'talk_npc':
                wp = Waypoint(type=WaypointType.CALL_NPC.value, x=0, y=0, z=0, options=opts, enabled=True, created_at=now_iso())
                waypoints.append(wp)
                continue
            if callname == 'say' or callname == 'call':
                # if 'sentence' present
                if 'sentence' in opts:
                    wp = Waypoint(type=WaypointType.CALL_NPC.value, x=0, y=0, z=0, options={'sentence': opts['sentence']}, enabled=True, created_at=now_iso())
                    waypoints.append(wp)
                    continue
            # conditional_jump_script_options
            if callname.startswith('conditional_jump'):
                wp = Waypoint(type='conditional_jump', x=0, y=0, z=0, options=opts, enabled=True, created_at=now_iso())
                waypoints.append(wp)
                continue

        # action ... patterns
        m = re.match(r'^action\s+(?P<act>[^\(\s]+)\s*(?:\((?P<args>.*)\))?$', ln)
        if m:
            act = m.group('act').strip()
            args = m.group('args') or ''
            opts = _parse_kv_args(args)
            # travel/refill/trade/etc
            wp = Waypoint(type=WaypointType.TRAVEL.value if act.startswith('travel') else act, x=0, y=0, z=0, options={**opts, 'action': act}, enabled=True, created_at=now_iso())
            waypoints.append(wp)
            continue

        # direct verbs like 'shovel (x,y,z)'
        m = re.match(r'^(?P<verb>\w+)\s*\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*(?P<z>-?\d+)\s*\)$', ln)
        if m:
            verb = m.group('verb')
            x = int(m.group('x'))
            y = int(m.group('y'))
            z = int(m.group('z'))
            if verb in {'shovel', 'pick', 'open_hole'}:
                wp = Waypoint(type=WaypointType.USE_RIGHT_CLICK.value, x=max(0, x), y=max(0, y), z=z, options={'action_kind': verb}, enabled=True, created_at=now_iso())
                waypoints.append(wp)
                continue
            if verb in {'open_door'}:
                wp = Waypoint(type=WaypointType.OPEN_DOOR.value, x=max(0, x), y=max(0, y), z=z, options={'action_kind': verb}, enabled=True, created_at=now_iso())
                waypoints.append(wp)
                continue

    return Script(name=name or "imported", enabled=True, run_to_target=False, waypoints=waypoints, metadata={})


def load_script(path: str | Path) -> Script:
    p = Path(path)
    p = Path(path)
    # Support importing legacy .in DSL files directly
    if p.suffix.lower() == '.in':
        try:
            return parse_in_to_script(p)
        except Exception as exc:
            raise SchemaError(f"Failed to parse .in file: {exc}") from exc

    try:
        raw = p.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise SchemaError(f"Failed to read file: {exc}") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise SchemaError(f"Invalid JSON: {exc}") from exc

    script = script_from_dict(data)

    # Sanitize legacy or hand-edited scripts: clamp negative coordinates
    # to zero and ensure each waypoint has a created_at timestamp.
    try:
        for wp in script.waypoints:
            try:
                wp.x = max(0, int(wp.x))
            except Exception:
                wp.x = 0
            try:
                wp.y = max(0, int(wp.y))
            except Exception:
                wp.y = 0
            if not getattr(wp, "created_at", None):
                wp.created_at = now_iso()
    except Exception:
        # If sanitization fails for any reason, return the parsed script
        # so the caller can decide how to proceed.
        pass

    return script
