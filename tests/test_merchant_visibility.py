"""
Tests for merchant company visibility, based on the nation a merchant is
currently stationed in (merchant["location"]):

  - The host nation always has full (tier 4) visibility of a merchant
    stationed within it.
  - Any other nation's visibility into the merchant shares the host
    nation's *structural* baseline exposure to that viewer (region /
    overlord-vassal / shared-market / diplomatic-pact bonus, plus the
    viewer's own offensive visibility reach) — but is NOT affected by the
    host nation's own visibility_modifiers (its own investments in secrecy
    or reach don't automatically cover a merchant company just because it's
    stationed there).
  - The merchant's OWN visibility_modifiers (added the same generic way a
    nation's are, via its `modifiers` array) apply instead — most notably
    its own defensive visibility.

See calculations/visibility.py's compute_merchant_visibility.
"""
from bson import ObjectId

from calculations.visibility import compute_merchant_visibility, collect_visibility_modifiers
from helpers.visibility_helpers import is_item_owner
from app_core import json_data


def _patch_mongo(test_db):
    from unittest.mock import patch
    return patch("calculations.visibility.mongo", **{"db": test_db})


class TestComputeMerchantVisibilityHostNation:
    def test_host_nation_has_full_visibility(self, test_db):
        host_id = ObjectId()
        host_nation = {"_id": host_id, "region": None, "overlord": None}
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(host_nation, merchant)
        assert tier == 4

    def test_no_location_gives_zero(self, test_db):
        viewer_nation = {"_id": ObjectId(), "region": None, "overlord": None}
        merchant = {"visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 0


class TestComputeMerchantVisibilitySharesHostBaseline:
    def test_shares_hosts_region_bonus(self, test_db):
        viewer_id = ObjectId()
        host_id = ObjectId()
        region_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": str(region_id), "overlord": None}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": str(region_id), "overlord": None,
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 1

    def test_shares_hosts_vassal_overlord_bonus(self, test_db):
        viewer_id = ObjectId()
        host_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": None, "overlord": str(viewer_id),
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 2

    def test_no_relation_gives_zero(self, test_db):
        viewer_id = ObjectId()
        host_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": None, "overlord": None,
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 0


class TestComputeMerchantVisibilityIgnoresHostsOwnModifiers:
    def test_hosts_own_defensive_modifier_does_not_reduce_merchant_visibility(self, test_db):
        """The host nation's own counterintelligence investment protects
        itself, not a merchant company just passing through."""
        viewer_id = ObjectId()
        host_id = ObjectId()
        region_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": str(region_id), "overlord": None}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": str(region_id), "overlord": None,
            "visibility_modifiers": [{"type": "defensive", "value": -5, "source": "Spy Network"}],
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 1  # region bonus only — host's -5 defensive mod ignored

    def test_hosts_own_offensive_modifier_does_not_affect_merchant_visibility(self, test_db):
        """An offensive modifier is a viewer capability, not something that
        would ever apply from the host's side here — confirms only the
        VIEWER's own offensive modifiers are consulted."""
        viewer_id = ObjectId()
        host_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None, "visibility_modifiers": []}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": None, "overlord": None,
            "visibility_modifiers": [{"type": "offensive", "value": 5, "target_type": "all_nations", "source": "Host's Own Spies"}],
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 0


class TestComputeMerchantVisibilityUsesMerchantsOwnModifiers:
    def test_merchants_own_defensive_modifier_reduces_visibility(self, test_db):
        viewer_id = ObjectId()
        host_id = ObjectId()
        region_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": str(region_id), "overlord": None}
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": str(region_id), "overlord": None,
        })
        merchant = {
            "location": str(host_id),
            "visibility_modifiers": [{"type": "defensive", "value": -1, "source": "Discreet Operations"}],
        }
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 0  # 1 (region) - 1 (merchant's own defensive) = 0

    def test_viewers_own_offensive_modifier_still_applies(self, test_db):
        """The viewer's own spying capability is unrelated to the host
        nation, so it still reaches a merchant stationed there."""
        viewer_id = ObjectId()
        host_id = ObjectId()
        viewer_nation = {
            "_id": viewer_id, "region": None, "overlord": None,
            "visibility_modifiers": [{"type": "offensive", "value": 2, "target_type": "all_nations", "source": "Spy Network"}],
        }
        test_db["nations"].insert_one({
            "_id": host_id, "name": "Host Nation", "region": None, "overlord": None,
        })
        merchant = {"location": str(host_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_merchant_visibility(viewer_nation, merchant)
        assert tier == 2


class TestMerchantModifierTypesAllowVisibilityModifiers:
    def test_offensive_and_defensive_visibility_applicable_to_merchant(self):
        modifier_types = json_data["modifier_types"]
        assert "merchant" in modifier_types["offensive_visibility"]["applicable_to"]
        assert "merchant" in modifier_types["defensive_visibility"]["applicable_to"]


class TestCollectVisibilityModifiersWorksForMerchants:
    def test_extracts_defensive_modifier_from_merchant_modifiers_array(self):
        merchant = {
            "modifiers": [
                {"modifier_type": "defensive_visibility", "value": -2, "source": "Discreet Operations"},
                {"modifier_type": "money_income", "value": 10, "source": "Irrelevant"},
            ],
        }
        result = collect_visibility_modifiers(merchant)
        assert result == [{"type": "defensive", "value": -2, "source": "Discreet Operations"}]


class TestIsItemOwnerForMerchants:
    def test_leader_players_own_the_merchant(self, test_db):
        from unittest.mock import patch
        player_id = ObjectId()
        leader_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({"_id": leader_id, "player": str(player_id)})
        merchant = {"leaders": [str(leader_id)]}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("merchants", merchant, {"id": "user-1"}) is True

    def test_unrelated_player_does_not_own_the_merchant(self, test_db):
        from unittest.mock import patch
        player_id = ObjectId()
        other_player_id = ObjectId()
        leader_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["players"].insert_one({"_id": other_player_id, "id": "user-2"})
        test_db["characters"].insert_one({"_id": leader_id, "player": str(other_player_id)})
        merchant = {"leaders": [str(leader_id)]}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("merchants", merchant, {"id": "user-1"}) is False

    def test_no_leaders_means_no_owner(self, test_db):
        from unittest.mock import patch
        player_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        merchant = {"leaders": []}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("merchants", merchant, {"id": "user-1"}) is False
