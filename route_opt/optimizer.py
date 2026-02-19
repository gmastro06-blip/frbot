from __future__ import annotations

from typing import List, Tuple, Dict, Any
from .models import Spawn, Config
from .simulator import Simulator
import random


def greedy_initial(spawns: List[Spawn], config: Config, max_waypoints: int | None) -> List[Spawn]:
    # Compute profit density = xp per expected dwell second
    scoring = []
    for s in spawns:
        dwell = 60.0 / (s.kills_per_min or 1.0)
        density = (s.xp_per_kill * (s.kills_per_min or 1.0) / 60.0) / (dwell + 1.0)
        scoring.append((density, s))
    scoring.sort(key=lambda x: x[0], reverse=True)
    k = max_waypoints or len(spawns)
    return [s for _, s in scoring[:k]]


def two_opt(route: List[Spawn]) -> List[Spawn]:
    # simple 2-opt: try a few random swaps
    if len(route) < 4:
        return route
    r = route[:]
    i = random.randrange(0, len(r) - 2)
    j = random.randrange(i + 1, len(r))
    r[i:j] = reversed(r[i:j])
    return r


def optimize(spawns: List[Spawn], config: Config, mc_iters: int = 100, seed: int | None = None) -> Tuple[List[Spawn], Dict[str, Any]]:
    rand = random.Random(seed or config.seed)
    max_waypoints = config.player.constraints.max_waypoints or len(spawns)
    initial = greedy_initial(spawns, config, max_waypoints)

    sim = Simulator(config, seed=seed)
    base_stats = sim.simulate_route(initial, route_cycle_seconds=0.0, mc_iters=mc_iters)
    best = initial[:]
    best_score = base_stats['xp_per_hour_mean']

    # improve with hillclimb + random restarts
    for temp in [0.5, 0.2, 0.1]:
        for _ in range(20):
            cand = two_opt(best)
            stats = sim.simulate_route(cand, route_cycle_seconds=0.0, mc_iters=max(10, mc_iters // 10))
            score = stats['xp_per_hour_mean']
            if score > best_score:
                best = cand
                best_score = score

    debug = {
        'baseline': base_stats,
        'optimized': {'xp_per_hour_mean': best_score},
    }
    return best, debug
