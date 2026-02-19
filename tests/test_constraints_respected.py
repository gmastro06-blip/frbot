from route_opt.io import load_config
from route_opt.optimizer import optimize


def test_constraints_respected():
    cfg = load_config('tools/example_route_config.json')
    # set constraint
    cfg.player.constraints.max_waypoints = 2
    route, debug = optimize(cfg.spawns, cfg, mc_iters=20, seed=7)
    assert len(route) <= 2
