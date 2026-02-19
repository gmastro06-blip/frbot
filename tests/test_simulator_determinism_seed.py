from route_opt.io import load_config
from route_opt.simulator import Simulator


def test_simulator_determinism_seed():
    cfg = load_config('tools/example_route_config.json')
    sim1 = Simulator(cfg, seed=123)
    sim2 = Simulator(cfg, seed=123)
    route = cfg.spawns[:2]
    a = sim1.simulate_route(route, route_cycle_seconds=0.0, mc_iters=20)
    b = sim2.simulate_route(route, route_cycle_seconds=0.0, mc_iters=20)
    assert a['xp_per_hour_mean'] == b['xp_per_hour_mean']
