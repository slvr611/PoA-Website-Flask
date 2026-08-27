"""
Regression tests for the trade risk / bandit camp rework:

- trade_risk is now purely informational: 0% normally, 50% the instant the
  market has a bandit camp anywhere in it. It's no longer additive from
  protection stance / market type / owner units (moved to
  bandit_camp_spawn_chance instead).
- bandit_camp_spawn_chance (base 5%) is the new lever protection stance,
  market type, and owner naval/land units/luxuries actually affect.
- bandit_camp_spawn_tick: each market session-ticks a chance to spawn a new
  camp on a member-owned road tile.
- complex_trade_bandit_loss_tick: an active/ending trade route between two
  nations who share a market with a bandit camp has a flat 25% chance per
  session of having that session's delivery lost — recorded on the bandit
  camp tile's stolen_goods log and on the route's raided_sessions.
- is_delivering() treats a raided session as non-delivering for both sides.
"""
from unittest.mock import patch
from bson import ObjectId

import calculations.compute_functions as cf
import helpers.tick_helpers as th
from helpers.trade_route_helpers import is_delivering


def _patched_db(test_db):
    """Swap app_core.mongo's shared .db attribute to the mongomock test_db —
    every module that did `from app_core import mongo` holds the SAME mongo
    object, so mutating its .db is visible to tick_helpers, compute_functions,
    and trade_route_helpers all at once (unlike patching a name in one module)."""
    from app_core import mongo as real_mongo
    return real_mongo, test_db


class TestComputeTradeRisk:
    def test_zero_with_no_bandit_camp(self, test_db):
        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            result = cf.compute_trade_risk("trade_risk", {"name": "Grand Bazaar"}, 0, {}, {})
        finally:
            real_mongo.db = original_db
        assert result == 0.0

    def test_fifty_percent_with_a_bandit_camp_anywhere_in_the_market(self, test_db):
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}})
        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            result = cf.compute_trade_risk("trade_risk", {"name": "Grand Bazaar"}, 0, {}, {})
        finally:
            real_mongo.db = original_db
        assert result == 0.5

    def test_camp_in_a_different_market_does_not_count(self, test_db):
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Thieves' Den"}})
        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            result = cf.compute_trade_risk("trade_risk", {"name": "Grand Bazaar"}, 0, {}, {})
        finally:
            real_mongo.db = original_db
        assert result == 0.0


class TestComputeBanditCampSpawnChance:
    def test_base_value_plus_flat_modifiers(self):
        result = cf.compute_bandit_camp_spawn_chance(
            "bandit_camp_spawn_chance", {}, 0.05, {}, {"bandit_camp_spawn_chance": 0.15}
        )
        assert result == 0.2

    def test_clamped_between_zero_and_one(self):
        result = cf.compute_bandit_camp_spawn_chance(
            "bandit_camp_spawn_chance", {}, 0.05, {}, {"bandit_camp_spawn_chance": -1}
        )
        assert result == 0.0

    def test_per_owner_naval_unit_scales_by_market_head(self, test_db):
        head_id = ObjectId()
        test_db.nations.insert_one({"_id": head_id, "naval_unit_count": 3, "land_unit_count": 0, "resource_storage": {}})
        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            result = cf.compute_bandit_camp_spawn_chance(
                "bandit_camp_spawn_chance", {"market_head": str(head_id)}, 0.05, {},
                {"bandit_camp_spawn_chance_per_owner_naval_unit": -0.01, "market_tier_multiplier": 1},
            )
        finally:
            real_mongo.db = original_db
        assert round(result, 10) == 0.02


class TestIsDeliveringRespectsRaidedSessions:
    def test_raided_session_does_not_deliver(self):
        route = {"accepted_session": 1, "delay": 0, "duration_ticks": None, "raided_sessions": [2]}
        assert is_delivering(route, 2) is False

    def test_non_raided_session_still_delivers(self):
        route = {"accepted_session": 1, "delay": 0, "duration_ticks": None, "raided_sessions": [2]}
        assert is_delivering(route, 3) is True


class TestBanditCampSpawnTick:
    def _market(self, market_id, name="Grand Bazaar", spawn_chance=0.05):
        return {"_id": market_id, "name": name, "bandit_camp_spawn_chance": spawn_chance}

    def test_no_spawn_when_roll_fails(self, test_db):
        market_id = ObjectId()
        nation_id = ObjectId()
        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.nations.insert_one({"_id": nation_id, "name": "Testland"})
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "owner": "Testland", "route": {}})

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.99):
                market = self._market(market_id)
                log = th.bandit_camp_spawn_tick(market, market, {}, pending_tiles=[])
        finally:
            real_mongo.db = original_db

        assert log == ""
        assert test_db.hex_map_tiles.find_one({"q": 0, "r": 0}).get("bandit_camp") is None

    def test_spawns_on_member_owned_route_tile_when_roll_succeeds(self, test_db):
        market_id = ObjectId()
        nation_id = ObjectId()
        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.nations.insert_one({"_id": nation_id, "name": "Testland"})
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "owner": "Testland", "route": {}})
        # A route tile NOT owned by a member — must never be picked.
        test_db.hex_map_tiles.insert_one({"q": 9, "r": 9, "owner": "Otherland", "route": {}})

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        pending_tiles = []
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.0):
                market = self._market(market_id)
                log = th.bandit_camp_spawn_tick(market, market, {}, pending_tiles=pending_tiles)
        finally:
            real_mongo.db = original_db

        assert "bandit camp" in log.lower()
        assert len(pending_tiles) == 1
        tile_id = test_db.hex_map_tiles.find_one({"q": 0, "r": 0})["_id"]
        assert pending_tiles[0]["_id"] == tile_id
        assert pending_tiles[0]["set"]["bandit_camp"]["market"] == "Grand Bazaar"

    def test_prefers_empty_tile_over_one_with_a_district(self, test_db):
        market_id = ObjectId()
        nation_id = ObjectId()
        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.nations.insert_one({"_id": nation_id, "name": "Testland"})
        # Occupied route tile
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "owner": "Testland", "route": {}, "district": {"id": "x"}})
        # Empty route tile — must be the one picked
        test_db.hex_map_tiles.insert_one({"q": 1, "r": 0, "owner": "Testland", "route": {}})

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        pending_tiles = []
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.0), \
                 patch("helpers.tick_helpers.random.choice", side_effect=lambda seq: seq[0]):
                market = self._market(market_id)
                th.bandit_camp_spawn_tick(market, market, {}, pending_tiles=pending_tiles)
        finally:
            real_mongo.db = original_db

        empty_tile_id = test_db.hex_map_tiles.find_one({"q": 1, "r": 0})["_id"]
        assert pending_tiles[0]["_id"] == empty_tile_id


class TestComplexTradeBanditLossTick:
    def _setup_two_member_market(self, test_db, market_id, nation_a="Alpha", nation_b="Beta"):
        a_id, b_id = ObjectId(), ObjectId()
        test_db.nations.insert_many([
            {"_id": a_id, "name": nation_a},
            {"_id": b_id, "name": nation_b},
        ])
        test_db.market_links.insert_many([
            {"member": str(a_id), "market": str(market_id)},
            {"member": str(b_id), "market": str(market_id)},
        ])

    def test_no_op_without_a_bandit_camp(self, test_db):
        market_id = ObjectId()
        self._setup_two_member_market(test_db, market_id)
        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            market = {"_id": market_id, "name": "Grand Bazaar"}
            log = th.complex_trade_bandit_loss_tick(market, market, {}, pending_tiles=[])
        finally:
            real_mongo.db = original_db
        assert log == ""

    def test_delivering_route_raided_on_successful_roll(self, test_db, monkeypatch):
        market_id = ObjectId()
        self._setup_two_member_market(test_db, market_id)
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}})
        route_id = ObjectId()
        test_db.trade_routes.insert_one({
            "_id": route_id, "nation_a": "Alpha", "nation_b": "Beta",
            "status": "active", "accepted_session": 1, "delay": 0, "duration_ticks": None,
            "resources_a_to_b": [{"resource": "food", "quantity": 10}],
            "resources_b_to_a": [],
        })
        monkeypatch.setattr("helpers.trade_route_helpers._current_session", lambda: 1)

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        pending_tiles = []
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.0):
                market = {"_id": market_id, "name": "Grand Bazaar"}
                log = th.complex_trade_bandit_loss_tick(market, market, {}, pending_tiles=pending_tiles)
        finally:
            real_mongo.db = original_db

        assert "raided" in log.lower()
        updated_route = test_db.trade_routes.find_one({"_id": route_id})
        assert 1 in updated_route["raided_sessions"]
        assert 1 in updated_route["raid_checked_sessions"]
        assert len(pending_tiles) == 1
        stolen = pending_tiles[0]["set"]["bandit_camp"]["stolen_goods"]
        assert len(stolen) == 1
        assert stolen[0]["nation_a"] == "Alpha"
        assert stolen[0]["resources_a_to_b"] == [{"resource": "food", "quantity": 10}]

    def test_delivering_route_not_raided_on_failed_roll(self, test_db, monkeypatch):
        market_id = ObjectId()
        self._setup_two_member_market(test_db, market_id)
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}})
        route_id = ObjectId()
        test_db.trade_routes.insert_one({
            "_id": route_id, "nation_a": "Alpha", "nation_b": "Beta",
            "status": "active", "accepted_session": 1, "delay": 0, "duration_ticks": None,
            "resources_a_to_b": [{"resource": "food", "quantity": 10}],
            "resources_b_to_a": [],
        })
        monkeypatch.setattr("helpers.trade_route_helpers._current_session", lambda: 1)

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.99):
                market = {"_id": market_id, "name": "Grand Bazaar"}
                log = th.complex_trade_bandit_loss_tick(market, market, {}, pending_tiles=[])
        finally:
            real_mongo.db = original_db

        assert log == ""
        updated_route = test_db.trade_routes.find_one({"_id": route_id})
        assert updated_route.get("raided_sessions", []) == []
        assert 1 in updated_route["raid_checked_sessions"]

    def test_route_already_checked_this_session_is_not_rerolled(self, test_db, monkeypatch):
        """Two nations sharing 2 markets that both have camps must not get
        rolled twice for the same session's delivery."""
        market_id = ObjectId()
        self._setup_two_member_market(test_db, market_id)
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}})
        route_id = ObjectId()
        test_db.trade_routes.insert_one({
            "_id": route_id, "nation_a": "Alpha", "nation_b": "Beta",
            "status": "active", "accepted_session": 1, "delay": 0, "duration_ticks": None,
            "resources_a_to_b": [{"resource": "food", "quantity": 10}],
            "resources_b_to_a": [],
            "raid_checked_sessions": [1],
        })
        monkeypatch.setattr("helpers.trade_route_helpers._current_session", lambda: 1)

        real_mongo, _ = _patched_db(test_db)
        original_db = real_mongo.db
        real_mongo.db = test_db
        try:
            with patch("helpers.tick_helpers.random.random", return_value=0.0):
                market = {"_id": market_id, "name": "Grand Bazaar"}
                log = th.complex_trade_bandit_loss_tick(market, market, {}, pending_tiles=[])
        finally:
            real_mongo.db = original_db

        assert log == ""
        updated_route = test_db.trade_routes.find_one({"_id": route_id})
        assert updated_route.get("raided_sessions", []) == []
