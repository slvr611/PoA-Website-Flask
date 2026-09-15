"""
Tests for mercenary company visibility, based on the region a mercenary
company is currently stationed in (mercenary["region"]):

  - Any viewer nation sharing the mercenary's region gets a flat tier-1
    baseline — mirroring nations' own same-region +1 bonus in
    _structural_relationship_bonus — plus the viewer's own offensive
    visibility reach.
  - Unlike merchants, there's no single "host nation" that always sees a
    mercenary company at full (tier 4) visibility — a mercenary's "patron"
    merely employs it, it doesn't own/contain it the way a merchant's
    location nation does.
  - The mercenary's OWN visibility_modifiers (added the same generic way a
    nation's/merchant's are, via its `modifiers` array) apply — most
    notably its own defensive visibility.

See calculations/visibility.py's compute_mercenary_visibility.
"""
from bson import ObjectId
from unittest.mock import patch

from calculations.visibility import compute_mercenary_visibility, collect_visibility_modifiers
from helpers.visibility_helpers import is_item_owner, get_nation_id_for_item
from app_core import json_data


def _patch_mongo(test_db):
    return patch("calculations.visibility.mongo", **{"db": test_db})


class TestComputeMercenaryVisibilitySameRegionBaseline:
    def test_shares_region_grants_tier_1(self, test_db):
        region_id = ObjectId()
        viewer_nation = {"_id": ObjectId(), "region": str(region_id), "overlord": None}
        mercenary = {"region": str(region_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 1

    def test_different_region_grants_zero(self, test_db):
        viewer_nation = {"_id": ObjectId(), "region": str(ObjectId()), "overlord": None}
        mercenary = {"region": str(ObjectId()), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 0

    def test_no_region_on_either_side_grants_zero(self, test_db):
        viewer_nation = {"_id": ObjectId(), "region": None, "overlord": None}
        mercenary = {"region": None, "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 0

    def test_highest_tier_across_multiple_viewer_nations(self, test_db):
        region_id = ObjectId()
        same_region_viewer = {"_id": ObjectId(), "region": str(region_id), "overlord": None}
        other_region_viewer = {"_id": ObjectId(), "region": str(ObjectId()), "overlord": None}
        mercenary = {"region": str(region_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility([other_region_viewer, same_region_viewer], mercenary)
        assert tier == 1


class TestComputeMercenaryVisibilityModifiers:
    def test_viewers_own_offensive_modifier_adds_on_top_of_region_baseline(self, test_db):
        region_id = ObjectId()
        viewer_nation = {
            "_id": ObjectId(), "region": str(region_id), "overlord": None,
            "visibility_modifiers": [{"type": "offensive", "value": 2, "target_type": "all_nations", "source": "Spy Network"}],
        }
        mercenary = {"region": str(region_id), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 3  # 1 (region) + 2 (offensive)

    def test_offensive_modifier_alone_can_reach_a_mercenary_outside_the_region(self, test_db):
        viewer_nation = {
            "_id": ObjectId(), "region": str(ObjectId()), "overlord": None,
            "visibility_modifiers": [{"type": "offensive", "value": 2, "target_type": "all_nations", "source": "Spy Network"}],
        }
        mercenary = {"region": str(ObjectId()), "visibility_modifiers": []}
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 2

    def test_mercenarys_own_defensive_modifier_reduces_visibility(self, test_db):
        region_id = ObjectId()
        viewer_nation = {"_id": ObjectId(), "region": str(region_id), "overlord": None}
        mercenary = {
            "region": str(region_id),
            "visibility_modifiers": [{"type": "defensive", "value": -1, "source": "Discreet Operations"}],
        }
        with _patch_mongo(test_db):
            tier = compute_mercenary_visibility(viewer_nation, mercenary)
        assert tier == 0  # 1 (region) - 1 (own defensive) = 0

    def test_tier_never_goes_below_zero_or_above_four(self, test_db):
        region_id = ObjectId()
        viewer_nation = {"_id": ObjectId(), "region": str(region_id), "overlord": None}
        overly_hidden = {
            "region": str(region_id),
            "visibility_modifiers": [{"type": "defensive", "value": -99, "source": "Ghosts"}],
        }
        overly_exposed = {
            "region": str(region_id),
            "visibility_modifiers": [{"type": "defensive", "value": 99, "source": "Loudmouths"}],
        }
        with _patch_mongo(test_db):
            assert compute_mercenary_visibility(viewer_nation, overly_hidden) == 0
            assert compute_mercenary_visibility(viewer_nation, overly_exposed) == 4


class TestMercenaryModifierTypesAllowVisibilityModifiers:
    def test_offensive_and_defensive_visibility_applicable_to_mercenary(self):
        modifier_types = json_data["modifier_types"]
        assert "mercenary" in modifier_types["offensive_visibility"]["applicable_to"]
        assert "mercenary" in modifier_types["defensive_visibility"]["applicable_to"]


class TestCollectVisibilityModifiersWorksForMercenaries:
    def test_extracts_defensive_modifier_from_mercenary_modifiers_array(self):
        mercenary = {
            "modifiers": [
                {"modifier_type": "defensive_visibility", "value": -2, "source": "Discreet Operations"},
                {"modifier_type": "money_income", "value": 10, "source": "Irrelevant"},
            ],
        }
        result = collect_visibility_modifiers(mercenary)
        assert result == [{"type": "defensive", "value": -2, "source": "Discreet Operations"}]


class TestIsItemOwnerForMercenaries:
    def test_leader_players_own_the_mercenary(self, test_db):
        player_id = ObjectId()
        leader_id = ObjectId()
        mercenary_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({
            "_id": leader_id, "player": str(player_id), "ruling_nation_org": str(mercenary_id),
        })
        mercenary = {"_id": mercenary_id, "leaders": []}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("mercenaries", mercenary, {"id": "user-1"}) is True

    def test_unrelated_player_does_not_own_the_mercenary(self, test_db):
        player_id = ObjectId()
        other_player_id = ObjectId()
        leader_id = ObjectId()
        mercenary_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["players"].insert_one({"_id": other_player_id, "id": "user-2"})
        test_db["characters"].insert_one({
            "_id": leader_id, "player": str(other_player_id), "ruling_nation_org": str(mercenary_id),
        })
        mercenary = {"_id": mercenary_id, "leaders": []}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("mercenaries", mercenary, {"id": "user-1"}) is False

    def test_no_leaders_means_no_owner(self, test_db):
        player_id = ObjectId()
        mercenary_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        mercenary = {"_id": mercenary_id, "leaders": []}
        with patch("helpers.visibility_helpers.mongo", **{"db": test_db}):
            assert is_item_owner("mercenaries", mercenary, {"id": "user-1"}) is False


class TestGetNationIdForItemRegionResolution:
    """Mercenaries resolve via their region, not a parent nation — used only
    as a has-a-placement truthy check by get_item_visibility, which then
    dispatches to compute_mercenary_visibility rather than compute_visibility."""

    def test_returns_region_id_when_present(self):
        region_id = ObjectId()
        mercenary = {"_id": ObjectId(), "region": str(region_id)}
        assert get_nation_id_for_item("mercenaries", mercenary) == str(region_id)

    def test_returns_none_when_no_region(self):
        mercenary = {"_id": ObjectId(), "region": None}
        assert get_nation_id_for_item("mercenaries", mercenary) is None
