from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal, TypeAlias

from contracts.engine import EngineInput, IntentMove
from contracts.runtime import Tile


Direction: TypeAlias = Literal['up', 'down', 'left', 'right']


@dataclass(frozen=True, slots=True)
class RuleResult:
	intent: Optional[IntentMove] = None
	abort_reason: Optional[str] = None

def _step_towards(from_tile: Tile, waypoint: Tile) -> Optional[tuple[Direction, Tile]]:
	if waypoint.z != from_tile.z:
		return None
	dx = waypoint.x - from_tile.x
	dy = waypoint.y - from_tile.y
	if dx == 0 and dy == 0:
		return None
	# Deterministic axis choice: X then Y.
	if dx != 0:
		direction: Direction = 'right' if dx > 0 else 'left'
		return (direction, Tile(x=from_tile.x + (1 if dx > 0 else -1), y=from_tile.y, z=from_tile.z))
	direction = 'down' if dy > 0 else 'up'
	return (direction, Tile(x=from_tile.x, y=from_tile.y + (1 if dy > 0 else -1), z=from_tile.z))



def select_cavebot_intent(inp: EngineInput, *, enable_cavebot: bool) -> RuleResult:
	if not enable_cavebot:
		return RuleResult()

	waypoint = inp.target_tile
	if waypoint is None:
		return RuleResult()

	from_tile = inp.current_position
	if (from_tile.x, from_tile.y, from_tile.z) == (waypoint.x, waypoint.y, waypoint.z):
		return RuleResult()

	step = _step_towards(from_tile, waypoint)
	if step is None:
		return RuleResult(abort_reason='cavebot waypoint requires z-level change (unsupported)')

	direction, to_tile = step
	return RuleResult(intent=IntentMove(direction=direction, reason='cavebot'))
