"""
Tests for helpers.ai_decision_helpers.fix_city_and_capital_placement — the
admin tool that:

1. Enforces a minimum 3-tile hex distance between every pair of cities on
   the map, regardless of owner (including two cities of the SAME nation).
   AI-owned violators are relocated using the same node/admin-range scoring
   the AI uses when building a new city; player-owned cities are NEVER
   moved and are instead flagged for manual review.
2. Recenters each AI nation's capital onto whichever of its own cities is
   most central (lowest sum of hex distance to its other cities, ties
   broken by preferring a city with a resource/magic node), collapsing any
   duplicate or orphaned capital flags. Player nations' capitals are never
   touched, and nomadic nations (cities never touch the map) are skipped.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh
from helpers.hex_map_helpers import hex_distance

MIN_DIST = adh.MIN_CITY_TILE_DISTANCE


def _nation(name, administration=3, government_type="Fallen Monarchy"):
    return {
        "_id": ObjectId(), "name": name, "administration": administration,
        "government_type": government_type,
        "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
        "resource_storage": {}, "jobs": {}, "job_details": {}, "money": 0,
        "money_income": 0, "region": "",
    }


def _tile(q, r, owner, city=None, capital=False, node=None, terrain="plains", district=None, wonder=None):
    return {
        "_id": ObjectId(), "q": q, "r": r, "terrain": terrain, "owner": owner,
        "city": city, "capital": capital, "node": node, "district": district, "wonder": wonder,
    }


def _city(city_id, city_type="generic", name=""):
    return {"id": city_id, "type": city_type, "name": name}


def _grid_tiles(owner, size=4, exclude=()):
    """Every (q, r) in [-size, size]^2 except `exclude`, owned by `owner`."""
    tiles = []
    for q in range(-size, size + 1):
        for r in range(-size, size + 1):
            if (q, r) in exclude:
                continue
            tiles.append(_tile(q, r, owner))
    return tiles


class TestSameNationDistanceViolation:
    def test_second_city_relocates_away_from_the_first(self):
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=5, exclude={(0, 0), (1, 0)})
        tiles.append(_tile(0, 0, "Alpha", city=_city("city_a"), capital=True))
        tiles.append(_tile(1, 0, "Alpha", city=_city("city_b")))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["moved"]) == 1
        moved = report["moved"][0]
        assert moved["nation"] == "Alpha"
        assert moved["id"] == "city_b"
        assert tuple(moved["from"]) == (1, 0)
        new_coord = tuple(moved["to"])
        assert hex_distance(new_coord[0], new_coord[1], 0, 0) >= MIN_DIST
        assert not report["removed"]
        assert not report["flagged_player"]


class TestCrossNationDistanceViolation:
    def test_ai_city_relocates_away_from_player_city_which_stays_put(self):
        player_nation = _nation("PlayerNation")
        ai_nation = _nation("Alpha")
        player_tiles = _grid_tiles("PlayerNation", size=2)
        player_tiles.append(_tile(0, 0, "PlayerNation", city=_city("player_city"), capital=True))
        ai_tiles = _grid_tiles("Alpha", size=5, exclude={(0, 0), (1, 0)})
        ai_tiles.append(_tile(1, 0, "Alpha", city=_city("ai_city")))
        tiles_by_owner = {"PlayerNation": player_tiles, "Alpha": ai_tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[player_nation, ai_nation],
            player_nation_ids={player_nation["_id"]},
        )

        assert len(report["moved"]) == 1
        assert report["moved"][0]["nation"] == "Alpha"
        assert report["moved"][0]["id"] == "ai_city"
        assert not report["flagged_player"], "player city should not be flagged once the AI side moved away"


class TestPlayerVsPlayerViolationIsOnlyFlagged:
    def test_neither_player_city_moves_both_are_flagged(self):
        p1 = _nation("PlayerOne")
        p2 = _nation("PlayerTwo")
        tiles1 = _grid_tiles("PlayerOne", size=2)
        tiles1.append(_tile(0, 0, "PlayerOne", city=_city("p1_city"), capital=True))
        tiles2 = _grid_tiles("PlayerTwo", size=2)
        tiles2.append(_tile(1, 0, "PlayerTwo", city=_city("p2_city"), capital=True))
        tiles_by_owner = {"PlayerOne": tiles1, "PlayerTwo": tiles2}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[p1, p2], player_nation_ids={p1["_id"], p2["_id"]},
        )

        assert not report["moved"]
        assert not report["removed"]
        flagged_ids = {f["id"] for f in report["flagged_player"]}
        assert flagged_ids == {"p1_city", "p2_city"}


class TestRemovedAndRefundedWhenNoLegalTileExists:
    def test_ai_city_is_removed_and_refund_computed_in_dry_run(self):
        """Alpha's entire territory is just the two conflicting city tiles —
        there's no empty tile anywhere for city_b to relocate to, so it's
        removed instead of left in its illegal spot. dry_run=True must still
        compute (but not persist) the refund."""
        alpha = _nation("Alpha")
        tiles = [
            _tile(0, 0, "Alpha", city=_city("city_a"), capital=True),
            _tile(1, 0, "Alpha", city=_city("city_b", city_type="generic")),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert not report["moved"]
        assert len(report["removed"]) == 1
        removed = report["removed"][0]
        assert removed["id"] == "city_b"
        assert removed["refunded"] == {"wood": 10, "stone": 6, "food": 6}  # generic city's cost
        # dry_run: nothing actually written to the tile dict.
        assert tiles[1].get("city") is not None

    def test_dry_run_false_removes_from_map_nation_and_refunds_resources(self, test_db):
        alpha_id = ObjectId()
        alpha = {
            "_id": alpha_id, "name": "Alpha", "administration": 3,
            "government_type": "Fallen Monarchy",
            "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
            "resource_storage": {"wood": 5, "stone": 0}, "jobs": {}, "job_details": {},
            "money": 0, "money_income": 0, "region": "",
            "cities": [
                {"_id": "city_a", "type": "generic", "name": ""},
                {"_id": "city_b", "type": "generic", "name": ""},
            ],
        }
        test_db["nations"].insert_one(alpha)

        raw_tiles = [
            _tile(0, 0, "Alpha", city=_city("city_a"), capital=True),
            _tile(1, 0, "Alpha", city=_city("city_b", city_type="generic")),
        ]
        for t in raw_tiles:
            test_db["hex_map_tiles"].insert_one(t)
        tiles_by_owner = {"Alpha": list(test_db["hex_map_tiles"].find({"owner": "Alpha"}))}

        with patch.object(adh, "mongo", MagicMock(db=test_db)):
            report = adh.fix_city_and_capital_placement(
                dry_run=False, tiles_by_owner=tiles_by_owner,
                all_nations=[alpha], player_nation_ids=set(),
            )

        assert len(report["removed"]) == 1
        assert report["removed"][0]["refunded"] == {"wood": 10, "stone": 6, "food": 6}

        # Removed from the map tile.
        tile_b = test_db["hex_map_tiles"].find_one({"q": 1, "r": 0})
        assert not tile_b.get("city")

        # Removed from the nation's cities array.
        updated_nation = test_db["nations"].find_one({"_id": alpha_id})
        remaining_ids = {c["_id"] for c in updated_nation.get("cities", [])}
        assert remaining_ids == {"city_a"}

        # Refunded into resource_storage (existing 5 wood + 10 refunded = 15; no cap set).
        assert updated_nation["resource_storage"]["wood"] == 15
        assert updated_nation["resource_storage"]["stone"] == 6
        assert updated_nation["resource_storage"]["food"] == 6

    def test_capped_refund_does_not_exceed_resource_capacity(self, test_db):
        """A player city anchors first regardless of alphabetical order, so
        Alpha's lone city (its only owned tile — nowhere to relocate to) is
        the one forced into the "no legal tile" removal path here."""
        alpha_id = ObjectId()
        alpha = {
            "_id": alpha_id, "name": "Alpha", "administration": 3,
            "government_type": "Fallen Monarchy",
            "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
            "resource_storage": {"wood": 5}, "nation_resource_capacity": {"wood": 8},
            "jobs": {}, "job_details": {}, "money": 0, "money_income": 0, "region": "",
            "cities": [{"_id": "city_a", "type": "generic", "name": ""}],
        }
        test_db["nations"].insert_one(alpha)
        player = {"_id": ObjectId(), "name": "PlayerNation"}

        player_tiles = [_tile(0, 0, "PlayerNation", city=_city("player_city", city_type="generic"), capital=True)]
        alpha_tiles = [_tile(1, 0, "Alpha", city=_city("city_a", city_type="generic"))]
        for t in player_tiles + alpha_tiles:
            test_db["hex_map_tiles"].insert_one(t)
        tiles_by_owner = {"PlayerNation": player_tiles, "Alpha": alpha_tiles}

        with patch.object(adh, "mongo", MagicMock(db=test_db)):
            report = adh.fix_city_and_capital_placement(
                dry_run=False, tiles_by_owner=tiles_by_owner,
                all_nations=[alpha, player], player_nation_ids={player["_id"]},
            )

        assert len(report["removed"]) == 1
        assert report["removed"][0]["id"] == "city_a"
        updated_nation = test_db["nations"].find_one({"_id": alpha_id})
        assert updated_nation["resource_storage"]["wood"] == 8  # capped, not 5 + 10 = 15


class TestCapitalRecentering:
    def test_capital_moves_to_the_most_central_city(self):
        alpha = _nation("Alpha")
        # Cities along a line: (-6,0), (0,0), (6,0). (0,0) is most central.
        tiles = [
            _tile(-6, 0, "Alpha", city=_city("west"), capital=True),
            _tile(0, 0, "Alpha", city=_city("center")),
            _tile(6, 0, "Alpha", city=_city("east")),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["capitals_recentered"]) == 1
        rec = report["capitals_recentered"][0]
        assert rec["nation"] == "Alpha"
        assert tuple(rec["new_capital"]) == (0, 0)
        assert rec["previous_capitals"] == [[-6, 0]]

    def test_ties_prefer_a_city_with_a_node(self):
        alpha = _nation("Alpha")
        # With exactly 2 cities, centrality (sum of distance to the nation's
        # OTHER cities) is always tied between them — the node tie-break
        # is what decides it.
        tiles = [
            _tile(-3, 0, "Alpha", city=_city("no_node"), capital=True),
            _tile(3, 0, "Alpha", city=_city("has_node"), node={"resource_type": "iron"}),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert tuple(rec["new_capital"]) == (3, 0)

    def test_duplicate_capitals_collapse_to_one(self):
        alpha = _nation("Alpha")
        tiles = [
            _tile(0, 0, "Alpha", city=_city("a"), capital=True),
            _tile(10, 0, "Alpha", city=_city("b"), capital=True),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["capitals_recentered"]) == 1
        rec = report["capitals_recentered"][0]
        assert rec["had_duplicates"] is True
        assert sorted(rec["previous_capitals"]) == [[0, 0], [10, 0]]

    def test_already_correct_single_capital_is_left_alone(self):
        alpha = _nation("Alpha")
        tiles = [_tile(0, 0, "Alpha", city=_city("only"), capital=True)]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert report["capitals_recentered"] == []

    def test_player_nation_capital_is_never_touched(self):
        player = _nation("PlayerNation")
        tiles = [
            _tile(-6, 0, "PlayerNation", city=_city("west"), capital=True),
            _tile(0, 0, "PlayerNation", city=_city("center")),
            _tile(6, 0, "PlayerNation", city=_city("east")),
        ]
        tiles_by_owner = {"PlayerNation": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[player], player_nation_ids={player["_id"]},
        )

        assert report["capitals_recentered"] == []

    def test_nomadic_nation_is_skipped(self):
        """Nomadic nations' cities never touch the map — nothing here to
        recenter even though capital flags exist on paper."""
        from app_core import category_data
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        nomadic_gov = next((k for k, v in laws.items() if v.get("nomadic", 0) > 0), None)
        assert nomadic_gov, "expected at least one nomadic government type in nations.json"

        alpha = _nation("Alpha", government_type=nomadic_gov)
        tiles = [
            _tile(-6, 0, "Alpha", city=_city("west"), capital=True),
            _tile(0, 0, "Alpha", city=_city("center")),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert report["capitals_recentered"] == []


class TestNationsWithNoCitiesStillGetACapital:
    def test_existing_single_capital_with_no_city_is_left_alone(self):
        """A brand-new AI nation that hasn't built a city yet already has
        exactly one capital tile — nothing to fix."""
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=2, exclude={(0, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert report["capitals_recentered"] == []

    def test_duplicate_capitals_with_no_city_collapse_to_one_kept(self):
        """Two capital-flagged tiles, neither with a city — previously ALL
        capital flags got wiped in this case; now exactly one must survive
        (the lowest-coordinate one, for determinism)."""
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=3, exclude={(0, 0), (2, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True))
        tiles.append(_tile(2, 0, "Alpha", capital=True))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["capitals_recentered"]) == 1
        rec = report["capitals_recentered"][0]
        assert rec["had_duplicates"] is True
        assert rec["new_capital"] == [0, 0]
        assert sorted(rec["previous_capitals"]) == [[0, 0], [2, 0]]

    def test_no_capital_and_no_city_gets_one_designated(self):
        """A nation with zero cities AND zero capital would otherwise never
        be able to place its very first building (_compute_legal_placement
        can only bootstrap from an existing capital or building) — this is
        exactly the state left behind by removing a nation's only city for
        having nowhere legal to relocate to (see the distance-rule pass)."""
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=2)  # no capital, no city anywhere
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["capitals_recentered"]) == 1
        rec = report["capitals_recentered"][0]
        assert rec["previous_capitals"] == []
        assert rec["new_capital"] is not None
        assert rec["had_duplicates"] is False

    def test_fallback_capital_prefers_a_node_among_equally_central_tiles(self):
        alpha = _nation("Alpha")
        # Two tiles, equally central to each other (any 2-tile set ties);
        # only one has a node.
        tiles = [
            _tile(-1, 0, "Alpha", node=None),
            _tile(1, 0, "Alpha", node={"resource_type": "iron"}),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] == [1, 0]

    def test_fallback_capital_never_picked_on_water(self):
        alpha = _nation("Alpha")
        tiles = [
            _tile(0, 0, "Alpha", terrain="deep_water"),
            _tile(1, 0, "Alpha", terrain="plains"),
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] == [1, 0]

    def test_nation_that_owns_no_tiles_at_all_is_skipped(self):
        alpha = _nation("Alpha")
        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner={"Alpha": []},
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert report["capitals_recentered"] == []

    def test_dry_run_false_persists_the_designated_capital(self, test_db):
        alpha_id = ObjectId()
        alpha = {
            "_id": alpha_id, "name": "Alpha", "administration": 3,
            "government_type": "Fallen Monarchy",
            "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
            "resource_storage": {}, "jobs": {}, "job_details": {}, "money": 0,
            "money_income": 0, "region": "",
        }
        test_db["nations"].insert_one(alpha)
        raw_tiles = _grid_tiles("Alpha", size=2)
        for t in raw_tiles:
            test_db["hex_map_tiles"].insert_one(t)
        tiles_by_owner = {"Alpha": list(test_db["hex_map_tiles"].find({"owner": "Alpha"}))}

        with patch.object(adh, "mongo", MagicMock(db=test_db)):
            report = adh.fix_city_and_capital_placement(
                dry_run=False, tiles_by_owner=tiles_by_owner,
                all_nations=[alpha], player_nation_ids=set(),
            )

        rec = report["capitals_recentered"][0]
        new_q, new_r = rec["new_capital"]
        tile = test_db["hex_map_tiles"].find_one({"q": new_q, "r": new_r})
        assert tile.get("capital") is True
        assert test_db["hex_map_tiles"].count_documents({"owner": "Alpha", "capital": True}) == 1


class TestCapitalNeverPlacedOnAnExistingDistrict:
    """A capital hex may only ever be built on with a city, never a
    district (_compute_legal_placement's own rule) — so a capital-flagged
    tile that already has a district on it is invalid and must be moved,
    even when it's the nation's only capital and there are no cities at
    all. This only matters in the "capital exists, no cities" branch of
    Pass 2 — once a nation has any city, capital recentering only ever
    picks among its own city tiles, none of which could hold a district."""

    def test_single_capital_sitting_on_a_district_is_moved_to_a_clean_tile(self):
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=2, exclude={(0, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True, district={"id": "d1", "def_key": "farm"}))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        assert len(report["capitals_recentered"]) == 1
        rec = report["capitals_recentered"][0]
        assert rec["previous_capitals"] == [[0, 0]]
        assert rec["new_capital"] != [0, 0]

    def test_single_capital_on_a_wonder_is_also_moved(self):
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=2, exclude={(0, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True, wonder={"id": "w1", "def_key": "great_library"}))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] != [0, 0]

    def test_duplicate_capitals_prefers_the_one_without_a_district(self):
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=3, exclude={(0, 0), (2, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True, district={"id": "d1", "def_key": "farm"}))
        tiles.append(_tile(2, 0, "Alpha", capital=True))  # clean
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] == [2, 0]
        assert rec["had_duplicates"] is True

    def test_all_duplicate_capitals_on_districts_falls_back_to_a_fresh_tile(self):
        alpha = _nation("Alpha")
        tiles = _grid_tiles("Alpha", size=3, exclude={(0, 0), (2, 0)})
        tiles.append(_tile(0, 0, "Alpha", capital=True, district={"id": "d1", "def_key": "farm"}))
        tiles.append(_tile(2, 0, "Alpha", capital=True, district={"id": "d2", "def_key": "mine"}))
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] not in ([0, 0], [2, 0])
        assert rec["had_duplicates"] is True

    def test_no_capital_at_all_never_falls_back_onto_a_district_tile(self):
        """The general fallback picker (no capital, no city at all) must
        also skip district/wonder tiles, not just water."""
        alpha = _nation("Alpha")
        tiles = [
            _tile(0, 0, "Alpha", district={"id": "d1", "def_key": "farm"}),
            _tile(1, 0, "Alpha"),  # only genuinely empty tile
        ]
        tiles_by_owner = {"Alpha": tiles}

        report = adh.fix_city_and_capital_placement(
            dry_run=True, tiles_by_owner=tiles_by_owner,
            all_nations=[alpha], player_nation_ids=set(),
        )

        rec = report["capitals_recentered"][0]
        assert rec["new_capital"] == [1, 0]

    def test_dry_run_false_persists_moving_off_a_district_tile(self, test_db):
        alpha_id = ObjectId()
        alpha = {
            "_id": alpha_id, "name": "Alpha", "administration": 3,
            "government_type": "Fallen Monarchy",
            "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
            "resource_storage": {}, "jobs": {}, "job_details": {}, "money": 0,
            "money_income": 0, "region": "",
        }
        test_db["nations"].insert_one(alpha)
        raw_tiles = _grid_tiles("Alpha", size=2, exclude={(0, 0)})
        raw_tiles.append(_tile(0, 0, "Alpha", capital=True, district={"id": "d1", "def_key": "farm"}))
        for t in raw_tiles:
            test_db["hex_map_tiles"].insert_one(t)
        tiles_by_owner = {"Alpha": list(test_db["hex_map_tiles"].find({"owner": "Alpha"}))}

        with patch.object(adh, "mongo", MagicMock(db=test_db)):
            report = adh.fix_city_and_capital_placement(
                dry_run=False, tiles_by_owner=tiles_by_owner,
                all_nations=[alpha], player_nation_ids=set(),
            )

        rec = report["capitals_recentered"][0]
        new_q, new_r = rec["new_capital"]
        assert (new_q, new_r) != (0, 0)

        old_tile = test_db["hex_map_tiles"].find_one({"q": 0, "r": 0})
        assert not old_tile.get("capital")
        assert old_tile.get("district")  # the district itself is untouched

        new_tile = test_db["hex_map_tiles"].find_one({"q": new_q, "r": new_r})
        assert new_tile.get("capital") is True
        assert not new_tile.get("district")


class TestApplyWritesToTheDatabase:
    def test_dry_run_false_persists_the_move_and_capital_change(self, test_db):
        alpha = {
            "_id": ObjectId(), "name": "Alpha", "administration": 3,
            "government_type": "Fallen Monarchy",
            "resource_production": {}, "resource_consumption": {}, "resource_excess": {},
            "resource_storage": {}, "jobs": {}, "job_details": {}, "money": 0,
            "money_income": 0, "region": "",
        }
        test_db["nations"].insert_one(alpha)

        raw_tiles = _grid_tiles("Alpha", size=5, exclude={(0, 0), (1, 0)})
        raw_tiles.append(_tile(0, 0, "Alpha", city=_city("city_a"), capital=True))
        raw_tiles.append(_tile(1, 0, "Alpha", city=_city("city_b")))
        for t in raw_tiles:
            test_db["hex_map_tiles"].insert_one(t)
        tiles_by_owner = {"Alpha": list(test_db["hex_map_tiles"].find({"owner": "Alpha"}))}

        with patch.object(adh, "mongo", MagicMock(db=test_db)):
            report = adh.fix_city_and_capital_placement(
                dry_run=False, tiles_by_owner=tiles_by_owner,
                all_nations=[alpha], player_nation_ids=set(),
            )

        assert len(report["moved"]) == 1
        old_tile = test_db["hex_map_tiles"].find_one({"q": 1, "r": 0})
        assert not old_tile.get("city")
        new_q, new_r = report["moved"][0]["to"]
        new_tile = test_db["hex_map_tiles"].find_one({"q": new_q, "r": new_r})
        assert new_tile["city"]["id"] == "city_b"

        if report["capitals_recentered"]:
            rec = report["capitals_recentered"][0]
            new_cap_q, new_cap_r = rec["new_capital"]
            cap_tile = test_db["hex_map_tiles"].find_one({"q": new_cap_q, "r": new_cap_r})
            assert cap_tile.get("capital") is True
