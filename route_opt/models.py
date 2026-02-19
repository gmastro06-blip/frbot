from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import math


@dataclass
class Node:
    id: str
    x: float
    y: float
    z: Optional[float] = None


@dataclass
class Edge:
    from_id: str
    to_id: str
    travel_time_s: float


@dataclass
class Spawn:
    id: str
    node_id: str
    xp_per_kill: float
    kills_per_min: Optional[float] = None
    respawn_s_mean: float = 60.0
    respawn_s_std: Optional[float] = None
    variance_model: Optional[str] = None
    risk_score: float = 0.0
    density: Optional[int] = None


@dataclass
class PlayerConstraints:
    max_route_minutes: float = 60.0
    max_waypoints: Optional[int] = None
    min_waypoints: Optional[int] = None
    max_risk: Optional[float] = None
    allow_waiting: bool = True


@dataclass
class PlayerConfig:
    speed_tiles_per_s: float = 1.0
    dps_profile: Optional[Dict[str, Any]] = field(default_factory=dict)
    constraints: PlayerConstraints = field(default_factory=PlayerConstraints)


@dataclass
class Objective:
    maximize_xp_per_hour: bool = True
    travel_penalty: float = 0.0
    risk_penalty: float = 0.0


@dataclass
class MapData:
    nodes: List[Node]
    edges: List[Edge]


@dataclass
class Config:
    map: MapData
    spawns: List[Spawn]
    player: PlayerConfig
    objective: Objective
    seed: int = 42


def euclidean(a: Node, b: Node) -> float:
    dx = a.x - b.x
    dy = a.y - b.y
    dz = (a.z or 0.0) - (b.z or 0.0)
    return math.hypot(math.hypot(dx, dy), dz)
