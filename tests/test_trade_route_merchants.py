"""
Tests for extending the trade route system to merchant companies, positioned
by their specific current city (merchant.current_city_id) rather than a
whole nation's city network:

  - A merchant with no current city set behaves like a plain member of its
    home nation (merchant.location) for trade-distance purposes — same
    fallback a nation with no owned cities gets (falls back to all owned
    tiles).
  - A merchant WITH a current city is positioned at exactly that one tile —
    not "anywhere its home nation owns," even if the home nation has other,
    better-connected cities. This is the actual point of the feature: which
    city a merchant is in determines what it can reach.
  - A merchant can ALSO be based in additional cities — including cities
    belonging to other nations — via one "additional_trade_city" modifier
    per extra city (json-data/modifier_types.json). Its trade reach becomes
    the union of every city it's based in.
  - get_road_path_distance and get_connectable_parties accept "merchant" as
    a party type on either side.
  - The nation-only wrapper (_dijkstra_from_cities, and therefore
    get_connectable_nations/get_road_path_distance's existing nation-only
    behavior) is unchanged.

Map layout shared by these tests (axial q,r; AXIAL_DIRECTIONS from
hex_map_helpers.py — (1,0)/(2,0) are adjacent-adjacent along the q axis):

    (0,0) NationA capital city  --road(1,0)--  (2,0) NationB capital city

    (10,0) NationA's second city — isolated, no road to anything.
"""
from unittest.mock import patch

from helpers.trade_route_helpers import (
    get_road_path_distance,
    get_connectable_parties,
    get_connectable_nations,
    _party_position_set,
    _merchant_home_nation_name,
    _additional_trade_city_ids,
)


def _patch_mongo(test_db):
    return patch("helpers.trade_route_helpers.mongo", **{"db": test_db})


def _insert_basic_map(test_db):
    """NationA (two cities) <-road-> NationB, per the module docstring map."""
    test_db["hex_map_tiles"].insert_many([
        {"q": 0, "r": 0, "owner": "NationA", "terrain": "plains",
         "city": {"id": "cityA1", "name": "Alpha", "type": "generic"}, "capital": True},
        {"q": 1, "r": 0, "owner": "NationA", "terrain": "plains", "route": {"tier": 1}},
        {"q": 2, "r": 0, "owner": "NationB", "terrain": "plains",
         "city": {"id": "cityB1", "name": "Beta", "type": "generic"}, "capital": True},
        {"q": 10, "r": 0, "owner": "NationA", "terrain": "plains",
         "city": {"id": "cityA2", "name": "Isolated", "type": "generic"}},
    ])
    test_db["nations"].insert_many([
        {"name": "NationA", "trade_speed": 7, "overall_total_modifiers": {}},
        {"name": "NationB", "trade_speed": 7, "overall_total_modifiers": {}},
    ])


class TestMerchantHomeNationResolution:
    def test_resolves_location_to_nation_name(self, test_db):
        nation_id = test_db["nations"].insert_one({"name": "NationA", "trade_speed": 7}).inserted_id
        merchant = {"location": str(nation_id)}
        with _patch_mongo(test_db):
            assert _merchant_home_nation_name(merchant) == "NationA"

    def test_no_location_returns_none(self, test_db):
        with _patch_mongo(test_db):
            assert _merchant_home_nation_name({}) is None
            assert _merchant_home_nation_name(None) is None


class TestAdditionalTradeCityIds:
    def test_extracts_city_ids_from_matching_modifiers(self):
        merchant = {
            "modifiers": [
                {"modifier_type": "additional_trade_city", "city": "cityB1", "value": 1},
                {"modifier_type": "additional_trade_city", "city": "cityA2", "value": 1},
                {"modifier_type": "money_income", "value": 10},  # unrelated, ignored
            ]
        }
        assert _additional_trade_city_ids(merchant) == ["cityB1", "cityA2"]

    def test_no_modifiers_returns_empty(self):
        assert _additional_trade_city_ids({}) == []
        assert _additional_trade_city_ids(None) == []

    def test_modifier_missing_city_value_is_skipped(self):
        merchant = {"modifiers": [{"modifier_type": "additional_trade_city"}]}
        assert _additional_trade_city_ids(merchant) == []


class TestPartyPositionSetForMerchants:
    def test_merchant_with_no_current_city_falls_back_to_home_nation_cities(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({"name": "Traders Inc", "location": str(nation_a_id)})

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        # Falls back to NationA's city tiles (both of them — no specific city set)
        assert positions == {(0, 0), (10, 0)}
        assert home == "NationA"

    def test_merchant_with_current_city_uses_only_that_tile(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1",
        })

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        assert positions == {(0, 0)}
        assert home == "NationA"

    def test_current_city_id_pointing_at_nothing_falls_back(self, test_db):
        """A stale/incorrect current_city_id that doesn't match any city tile
        is treated as unset, not as a dead-end position."""
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id),
            "current_city_id": "does-not-exist",
        })

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        assert positions == {(0, 0), (10, 0)}

    def test_additional_trade_city_modifier_adds_to_primary_city(self, test_db):
        """A merchant based primarily in cityA1, with an additional_trade_city
        modifier pointing at cityB1 (a DIFFERENT nation's city) — reach should
        be the union of both, not just the primary."""
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1",
            "modifiers": [{"modifier_type": "additional_trade_city", "city": "cityB1", "value": 1}],
        })

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        assert positions == {(0, 0), (2, 0)}
        assert home == "NationA"  # only the primary home nation, not NationB

    def test_additional_trade_city_alone_with_no_primary_city_still_works(self, test_db):
        """No current_city_id set at all, but an additional_trade_city
        modifier is present — that alone should be enough to NOT fall back
        to the home-nation-wide behavior."""
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id),
            "modifiers": [{"modifier_type": "additional_trade_city", "city": "cityB1", "value": 1}],
        })

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        assert positions == {(2, 0)}

    def test_invalid_additional_city_id_is_silently_dropped(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1",
            "modifiers": [{"modifier_type": "additional_trade_city", "city": "nonexistent", "value": 1}],
        })

        with _patch_mongo(test_db):
            tiles = list(test_db["hex_map_tiles"].find())
            tile_map = {(t["q"], t["r"]): t for t in tiles}
            positions, home = _party_position_set("merchant", "Traders Inc", tile_map, set())

        assert positions == {(0, 0)}


class TestGetRoadPathDistanceWithMerchants:
    def test_merchant_at_connected_city_reaches_partner_nation(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1",
        })

        with _patch_mongo(test_db):
            dist, connected = get_road_path_distance(
                "Traders Inc", "NationB", party_a_type="merchant", party_b_type="nation",
            )
        assert connected is True
        assert dist == 2  # cost 1 to enter (1,0), cost 1 to enter (2,0)

    def test_merchant_at_isolated_city_does_not_reach_partner_nation(self, test_db):
        """The key differentiator: this merchant's HOME NATION is connected
        to NationB via its other city, but this merchant itself sits at the
        isolated one — it must NOT inherit the home nation's overall
        connectivity."""
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA2",
        })

        with _patch_mongo(test_db):
            dist, connected = get_road_path_distance(
                "Traders Inc", "NationB", party_a_type="merchant", party_b_type="nation",
            )
        assert connected is False
        assert dist is None

    def test_isolated_merchant_reaches_partner_via_additional_city(self, test_db):
        """Same isolated-primary-city setup as above, but this merchant ALSO
        has an additional_trade_city modifier pointing at NationB's city —
        it should now be reachable via that second presence."""
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA2",
            "modifiers": [{"modifier_type": "additional_trade_city", "city": "cityB1", "value": 1}],
        })

        with _patch_mongo(test_db):
            dist, connected = get_road_path_distance(
                "Traders Inc", "NationB", party_a_type="merchant", party_b_type="nation",
            )
        assert connected is True
        assert dist == 0  # the additional city IS NationB's city

    def test_merchant_with_no_city_behaves_like_its_home_nation(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({"name": "Traders Inc", "location": str(nation_a_id)})

        with _patch_mongo(test_db):
            merchant_dist, merchant_connected = get_road_path_distance(
                "Traders Inc", "NationB", party_a_type="merchant", party_b_type="nation",
            )
            nation_dist, nation_connected = get_road_path_distance("NationA", "NationB")

        assert merchant_connected == nation_connected is True
        assert merchant_dist == nation_dist == 2

    def test_two_merchants_in_the_same_city_can_reach_each_other(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_many([
            {"name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1"},
            {"name": "Caravan Co", "location": str(nation_a_id), "current_city_id": "cityA1"},
        ])
        with _patch_mongo(test_db):
            dist, connected = get_road_path_distance(
                "Traders Inc", "Caravan Co", party_a_type="merchant", party_b_type="merchant",
            )
        assert connected is True
        assert dist == 0


class TestGetConnectablePartiesIncludesMerchants:
    def test_nation_sees_both_nations_and_merchants(self, test_db):
        _insert_basic_map(test_db)
        nation_b_id = test_db["nations"].find_one({"name": "NationB"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_b_id), "current_city_id": "cityB1",
        })

        with _patch_mongo(test_db):
            results = get_connectable_parties("nation", "NationA", 7)

        by_name = {r["name"]: r for r in results}
        assert "NationB" in by_name and by_name["NationB"]["type"] == "nation"
        assert "Traders Inc" in by_name and by_name["Traders Inc"]["type"] == "merchant"
        assert by_name["Traders Inc"]["road_distance"] == 2

    def test_merchant_can_see_nations_as_a_source_party(self, test_db):
        _insert_basic_map(test_db)
        nation_a_id = test_db["nations"].find_one({"name": "NationA"})["_id"]
        test_db["merchants"].insert_one({
            "name": "Traders Inc", "location": str(nation_a_id), "current_city_id": "cityA1",
        })

        with _patch_mongo(test_db):
            results = get_connectable_parties("merchant", "Traders Inc", 20)

        by_name = {r["name"]: r for r in results}
        assert "NationB" in by_name
        assert by_name["NationB"]["road_distance"] == 2
        assert "NationA" in by_name  # its own home nation is a separate, reachable party


class TestNationOnlyPathUnchanged:
    def test_get_connectable_nations_still_nation_only(self, test_db):
        _insert_basic_map(test_db)
        test_db["merchants"].insert_one({"name": "Traders Inc", "current_city_id": "cityA1"})

        with _patch_mongo(test_db):
            results = get_connectable_nations("NationA", 7)

        names = {r["name"] for r in results}
        assert names == {"NationB"}  # merchants never appear in the nation-only path

    def test_get_road_path_distance_defaults_still_nation_only(self, test_db):
        _insert_basic_map(test_db)
        with _patch_mongo(test_db):
            dist, connected = get_road_path_distance("NationA", "NationB")
        assert connected is True
        assert dist == 2
