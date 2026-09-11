"""
Regression tests for tick_helpers._dispatch's new world_city_coords
binding — the mechanism that lets the tick loop build a single, shared,
mutated-in-place city-coordinate cache ONCE per tick run and hand it only
to ai_decision_tick, instead of that function (or _select_best_city
underneath it) re-querying every city on the map once per AI nation
(~190 nations/tick). Mirrors the existing pending_tiles/flee_events
dispatch-binding tests' shape.
"""
import helpers.tick_helpers as th


def test_ai_decision_tick_is_registered_as_world_city_coords_aware():
    assert th.ai_decision_tick in th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS


def test_dispatch_forwards_world_city_coords_only_to_registered_functions():
    received = {}

    def fake_aware_tick(nation, **kwargs):
        received["aware"] = kwargs.get("world_city_coords")
        return ""

    def fake_unaware_tick(nation):
        received["unaware_called"] = True
        return ""

    coords = {(1, 2), (3, 4)}
    th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS.add(fake_aware_tick)
    try:
        th._dispatch(fake_aware_tick, [], {"name": "Test"}, world_city_coords=coords)
        th._dispatch(fake_unaware_tick, [], {"name": "Test"}, world_city_coords=coords)
    finally:
        th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS.discard(fake_aware_tick)

    assert received["aware"] is coords
    assert received["unaware_called"] is True


def test_dispatch_defaults_to_none_when_not_supplied():
    received = {}

    def fake_aware_tick(nation, **kwargs):
        received["aware"] = kwargs.get("world_city_coords", "missing")
        return ""

    th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS.add(fake_aware_tick)
    try:
        th._dispatch(fake_aware_tick, [], {"name": "Test"})
    finally:
        th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS.discard(fake_aware_tick)

    assert received["aware"] is None
