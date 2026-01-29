from __future__ import annotations

try:
	from nptyping import NDArray
	_HAS_NPTYPING = True
except Exception:  # pragma: no cover
	NDArray = None  # type: ignore
	_HAS_NPTYPING = False
from typing import Any, List, Literal, Tuple, TypeAlias, Union


BBox: TypeAlias = Tuple[int, int, int, int]
Coordinate: TypeAlias = Tuple[int, int, int]
CoordinateList: TypeAlias = List[Coordinate]

CreatureCategory: TypeAlias = str
CreatureCategoryOrUnknown: TypeAlias = Union[CreatureCategory, Literal['Unknown']]

Direction: TypeAlias = Literal['up', 'down', 'left', 'right']

# Images
if _HAS_NPTYPING:
	GrayImage: TypeAlias = NDArray[Any, Any]
	GrayVector: TypeAlias = NDArray[Any, Any]
else:  # pragma: no cover
	GrayImage: TypeAlias = Any
	GrayVector: TypeAlias = Any
GrayPixel: TypeAlias = int

Slot: TypeAlias = Tuple[int, int]
SlotWidth: TypeAlias = Literal[32, 64]

Waypoint: TypeAlias = Any
WaypointList: TypeAlias = List[Waypoint]

XYCoordinate: TypeAlias = Tuple[int, int]
XYCoordinateList: TypeAlias = List[XYCoordinate]