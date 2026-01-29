import numpy as np
from typing import Any, TypeAlias

try:
	from nptyping import NDArray
	_HAS_NPTYPING = True
except Exception:  # pragma: no cover
	NDArray = None  # type: ignore
	_HAS_NPTYPING = False


Creature = np.dtype([('name', np.str_, 64), ('isBeingAttacked', np.bool_)])
# TODO: fix it
CreatureList: TypeAlias = NDArray[Any, Any] if _HAS_NPTYPING else Any
