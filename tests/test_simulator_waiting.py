from route_opt.io import load_config
from route_opt.simulator import Simulator


def test_waiting_increases_xp():
    cfg = load_config('tools/example_route_config.json')
    # create a single slow-respawn spawn
    s = cfg.spawns[0]
    s.respawn_s_mean = 5.0
    cfg.player.constraints.allow_waiting = True
    sim_wait = Simulator(cfg, seed=7)
    sim_nowait = Simulator(cfg, seed=7)
    route = [s]
    stats_wait = sim_wait.simulate_route(route, 0.0, mc_iters=50)
    cfg.player.constraints.allow_waiting = False
    stats_no = sim_nowait.simulate_route(route, 0.0, mc_iters=50)
    assert stats_wait['xp_per_hour_mean'] >= stats_no['xp_per_hour_mean']
