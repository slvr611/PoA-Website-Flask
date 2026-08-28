"""
Regression tests for get_viewer_nations (calculations/visibility.py):
a character's ruling_nation_org is polymorphic (nations/merchants/
mercenaries collections) — a character who rules a MERCHANT company (not a
nation) previously contributed nothing to the player's viewer_nations, since
the code blindly treated ruling_nation_org as a nation id and the resulting
id simply never matched anything in the nations collection.

Fix: for a ruling_nation_org id that isn't actually a nation, resolve it as
a merchant and use that merchant's CURRENT location (host nation) instead —
so a merchant-leading player's view of everything else (other nations,
other merchants, etc.) reflects their merchant's host nation's viewpoint,
same principle as a merchant's own page already being fully visible to its
leaders regardless of viewer_nations (see is_item_owner in
tests/test_merchant_visibility.py).
"""
from unittest.mock import patch
from bson import ObjectId

from calculations.visibility import get_viewer_nations


def _patch_mongo(test_db):
    return patch("calculations.visibility.mongo", **{"db": test_db})


class TestGetViewerNationsIncludesMerchantHostNation:
    def test_character_ruling_a_nation_still_works(self, test_db):
        player_id = ObjectId()
        char_id = ObjectId()
        nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(nation_id),
        })
        test_db["nations"].insert_one({"_id": nation_id, "name": "Testland", "region": None, "overlord": None})

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert [n["name"] for n in result] == ["Testland"]

    def test_character_ruling_a_merchant_inherits_hosts_nation(self, test_db):
        player_id = ObjectId()
        char_id = ObjectId()
        merchant_id = ObjectId()
        host_nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(merchant_id),
        })
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Trading Co", "location": str(host_nation_id)})
        test_db["nations"].insert_one({
            "_id": host_nation_id, "name": "Host Nation", "region": None, "overlord": None,
        })

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert [n["name"] for n in result] == ["Host Nation"]

    def test_character_ruling_a_merchant_with_no_location_yields_nothing_for_it(self, test_db):
        player_id = ObjectId()
        char_id = ObjectId()
        merchant_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": char_id, "player": str(player_id), "ruling_nation_org": str(merchant_id),
        })
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Trading Co"})  # no location

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert result == []

    def test_direct_player_attribution_still_works(self, test_db):
        player_id = ObjectId()
        nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "Directly Owned", "region": None, "overlord": None,
            "players": [str(player_id)],
        })

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert [n["name"] for n in result] == ["Directly Owned"]

    def test_ruling_both_a_nation_and_a_merchant_gets_both_perspectives(self, test_db):
        player_id = ObjectId()
        char_a = ObjectId()
        char_b = ObjectId()
        nation_id = ObjectId()
        merchant_id = ObjectId()
        host_nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_many([
            {"_id": char_a, "player": str(player_id), "ruling_nation_org": str(nation_id)},
            {"_id": char_b, "player": str(player_id), "ruling_nation_org": str(merchant_id)},
        ])
        test_db["nations"].insert_many([
            {"_id": nation_id, "name": "Ruled Nation", "region": None, "overlord": None},
            {"_id": host_nation_id, "name": "Merchant's Host", "region": None, "overlord": None},
        ])
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Trading Co", "location": str(host_nation_id)})

        with _patch_mongo(test_db):
            result = get_viewer_nations({"id": "user-1"})

        assert {n["name"] for n in result} == {"Ruled Nation", "Merchant's Host"}
