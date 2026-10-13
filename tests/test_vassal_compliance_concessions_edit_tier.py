"""
Regression test for a recurring report: overlords couldn't submit a
change request to a vassal's compliance or concessions — the edit just
silently didn't take, with no error.

Root cause: NATION_EDIT_FIELD_TIERS (helpers/visibility_helpers.py) required
tier 3 to edit "compliance"/"concessions", but an overlord with NO other
relationship to their vassal (no shared region/market/pact) only ever
reaches tier 2 — see calculations/visibility.py's
_structural_relationship_bonus, which grants exactly +2 for the overlord/
vassal relationship alone (confirmed by the existing
TestComputeVisibilityVassalOverlord::test_target_is_vassal_of_viewer_gives_
plus_two, unchanged by this fix). strip_form_data_to_tier silently dropped
these two fields from the submitted form data before request_change was
ever called whenever the overlord's visibility_level landed at exactly 2
— i.e. whenever they had no OTHER, coincidental relationship bonus to push
them to 3. Fixed by lowering both fields to tier 2, matching the
dedicated overlord/vassal bonus exactly rather than requiring it to
combine with some unrelated, coincidental source of visibility.
"""
from bson import ObjectId
from unittest.mock import patch

from calculations.visibility import compute_visibility
from helpers.visibility_helpers import strip_form_data_to_tier, NATION_EDIT_FIELD_TIERS


class TestOverlordAlonePovidesExactlyTierTwo:
    def test_plain_overlord_relationship_with_no_other_bonus_is_tier_two(self, test_db):
        """Documents the exact scenario that broke: an overlord with no
        shared region/market/pact with this specific vassal."""
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None}
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Vassal Nation", "region": None,
            "overlord": str(viewer_id), "visibility_modifiers": [],
        })
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            tier = compute_visibility(viewer_nation, str(target_id))
        assert tier == 2


class TestStripFormDataToTierPreservesComplianceAndConcessionsAtTierTwo:
    def test_compliance_and_concessions_survive_at_the_overlords_actual_tier(self):
        form_data = {"compliance": "Loyal", "concessions": {"food": 10}, "name": "Vassal Nation"}
        result = strip_form_data_to_tier(form_data, tier=2)
        assert result == form_data

    def test_overlord_field_itself_still_requires_tier_three(self):
        """Unaffected by this fix — reassigning WHO a nation's overlord is
        stays a higher-trust action than managing an existing vassal
        relationship's compliance/concessions."""
        form_data = {"overlord": "some-nation-id"}
        assert strip_form_data_to_tier(form_data, tier=2) == {}
        assert strip_form_data_to_tier(form_data, tier=3) == form_data

    def test_tier_assignments_are_exactly_two(self):
        assert NATION_EDIT_FIELD_TIERS["compliance"] == 2
        assert NATION_EDIT_FIELD_TIERS["concessions"] == 2
