from __future__ import annotations

from dataclasses import is_dataclass, asdict
from typing import Any

from runtime.error_policy import should_reraise


def serialize_for_trace(obj: Any) -> Any:
    """Recursively coerce objects to JSON-serializable primitives.

    - Keep ints/floats/str/bool/None as-is.
    - Convert dataclasses to dicts.
    - Convert bytes to a hex string.
    - Convert iterables (list/tuple/set) to lists.
    - Convert unknown objects to their string representation.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        try:
            return obj.hex()
        except Exception:
            if should_reraise():
                raise
            return str(obj)
    # is_dataclass can be True for both instances and classes; ensure instance
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return serialize_for_trace(asdict(obj))
        except Exception:
            # Fallback to attribute inspection
            if should_reraise():
                raise
            pass
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            try:
                key = str(k)
            except Exception:
                key = repr(k)
            out[key] = serialize_for_trace(v)
        return out
    if isinstance(obj, (list, tuple, set)):
        return [serialize_for_trace(v) for v in obj]

    # Fallback: try to extract useful attrs for common objects
    try:
        # objects with __dict__
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict) and d:
            return serialize_for_trace(d)
    except Exception:
        if should_reraise():
            raise
        pass

    try:
        return str(obj)
    except Exception:
        if should_reraise():
            raise
        return repr(obj)
