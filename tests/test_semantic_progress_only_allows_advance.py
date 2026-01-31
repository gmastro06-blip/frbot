from __future__ import annotations

from contracts.runtime import Tile
from runtime.minimap_semantics import semantic_progress_ok


def test_semantic_progress_only_allows_advance() -> None:
    wp = Tile(x=2, y=0, z=7)

    # No movement never counts.
    assert not semantic_progress_ok(direction='right', before=Tile(x=0, y=0, z=7), after=Tile(x=0, y=0, z=7), waypoint=wp)

    # Movement opposite to expected direction never counts.
    assert not semantic_progress_ok(direction='right', before=Tile(x=0, y=0, z=7), after=Tile(x=-1, y=0, z=7), waypoint=wp)

    # Movement in expected direction must also reduce distance.
    assert semantic_progress_ok(direction='right', before=Tile(x=0, y=0, z=7), after=Tile(x=1, y=0, z=7), waypoint=wp)
