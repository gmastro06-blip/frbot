from __future__ import annotations

import argparse
import json
import os
from .io import load_config
from .optimizer import optimize
from .simulator import Simulator


def write_route(out_dir: str, route: list, sim_stats: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    route_path = os.path.join(out_dir, 'route.json')
    with open(route_path, 'w', encoding='utf-8') as f:
        json.dump([{'node_id': s.node_id, 'spawn_id': s.id} for s in route], f, indent=2)


def write_report(out_dir: str, baseline: dict, optimized: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'report.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# Route optimization report\n\n')
        f.write('## Summary\n\n')
        f.write('|variant|xp/h mean|xp/h std|samples|\n')
        f.write('|---|---:|---:|---:|\n')
        f.write(f"|baseline|{baseline.get('xp_per_hour_mean'):.2f}|{baseline.get('xp_per_hour_std',0):.2f}|{baseline.get('samples',0)}|\n")
        f.write(f"|optimized|{optimized.get('xp_per_hour_mean'):.2f}|{optimized.get('xp_per_hour_std',0):.2f}|{optimized.get('samples',0)}|\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--input', '-i', required=True)
    p.add_argument('--output', '-o', required=True)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--mc-iters', type=int, default=200)
    args = p.parse_args(argv)

    cfg = load_config(args.input)
    route, debug = optimize(cfg.spawns, cfg, mc_iters=max(10, args.mc_iters), seed=args.seed)

    sim = Simulator(cfg, seed=args.seed)
    baseline = debug['baseline']
    optimized_stats = sim.simulate_route(route, route_cycle_seconds=0.0, mc_iters=args.mc_iters)

    write_route(args.output, route, optimized_stats)
    write_report(args.output, baseline, optimized_stats)


if __name__ == '__main__':
    main()
