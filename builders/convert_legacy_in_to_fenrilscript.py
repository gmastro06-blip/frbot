import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


Coordinate = Tuple[int, int, int]


@dataclass
class ParseIssue:
    line_no: int
    raw: str
    reason: str


_COORD_RE = re.compile(
    r"\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*(?P<z>-?\d+)\s*\)"
)


def _parse_coord(text: str) -> Optional[Coordinate]:
    match = _COORD_RE.search(text)
    if not match:
        return None
    return (int(match.group("x")), int(match.group("y")), int(match.group("z")))


def _make_waypoint(
    *,
    label: str,
    waypoint_type: str,
    coordinate: Coordinate,
    options: Optional[Dict[str, Any]] = None,
    ignore: bool = False,
    passinho: bool = False,
) -> Dict[str, Any]:
    return {
        "label": label,
        "type": waypoint_type,
        "coordinate": [coordinate[0], coordinate[1], coordinate[2]],
        "options": options or {},
        "ignore": ignore,
        "passinho": passinho,
    }


def convert_waypoints_in(lines: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[ParseIssue]]:
    """Convert legacy `waypoints.in` (line-based) into Fenril Draken `.fenrilscript` format.

    This converter is intentionally conservative:
    - Preserves the original line order.
    - Does not infer gameplay semantics beyond direct structural mapping.
    - Records anything not directly representable as a Fenril waypoint type.

    Output waypoints always match the canonical Draken structure:
    {label,type,coordinate,options,ignore,passinho}
    """

    waypoints: List[Dict[str, Any]] = []
    issues: List[ParseIssue] = []

    last_coordinate: Optional[Coordinate] = None
    pending_labels: List[str] = []

    def flush_pending_labels_as_noop_waypoints(line_no: int) -> None:
        nonlocal pending_labels
        if not pending_labels:
            return
        if last_coordinate is None:
            # We cannot place labels without a coordinate; keep them pending.
            return
        # Represent each label as a no-op walk at the current coordinate.
        # This preserves label placement/order without inventing new coordinates.
        while pending_labels:
            label = pending_labels.pop(0)
            waypoints.append(
                _make_waypoint(
                    label=label,
                    waypoint_type="walk",
                    coordinate=last_coordinate,
                    options={"legacy": {"kind": "label"}},
                    # ignore=True reduces unintended combat/loot at a no-op marker.
                    ignore=True,
                    passinho=False,
                )
            )

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        # `label <name>`
        if line.lower().startswith("label "):
            label = line[6:].strip()
            if not label:
                issues.append(ParseIssue(idx, raw_line.rstrip("\n"), "Empty label"))
                continue
            # If we already know the current position, materialize as a no-op marker.
            if last_coordinate is not None:
                pending_labels.append(label)
                flush_pending_labels_as_noop_waypoints(idx)
            else:
                # Defer until we have a coordinate.
                pending_labels.append(label)
            continue

        # `action <name>`
        if line.lower().startswith("action "):
            action = line[7:].strip()
            if not action:
                issues.append(ParseIssue(idx, raw_line.rstrip("\n"), "Empty action"))
                continue
            if last_coordinate is None:
                issues.append(ParseIssue(idx, raw_line.rstrip("\n"), "Action before any coordinate"))
                continue
            # Fenril doesn't have a generic 'action' waypoint type.
            # Preserve the step as a no-op walk marker carrying the legacy action payload.
            waypoints.append(
                _make_waypoint(
                    label=f"action:{action}",
                    waypoint_type="walk",
                    coordinate=last_coordinate,
                    options={"legacy": {"kind": "action", "action": action, "raw": line}},
                    ignore=True,
                    passinho=False,
                )
            )
            continue

        # `node/stand/walk/door/rope/shovel (<x,y,z>)`
        directive = line.split(" ", 1)[0].lower()
        coord = _parse_coord(line)
        if coord is None:
            issues.append(ParseIssue(idx, raw_line.rstrip("\n"), "Could not parse coordinate"))
            continue

        # Apply pending labels to a marker at the prior coordinate (if any);
        # if we still don't have a prior coordinate, we'll attach to the first coordinate via a marker.
        if pending_labels and last_coordinate is None:
            # Place all pending labels at the first seen coordinate.
            while pending_labels:
                label = pending_labels.pop(0)
                waypoints.append(
                    _make_waypoint(
                        label=label,
                        waypoint_type="walk",
                        coordinate=coord,
                        options={"legacy": {"kind": "label"}},
                        ignore=True,
                        passinho=False,
                    )
                )

        # Convert directives
        if directive in {"node", "stand", "walk"}:
            # All become a `walk` waypoint; we keep the original directive in options.
            waypoints.append(
                _make_waypoint(
                    label="",
                    waypoint_type="walk",
                    coordinate=coord,
                    options={"legacy": {"kind": directive, "raw": line}},
                    ignore=False,
                    passinho=False,
                )
            )
            last_coordinate = coord
            continue

        if directive == "door":
            waypoints.append(
                _make_waypoint(
                    label="",
                    waypoint_type="openDoor",
                    coordinate=coord,
                    options={"legacy": {"kind": "door", "raw": line}},
                    ignore=False,
                    passinho=False,
                )
            )
            last_coordinate = coord
            continue

        if directive == "rope":
            waypoints.append(
                _make_waypoint(
                    label="",
                    waypoint_type="useRope",
                    coordinate=coord,
                    options={"legacy": {"kind": "rope", "raw": line}},
                    ignore=False,
                    passinho=False,
                )
            )
            last_coordinate = coord
            continue

        if directive == "shovel":
            waypoints.append(
                _make_waypoint(
                    label="",
                    waypoint_type="useShovel",
                    coordinate=coord,
                    options={"legacy": {"kind": "shovel", "raw": line}},
                    ignore=False,
                    passinho=False,
                )
            )
            last_coordinate = coord
            continue

        issues.append(ParseIssue(idx, raw_line.rstrip("\n"), f"Unknown directive '{directive}'"))

    # If any trailing labels remain, materialize them at last known coordinate.
    flush_pending_labels_as_noop_waypoints(len(lines))

    return waypoints, issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    args = parser.parse_args()

    input_path = Path(args.input_path)
    output_path = Path(args.output_path)

    text = input_path.read_text(encoding="utf-8", errors="replace")
    waypoints, issues = convert_waypoints_in(text.splitlines())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(waypoints, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print(f"Waypoints generated: {len(waypoints)}")
    if issues:
        print(f"Issues: {len(issues)}")
        for issue in issues:
            print(f"- L{issue.line_no}: {issue.reason}: {issue.raw}")
    else:
        print("Issues: 0")

    # Non-zero exit if we had issues, so CI/automation can detect incomplete mapping.
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
