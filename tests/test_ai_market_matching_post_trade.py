"""
Tests for the post-trade AI decision pass: after AI Market Matching Tick
resolves buy/sell orders, every nation that bought OR sold something gets a
full second AI decision pass (_run_post_trade_decision_pass, wrapping the
same _run_ai_decision_pass pipeline ai_decision_tick uses pre-trade) —
goal selection, multi-district building, full job reassignment — using
post-trade resources, instead of the old _post_trade_build_and_rejob's
narrow "is the one already-planned district affordable now" check.

This is a deliberate second decision round (the AI can build twice per
tick: once pre-trade, once post-trade), not just a numbers-accuracy patch.
Replaces the old (deleted) _post_trade_build_and_rejob, which only handled
buyers — sellers who earned money from a sale never got a chance to build
with it before this change.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh
import helpers.hex_map_helpers as hmh
import helpers.tick_helpers as th


def _base_state(money=1000, district_slots=1):
    return {
        "money": money, "money_income": 0, "stockpiles": {}, "net_production": {},
        "resource_capacity": {}, "active_resources": set(), "open_district_slots": district_slots,
        "existing_def_keys": set(), "existing_def_key_counts": {}, "available_jobs": {},
        "existing_types": set(), "total_pops": 0, "idle_pops": 0,
        "sessions_until_empty": {},
    }


class TestRunAiMarketMatchingTracksBuyersAndSellers:
    def _nation(self, name, **overrides):
        n = {
            "_id": ObjectId(), "name": name, "temperament": "Aggressive",
            "money": 100, "resource_storage": {}, "resource_desires": [],
        }
        n.update(overrides)
        return n

    def test_buyer_and_seller_are_both_reported(self, monkeypatch):
        buyer = self._nation("Buyer", resource_desires=[
            {"resource": "wood", "trade_type": "Need to Buy", "price": 5, "quantity": 10},
        ])
        seller = self._nation("Seller", resource_storage={"wood": 50}, resource_desires=[
            {"resource": "wood", "trade_type": "Need to Sell", "price": 5, "quantity": 10},
        ])
        old_nations = [dict(buyer), dict(seller)]
        new_nations = [dict(buyer), dict(seller)]

        monkeypatch.setattr(adh, "_load_trade_tiles", lambda names: {}, raising=False)
        with patch("helpers.trade_route_helpers._load_trade_tiles", return_value={}), \
             patch("helpers.trade_route_helpers._build_portal_map", return_value={}), \
             patch("helpers.trade_route_helpers._terrain_move_costs", return_value={}), \
             patch("helpers.trade_route_helpers._get_trade_source_wonder_ids", return_value=set()), \
             patch.object(adh, "_build_ai_distance_cache", return_value={"Buyer": {"Seller": 0}}):
            buyers, sellers = adh._run_ai_market_matching(old_nations, new_nations, [])

        assert buyers == {0}
        assert sellers == {1}
        assert new_nations[0]["resource_storage"].get("wood") == 10
        assert new_nations[1]["money"] > 100

    def test_no_trade_returns_empty_sets(self):
        old_nations, new_nations = [], []
        buyers, sellers = adh._run_ai_market_matching(old_nations, new_nations, [])
        assert buyers == set()
        assert sellers == set()


class TestRunPostTradeDecisionPass:
    def test_recalculates_when_jobs_changed(self):
        old_nation = {"_id": ObjectId(), "name": "Test Nation", "temperament": "Aggressive"}
        new_nation = {"jobs": {"miner": 1}, "districts": [], "cities": []}
        log_lines = []

        def _fake_pass(old_n, new_n, schema, pending_tiles=None, world_city_coords=None, pass_label=""):
            new_n["jobs"] = {"miner": 2}  # simulate a real job change
            return f"{old_n['name']}: {pass_label} (1 actions)\n"

        with patch.object(adh, "_run_ai_decision_pass", side_effect=_fake_pass) as pass_spy, \
             patch("calculations.field_calculations.calculate_all_fields", return_value={"resource_production": {"wood": 5}}) as calc_spy:
            adh._run_post_trade_decision_pass(old_nation, new_nation, {}, log_lines)

        pass_spy.assert_called_once()
        assert pass_spy.call_args.kwargs["pass_label"] == "AI decision (post-trade)"
        calc_spy.assert_called_once()
        assert new_nation["resource_production"] == {"wood": 5}
        assert "AI decision (post-trade)" in log_lines[0]

    def test_skips_recalculation_when_nothing_changed(self):
        old_nation = {"_id": ObjectId(), "name": "Test Nation", "temperament": "Aggressive"}
        new_nation = {"jobs": {"miner": 1}, "districts": [], "cities": []}
        log_lines = []

        def _fake_pass(old_n, new_n, schema, pending_tiles=None, world_city_coords=None, pass_label=""):
            return f"{old_n['name']}: {pass_label} (0 actions)\n"  # no state change

        with patch.object(adh, "_run_ai_decision_pass", side_effect=_fake_pass), \
             patch("calculations.field_calculations.calculate_all_fields") as calc_spy:
            adh._run_post_trade_decision_pass(old_nation, new_nation, {}, log_lines)

        calc_spy.assert_not_called()

    def test_recalculate_false_never_calls_calculate_all_fields_even_if_changed(self):
        """run_ai_market_matching_standalone passes recalculate=False since
        its own system_approve_change call always recalculates on save —
        doing it twice would be wasted work."""
        old_nation = {"_id": ObjectId(), "name": "Test Nation", "temperament": "Aggressive"}
        new_nation = {"jobs": {"miner": 1}, "districts": [], "cities": []}
        log_lines = []

        def _fake_pass(old_n, new_n, schema, pending_tiles=None, world_city_coords=None, pass_label=""):
            new_n["jobs"] = {"miner": 5}
            return ""

        with patch.object(adh, "_run_ai_decision_pass", side_effect=_fake_pass), \
             patch("calculations.field_calculations.calculate_all_fields") as calc_spy:
            adh._run_post_trade_decision_pass(old_nation, new_nation, {}, log_lines, recalculate=False)

        calc_spy.assert_not_called()


class TestAiMarketMatchingTickDispatchesToBuyersAndSellers:
    def test_both_buyer_and_seller_get_a_post_trade_pass(self):
        old_nations = [{"_id": ObjectId(), "name": "Buyer"}, {"_id": ObjectId(), "name": "Seller"}]
        new_nations = [dict(old_nations[0]), dict(old_nations[1])]
        pending_tiles = []
        world_city_coords = {(1, 1)}

        with patch.object(adh, "_run_ai_market_matching", return_value=({0}, {1})), \
             patch.object(adh, "_run_post_trade_decision_pass") as pass_spy:
            adh.ai_market_matching_tick(
                old_nations, new_nations, {}, pending_tiles=pending_tiles,
                world_city_coords=world_city_coords,
            )

        assert pass_spy.call_count == 2
        called_names = {c.args[1]["name"] for c in pass_spy.call_args_list}
        assert called_names == {"Buyer", "Seller"}
        for c in pass_spy.call_args_list:
            assert c.kwargs["pending_tiles"] is pending_tiles
            assert c.kwargs["world_city_coords"] is world_city_coords

    def test_a_nation_that_is_both_buyer_and_seller_only_runs_once(self):
        old_nations = [{"_id": ObjectId(), "name": "BothSides"}]
        new_nations = [dict(old_nations[0])]

        with patch.object(adh, "_run_ai_market_matching", return_value=({0}, {0})), \
             patch.object(adh, "_run_post_trade_decision_pass") as pass_spy:
            adh.ai_market_matching_tick(old_nations, new_nations, {})

        pass_spy.assert_called_once()

    def test_no_trades_calls_nothing(self):
        old_nations = [{"_id": ObjectId(), "name": "Idle"}]
        new_nations = [dict(old_nations[0])]

        with patch.object(adh, "_run_ai_market_matching", return_value=(set(), set())), \
             patch.object(adh, "_run_post_trade_decision_pass") as pass_spy:
            result = adh.ai_market_matching_tick(old_nations, new_nations, {})

        pass_spy.assert_not_called()
        assert result == ""

    def test_error_in_one_nations_pass_does_not_block_others(self):
        old_nations = [{"_id": ObjectId(), "name": "Broken"}, {"_id": ObjectId(), "name": "Fine"}]
        new_nations = [dict(old_nations[0]), dict(old_nations[1])]

        def _side_effect(old_n, new_n, schema, log_lines, **kwargs):
            if old_n["name"] == "Broken":
                raise RuntimeError("boom")
            log_lines.append("Fine: AI decision (post-trade) (1 actions)")

        with patch.object(adh, "_run_ai_market_matching", return_value=({0, 1}, set())), \
             patch.object(adh, "_run_post_trade_decision_pass", side_effect=_side_effect):
            result = adh.ai_market_matching_tick(old_nations, new_nations, {})

        assert "Broken" in result and "error" in result
        assert "Fine" in result


class TestWorldCityCoordsSurvivesPastNationTickLoopInRealTick:
    """tick()'s own local world_city_coords variable is reassigned to None
    on every NATION_TICK_FUNCTIONS label that isn't "AI Decision Tick" — by
    the time the loop reaches NATION_CROSS_TICK_FUNCTIONS it's already back
    to None. ai_world_city_coords is a second variable, set once and never
    reset, that survives past the loop specifically so AI Market Matching
    Tick's post-trade pass can still see pass 1's set (mutated in place with
    any cities pass 1 placed this tick, not yet in the database). This
    exercises the real tick() function end to end, unlike the other
    dispatch-level tests above, to prove that specific survival."""

    def test_ai_market_matching_tick_receives_the_same_set_ai_decision_tick_built(self):
        from unittest.mock import MagicMock, patch
        from app_core import category_data
        import helpers.tick_helpers as th

        fake_nation = {"_id": ObjectId(), "name": "Test Nation", "temperament": "Aggressive"}
        find_mock = MagicMock(return_value=MagicMock(sort=MagicMock(return_value=[fake_nation])))
        fake_nation_db = MagicMock(find=find_mock)
        fake_mongo = MagicMock()
        fake_mongo.db.__getitem__.return_value.find_one.return_value = None
        # get_all_tiles_from_chunks (via calculate_all_fields's admin-range
        # computation) reads global_modifiers by attribute, not by
        # __getitem__ — without this it hit the REAL local MongoDB through
        # hex_map_helpers's own mongo reference, adding ~15s of real network
        # I/O to this test.
        fake_mongo.db.global_modifiers.find_one.return_value = None
        fake_mongo.db.hex_map_tile_chunks.find.return_value = []

        the_coords_set = {(9, 9)}
        dispatch_calls = []
        real_dispatch = th._dispatch

        def _spy_dispatch(tick_function, pending, *args, **kwargs):
            if tick_function is th.ai_market_matching_tick:
                dispatch_calls.append(kwargs.get("world_city_coords"))
            return real_dispatch(tick_function, pending, *args, **kwargs)

        original_nations_db = category_data["nations"]["database"]
        category_data["nations"]["database"] = fake_nation_db
        try:
            with patch.object(th, "mongo", fake_mongo), \
                 patch("helpers.archive_helpers.mongo", fake_mongo), \
                 patch.object(hmh, "mongo", fake_mongo), \
                 patch("app_core.time.sleep"), \
                 patch.object(th, "_fetch_world_city_coords", return_value=the_coords_set), \
                 patch.object(th, "ai_decision_tick", return_value=""), \
                 patch.object(adh, "_run_ai_market_matching", return_value=(set(), set())), \
                 patch.object(th, "_dispatch", side_effect=_spy_dispatch):
                result = th.tick({
                    "run_AI Decision Tick": "on",
                    "run_AI Market Matching Tick": "on",
                })
        finally:
            category_data["nations"]["database"] = original_nations_db

        assert "FAILED" not in result
        assert dispatch_calls == [the_coords_set]


class TestWorldCityCoordsThreadingToAiMarketMatchingTick:
    def test_ai_market_matching_tick_is_registered_as_world_city_coords_aware(self):
        assert th.ai_market_matching_tick in th._WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS

    def test_ai_market_matching_tick_is_still_tile_pending_aware(self):
        assert th.ai_market_matching_tick in th._TILE_PENDING_AWARE_TICK_FUNCTIONS

    def test_dispatch_forwards_world_city_coords_to_ai_market_matching_tick(self):
        """_dispatch only binds world_city_coords for functions registered
        in _WORLD_CITY_COORDS_AWARE_TICK_FUNCTIONS — that set holds the real
        ai_market_matching_tick function object captured at module-load
        time, so patching th.ai_market_matching_tick to a Mock wouldn't be
        the same object the set was built with. Verify the real binding by
        calling _dispatch with the real function and observing the effect
        (the kwarg reaching _run_post_trade_decision_pass), the same way
        TestAiMarketMatchingTickDispatchesToBuyersAndSellers does."""
        old_nations = [{"_id": ObjectId(), "name": "Buyer"}]
        new_nations = [dict(old_nations[0])]
        coords = {(5, 5)}

        with patch.object(adh, "_run_ai_market_matching", return_value=({0}, set())), \
             patch.object(adh, "_run_post_trade_decision_pass") as pass_spy:
            th._dispatch(
                th.ai_market_matching_tick, [], old_nations, new_nations, {},
                pending_tiles=[], world_city_coords=coords,
            )

        pass_spy.assert_called_once()
        assert pass_spy.call_args.kwargs["world_city_coords"] is coords


class TestPostTradePassCanBuildBeyondTheOriginalPlan:
    """Proves the post-trade pass runs the FULL evaluate_goal_district loop
    (goal selection + multi-build), not just a check against one
    already-planned district — the actual gap _post_trade_build_and_rejob
    had. Mirrors the heavy-mocking pattern already established in
    test_ai_district_city_build_requires_placement.py for exercising this
    pipeline without needing full realistic game-balance data."""

    def test_seller_with_new_money_builds_a_district_post_trade(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False,
            "cost": {"money": 50}, "map_count": 1,
        })
        # An existing building anchors the new district's placement — a
        # nation with zero buildings has no adjacency anchor and can't
        # legally place anything (see _pick_district_tile/
        # _compute_legal_placement; matches
        # test_ai_district_city_build_requires_placement.py's own
        # owned_tile setup for this exact reason).
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test Nation", "q": 0, "r": 0, "terrain": "plains",
            "district": {"id": "existing1", "def_key": "farm", "display_name": "Farm", "type": ""},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test Nation", "q": 1, "r": 0, "terrain": "plains",
        })
        old_nation = {
            "_id": ObjectId(), "name": "Test Nation", "money": 100,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Standard", "temperament": "Aggressive",
        }
        # Post-trade state: nation just sold something and now has money
        # it didn't have pre-trade.
        new_nation = dict(old_nation)
        new_nation["money"] = 500

        candidate = (10.0, "forge", "Forge", {"money": 50}, "test rationale", "db")
        goal = {"type": "expand_economy", "display_name": "Expand Economy", "score": 1, "rationale": "test"}

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(hmh, "mongo", fake_mongo), \
             patch("app_core.mongo", fake_mongo), \
             patch.object(adh, "_select_best_city", return_value=None), \
             patch.object(adh, "score_buildable_districts", return_value=[candidate]), \
             patch.object(adh, "_apply_goal_alignment", return_value=([candidate], set(), set(), set())), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=False), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, [], 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])), \
             patch.object(adh, "evaluate_nation_state", return_value=_base_state(money=500)), \
             patch.object(adh, "get_stored_market_prices", return_value={}), \
             patch.object(adh, "assign_goal_jobs", return_value=({}, [], {})), \
             patch.object(adh, "select_tech_target", return_value=None), \
             patch.object(adh, "generate_goal_trade_desires", return_value=[]):
            log_lines = []
            adh._run_post_trade_decision_pass(old_nation, new_nation, {}, log_lines, recalculate=False)

        built_keys = {d.get("def_key") for d in new_nation.get("districts", [])}
        assert "forge" in built_keys, log_lines
        assert new_nation["money"] == 450  # 500 - 50 cost
