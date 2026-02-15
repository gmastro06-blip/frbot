from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from contracts.errors import PreflightFailed
from contracts.runtime import Tile


@dataclass(frozen=True, slots=True)
class LoadedBotConfig:
    waypoints: tuple[Tile, ...]


def _iter_waypoint_nodes(data: Any) -> Iterable[dict[str, Any]]:
    # Supports the legacy Waypoints/file.json structure used in this repo.
    if not isinstance(data, dict):
        return

    default_node = data.get('_default')
    if not isinstance(default_node, dict):
        return

    # Find first enabled profile under _default.
    for _profile_key, profile in default_node.items():
        if not isinstance(profile, dict):
            continue
        if profile.get('enabled') is not True:
            continue

        cfg = profile.get('config')
        if not isinstance(cfg, dict):
            continue
        cave = cfg.get('ng_cave')
        if not isinstance(cave, dict):
            continue
        if cave.get('enabled') is not True:
            continue

        wps = cave.get('waypoints')
        if not isinstance(wps, dict):
            continue
        items = wps.get('items')
        if not isinstance(items, list):
            continue

        for item in items:
            if isinstance(item, dict):
                yield item

        return


def load_bot_config(path_raw: str) -> LoadedBotConfig:
    path_raw = path_raw.strip()
    if not path_raw:
        return LoadedBotConfig(waypoints=())

    path = Path(path_raw)
    if not path.exists():
        raise PreflightFailed(f'bot_config_path does not exist: {path_raw!r}')

    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise PreflightFailed(f'failed to read bot_config_path: {type(exc).__name__}: {exc}') from exc

    wps: list[Tile] = []
    for node in _iter_waypoint_nodes(data):
        # Only use non-ignored walk nodes with coordinates.
        if node.get('ignore') is True:
            continue
        coord = node.get('coordinate')
        if not (isinstance(coord, list) and len(coord) == 3):
            continue
        try:
            x = int(coord[0])
            y = int(coord[1])
            z = int(coord[2])
        except Exception:
            continue
        wps.append(Tile(x=x, y=y, z=z, walkable=True))

    return LoadedBotConfig(waypoints=tuple(wps))


def first_waypoint_tile(cfg: LoadedBotConfig) -> Optional[tuple[int, int, int]]:
    if not cfg.waypoints:
        return None
    wp = cfg.waypoints[0]
    return (wp.x, wp.y, wp.z)
