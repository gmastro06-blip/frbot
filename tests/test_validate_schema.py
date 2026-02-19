from route_opt.io import load_config
import pytest


def test_load_example():
    cfg = load_config('tools/example_route_config.json')
    assert cfg.map.nodes
    assert cfg.spawns


def test_missing_fields(tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text('{}')
    with pytest.raises(ValueError):
        load_config(str(p))
