from route_opt.io import load_config
from route_opt.optimizer import optimize, greedy_initial
from route_opt.simulator import Simulator


def test_optimizer_improves_over_baseline():
    cfg = load_config('tools/example_route_config.json')
    spawns = cfg.spawns
    baseline = greedy_initial(spawns, cfg, max_waypoints=3)
    sim1 = Simulator(cfg, seed=12345)
    sim2 = Simulator(cfg, seed=12345)
    base_stats = sim1.simulate_route(baseline, route_cycle_seconds=0.0, mc_iters=30)
    route, debug = optimize(spawns, cfg, mc_iters=60, seed=12345)
    opt_stats = sim2.simulate_route(route, route_cycle_seconds=0.0, mc_iters=30)
    assert opt_stats['xp_per_hour_mean'] + 1e-6 >= base_stats['xp_per_hour_mean']
