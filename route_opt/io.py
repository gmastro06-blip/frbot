from __future__ import annotations

import json
from typing import Any, Dict
from .models import (
    Config,
    MapData,
    Node,
    Edge,
    Spawn,
    PlayerConfig,
    PlayerConstraints,
    Objective,
)

from .calibrator import median_mad, ewma


def load_config(path: str) -> Config:
    with open(path, 'r', encoding='utf-8') as f:
        j = json.load(f)

    # Minimal validation and required fields.
    if 'map' not in j or 'nodes' not in j['map']:
        raise ValueError('config.map.nodes required')
    nodes = [Node(**n) for n in j['map'].get('nodes', [])]
    edges = []
    for e in j['map'].get('edges', []):
        # normalize keys
        edges.append(Edge(from_id=e.get('from'), to_id=e.get('to'), travel_time_s=e.get('travel_time_s')))

    mapd = MapData(nodes=nodes, edges=edges)

    spawns = []
    for s in j.get('spawns', []):
        # require some fields
        required = ['id', 'node_id', 'xp_per_kill', 'respawn_s_mean']
        for r in required:
            if r not in s:
                raise ValueError(f'spawn {s.get("id","<unknown>")} missing {r}')
        spawns.append(Spawn(**s))

    p = j.get('player', {})
    constraints = PlayerConstraints(**p.get('constraints', {}))
    player = PlayerConfig(speed_tiles_per_s=p.get('speed_tiles_per_s', 1.0), dps_profile=p.get('dps_profile', {}), constraints=constraints)

    obj = j.get('objective', {})
    objective = Objective(maximize_xp_per_hour=obj.get('maximize_xp_per_hour', True), travel_penalty=obj.get('travel_penalty', 0.0), risk_penalty=obj.get('risk_penalty', 0.0))

    seed = j.get('seed', 42)

    # Optional: calibrate spawns from provided logs in config (list of respawn intervals per spawn)
    logs = j.get('logs')
    if logs and isinstance(logs, dict):
        # logs: {spawn_id: [intervals_s,...]}
        for s in spawns:
            if s.id in logs:
                vals = logs[s.id]
                try:
                    m, mad = median_mad(vals)
                    s.respawn_s_mean = float(m)
                    if s.respawn_s_std is None:
                        s.respawn_s_std = float(mad)
                except Exception:
                    pass

    return Config(map=mapd, spawns=spawns, player=player, objective=objective, seed=seed)
