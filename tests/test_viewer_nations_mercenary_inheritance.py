"""
Regression tests for get_viewer_nations (calculations/visibility.py), the
mercenary-company counterpart to test_viewer_nations_merchant_inheritance.py:
a character ruling a MERCENARY company (not a nation, not a merchant)
previously contributed nothing to the player's viewer_nations at all — unlike
merchant-ruling characters, there was no fallback branch for mercenaries, so
a mercenary company's leader had ZERO baseline visibility into anything,
not even nations/orgs in their own region (requested fix: "nations and orgs
have 1 visibility on other orgs and nations in their region baseline").

Fix: a mercenary company has no single host nation (only a "region" it's
stationed in), but it does have an optional "patron" — the nation currently
employing it — which is the closest equivalent to a merchant's "location".
When a patron is set, the ruling character inherits that patron nation's
viewpoint, same mechanism merchants already use via their host nation.
With no patron (unemployed), there's no nation to inherit from, matching the
existing "merchant with no location" precedent.
"""
from unittest.mock import patch
from bson import ObjectId

from calculations.visibility import get_viewer_nations


def _patch_mongo(test_db):
    return patch("calculations.visibility.mongo", **{"db": test_db})


class TestGetViewerNationsIncludesMercenaryPatronNation:
    def test_character_ruling_a_mercenary_inherits_patrons_nation(self, test_db):
        player_id = ObjectId()
        char_id = ObjectId()
        mercenary_id = ObjectId()
        patron_nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(mercenary_id),
        })
        test_db["mercenaries"].insert_one({
            "_id": mercenary_id, "name": "Blade Company", "patron": str(patron_nation_id),
        })
        test_db["nations"].insert_one({
            "_id": patron_nation_id, "name": "Patron Nation", "region": None, "overlord": None,
        })

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert [n["name"] for n in result] == ["Patron Nation"]

    def test_character_ruling_an_unemployed_mercenary_yields_nothing_for_it(self, test_db):
        player_id = ObjectId()
        char_id = ObjectId()
        mercenary_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(mercenary_id),
        })
        test_db["mercenaries"].insert_one({"_id": mercenary_id, "name": "Blade Company"})  # no patron

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert result == []

    def test_ruling_both_a_nation_and_a_mercenary_gets_both_perspectives(self, test_db):
        player_id = ObjectId()
        char_a = ObjectId()
        char_b = ObjectId()
        nation_id = ObjectId()
        mercenary_id = ObjectId()
        patron_nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_many([
            {"_id": char_a, "player": str(player_id), "ruling_nation_org": str(nation_id)},
            {"_id": char_b, "player": str(player_id), "ruling_nation_org": str(mercenary_id)},
        ])
        test_db["nations"].insert_many([
            {"_id": nation_id, "name": "Ruled Nation", "region": None, "overlord": None},
            {"_id": patron_nation_id, "name": "Mercenary's Patron", "region": None, "overlord": None},
        ])
        test_db["mercenaries"].insert_one({
            "_id": mercenary_id, "name": "Blade Company", "patron": str(patron_nation_id),
        })

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert {n["name"] for n in result} == {"Ruled Nation", "Mercenary's Patron"}

    def test_ruling_both_a_merchant_and_a_mercenary_gets_both_perspectives(self, test_db):
        """Confirms the two org fallback branches (merchant location,
        mercenary patron) compose correctly rather than one clobbering the
        other's lookup."""
        player_id = ObjectId()
        char_a = ObjectId()
        char_b = ObjectId()
        merchant_id = ObjectId()
        mercenary_id = ObjectId()
        merchant_host_id = ObjectId()
        patron_nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_many([
            {"_id": char_a, "player": str(player_id), "ruling_nation_org": str(merchant_id)},
            {"_id": char_b, "player": str(player_id), "ruling_nation_org": str(mercenary_id)},
        ])
        test_db["nations"].insert_many([
            {"_id": merchant_host_id, "name": "Merchant's Host", "region": None, "overlord": None},
            {"_id": patron_nation_id, "name": "Mercenary's Patron", "region": None, "overlord": None},
        ])
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Trading Co", "location": str(merchant_host_id)})
        test_db["mercenaries"].insert_one({
            "_id": mercenary_id, "name": "Blade Company", "patron": str(patron_nation_id),
        })

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert {n["name"] for n in result} == {"Merchant's Host", "Mercenary's Patron"}


class TestMercenaryRulerGetsRegionBaselineViaPatron:
    """End-to-end: once the mercenary ruler inherits their patron's
    viewer_nation, the already-existing same-region +1 baseline (
    calculations.visibility._structural_relationship_bonus) applies exactly
    as it does for any other nation-to-nation view — this just confirms the
    fix actually unblocks that, not only that get_viewer_nations returns
    something."""

    def test_patron_nation_sees_tier_1_into_a_same_region_nation(self, test_db):
        from calculations.visibility import compute_visibility

        player_id = ObjectId()
        char_id = ObjectId()
        mercenary_id = ObjectId()
        patron_nation_id = ObjectId()
        other_nation_id = ObjectId()
        region_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(mercenary_id),
        })
        test_db["mercenaries"].insert_one({
            "_id": mercenary_id, "name": "Blade Company", "patron": str(patron_nation_id),
        })
        test_db["nations"].insert_many([
            {"_id": patron_nation_id, "name": "Patron Nation", "region": str(region_id), "overlord": None},
            {"_id": other_nation_id, "name": "Neighbor Nation", "region": str(region_id), "overlord": None},
        ])

        with _patch_mongo(test_db):
            viewer_nations = get_viewer_nations({"id": "user-1"})
            tier = compute_visibility(viewer_nations, str(other_nation_id))

        assert tier == 1
