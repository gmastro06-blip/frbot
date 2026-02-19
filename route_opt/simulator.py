from __future__ import annotations

import math
import random
from typing import List, Tuple, Dict
from .models import Config, Spawn, Node, euclidean


class Simulator:
    def __init__(self, config: Config, seed: int | None = None):
        self.config = config
        self.rand = random.Random(seed if seed is not None else config.seed)

    def travel_time(self, a: Node, b: Node) -> float:
        # If edges provided, external code can compute; fallback to euclidean / speed
        speed = max(1e-6, self.config.player.speed_tiles_per_s)
        return euclidean(a, b) / speed

    def simulate_route(self, route: List[Spawn], route_cycle_seconds: float, mc_iters: int = 200) -> Dict[str, float]:
        """
        Simulate XP gained over 3600s (1h) using Monte Carlo.
        route: ordered list of spawns (cyclic)
        route_cycle_seconds: expected cycle duration (travel + dwell per node sum)
        Returns dict with mean xp_per_hour and std.
        """
        results = []
        for _ in range(mc_iters):
            xp = self._simulate_one(route)
            results.append(xp)

        mean_xp = sum(results) / len(results)
        var = sum((x - mean_xp) ** 2 for x in results) / max(1, len(results) - 1)
        std = math.sqrt(var)
        return {'xp_per_hour_mean': mean_xp, 'xp_per_hour_std': std, 'samples': len(results)}

    def _simulate_one(self, route: List[Spawn]) -> float:
        # Simulate 3600s timeline visiting route cyclically.
        now = 0.0
        end_t = 3600.0
        # Last kill times: -infty -> effectively alive
        last_kill: Dict[str, float] = {s.id: -1e9 for s in route}
        xp_acc = 0.0

        # Precompute dwell per spawn (seconds spent fighting) from kills_per_min
        dwell_map: Dict[str, float] = {}
        for s in route:
            if s.kills_per_min:
                dwell_map[s.id] = max(0.5, 60.0 / s.kills_per_min)
            else:
                dwell_map[s.id] = 5.0

        # Precompute travel times between nodes using nodes lookup
        node_map = {n.id: n for n in self.config.map.nodes}
        travel_times = []
        for i, s in enumerate(route):
            a = node_map[s.node_id]
            b = node_map[route[(i + 1) % len(route)].node_id]
            travel_times.append(self.travel_time(a, b))

        idx = 0
        # Start at route[0]
        while now < end_t:
            s = route[idx]
            # travel to this spawn (skip travel for first visit)
            if idx != 0:
                now += travel_times[idx - 1]

            # Determine if spawn is alive.
            delta = now - last_kill[s.id]
            # Use deterministic respawn by default: alive if delta >= mean
            alive = delta >= max(0.0, s.respawn_s_mean)

            if not alive and self.config.player.constraints.allow_waiting:
                # Decide whether to wait: expected time until alive
                time_to_alive = max(0.0, s.respawn_s_mean - delta)
                # Heuristic: wait if shorter than next travel and yields positive expected xp
                next_travel = travel_times[idx] if travel_times else 0.0
                if time_to_alive < next_travel * 0.8 and time_to_alive < 30.0:
                    # wait until spawn alive
                    now += time_to_alive
                    alive = True

            if alive:
                # Spawn available; simulate kills as stochastic inter-arrival times
                dwell = dwell_map[s.id]
                remaining = dwell
                # treat density as number of creatures; simple model: respawn resets after full clear
                while remaining > 0:
                    mean_interval = max(0.1, 60.0 / (s.kills_per_min or 1.0))
                    interval = self.rand.expovariate(1.0 / mean_interval)
                    if interval >= remaining:
                        break
                    # a kill occurs
                    remaining -= interval
                    xp_acc += s.xp_per_kill
                    last_kill[s.id] = now
                # spend remaining dwell
                now += dwell

            # travel to next
            now += travel_times[idx]
            idx = (idx + 1) % len(route)

        # xp_acc is for 3600s
        return xp_acc
