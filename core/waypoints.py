from __future__ import annotations

from typing import Iterable, Iterator, Sequence

from contracts.errors import ContractViolation
from contracts.models import Coordinate, Waypoint


def validate_waypoints(waypoints: Sequence[Waypoint]) -> None:
    if waypoints is None:
        raise ContractViolation('waypoints must not be None')

    # Explicit: empty waypoint lists are allowed but must be handled by runtime.
    for idx, wp in enumerate(waypoints):
        if not isinstance(wp, Waypoint):
            raise ContractViolation(f'waypoints[{idx}] is not a Waypoint')


def iter_waypoint_coords(waypoints: Iterable[Waypoint]) -> Iterator[Coordinate]:
    for wp in waypoints:
        yield wp.coordinate
