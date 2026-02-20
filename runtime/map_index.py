from __future__ import annotations

from typing import Optional, Tuple
import logging
import os


LOG = logging.getLogger(__name__)


def create_default_map_index(cache_dir: Optional[str] = None):
    """Factory: try to return a tibia-backed MapIndex (auto-download),
    otherwise fall back to permissive `MapIndex`.

    Honor the env var `FRBOT_DISABLE_MAP_DOWNLOAD`. If set to a truthy value
    ("1", "true", "yes"), the factory will return a permissive `MapIndex`
    and avoid attempting any downloads. This is useful for CI or offline
    environments.
    """
    disable = os.environ.get("FRBOT_DISABLE_MAP_DOWNLOAD", "").lower()
    # allow overriding cache dir from env
    env_cache = os.environ.get("FRBOT_MAP_CACHE_DIR")
    if env_cache:
        cache_dir = env_cache
    if disable in {"1", "true", "yes"}:
        LOG.debug("FRBOT_DISABLE_MAP_DOWNLOAD is set -> skipping TibiaMapIndex")
        return MapIndex()

    try:
        # import lazily to avoid pulling heavy deps during tests if not needed
        from runtime.tibia_map_index import TibiaMapIndex

        return TibiaMapIndex(cache_dir=cache_dir)
    except Exception:
        LOG.debug("TibiaMapIndex unavailable, falling back to permissive MapIndex")
        return MapIndex()


class MapIndex:
    """Abstract map index interface.

    Implementations can provide real walkability and snapping using external
    map data (e.g. tibia-map-data). Default methods are permissive so that
    RouteRecordingSession works without a MapIndex instance.
    """

    def is_walkable(self, x: int, y: int, z: int) -> bool:
        return True

    def snap_tile(self, x: int, y: int, z: int) -> Tuple[int, int, int]:
        return (int(x), int(y), int(z))

    def find_nearest_walkable(self, x: int, y: int, z: int, max_radius: int = 2) -> Optional[Tuple[int, int, int]]:
        return None
