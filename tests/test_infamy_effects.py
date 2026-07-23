"""
Tests for infamy-tier effects from calculations/compute_functions.py and
routes/war_routes.py, per the infamy design table:

  1-9:    -2 diplomatic check modifier
  10-19:  -4 diplomatic check modifier, 20% stability loss chance, -5 war infamy cost
  20-29:  -6 diplomatic check modifier, 30% stability loss chance, -10 war infamy cost
  30-49:  -8 diplomatic check modifier, 40% stability loss chance, -15 war infamy cost
  50-99:  -10 diplomatic check modifier, (50 + infamy-50)% stability loss chance,
          -25 war infamy cost

Bug fixed here: compute_stability_loss_chance multiplied the infamy
contribution by `1 * stability_loss_chance_from_infamy_mult` (default 0)
instead of `1 + stability_loss_chance_from_infamy_mult` — a typo that meant
infamy contributed ZERO stability loss for every nation except those with the
Extermination diplomatic stance law (the only source of that modifier).
"""
import pytest
from unittest.mock import patch
from bson import ObjectId

from calculations.compute_functions import (
    compute_stability_loss_chance,
    compute_diplomatic_check_modifier,
)
from routes.war_routes import _get_infamy_for_war, _infamy_cost_reduction_for_defender
import helpers.change_helpers as ch


class TestStabilityLossChanceFromInfamy:
    def test_below_10_contributes_nothing(self):
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 9}, 0, {}, {}) == 0

    def test_10_to_19_contributes_20_percent(self):
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 15}, 0, {}, {}) == 0.20

    def test_20_to_29_contributes_30_percent(self):
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 25}, 0, {}, {}) == 0.30

    def test_30_to_49_contributes_40_percent(self):
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 40}, 0, {}, {}) == 0.40

    def test_50_scales_one_percent_per_point_over_50(self):
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 50}, 0, {}, {}) == 0.50
        assert compute_stability_loss_chance("stability_loss_chance", {"infamy": 75}, 0, {}, {}) == 0.75

    def test_applies_without_any_special_modifier_present(self):
        """The actual bug: this must be nonzero with an EMPTY modifiers dict —
        no diplomatic-stance law or anything else required."""
        value = compute_stability_loss_chance("stability_loss_chance", {"infamy": 20}, 0, {}, {})
        assert value > 0

    def test_extermination_style_modifier_increases_the_effect(self):
        """stability_loss_chance_from_infamy_mult is additive on top of the
        always-on base of 1, matching the `1 + get(key, 0)` convention used
        for every other "_mult" modifier in this codebase (trade_slots_mult,
        hiring_cost_mult, etc.) — it must not replace/zero the base effect."""
        base = compute_stability_loss_chance("stability_loss_chance", {"infamy": 20}, 0, {}, {})
        boosted = compute_stability_loss_chance(
            "stability_loss_chance", {"infamy": 20}, 0, {},
            {"stability_loss_chance_from_infamy_mult": 0.5},
        )
        assert boosted == round(base * 1.5, 2)


class TestDiplomaticCheckModifier:
    def test_zero_infamy_no_penalty(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 0}, 0, {}, {}) == 0

    def test_tier_1_to_9(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 5}, 0, {}, {}) == -2

    def test_tier_10_to_19(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 15}, 0, {}, {}) == -4

    def test_tier_20_to_29(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 25}, 0, {}, {}) == -6

    def test_tier_30_to_49(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 45}, 0, {}, {}) == -8

    def test_tier_50_plus(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 60}, 0, {}, {}) == -10

    def test_tier_100_plus_stays_at_max_penalty(self):
        assert compute_diplomatic_check_modifier("diplomatic_check_modifier", {"infamy": 150}, 0, {}, {}) == -10


class TestWarInfamyCostReduction:
    def test_below_10_no_reduction(self):
        assert _infamy_cost_reduction_for_defender(5) == 0

    def test_10_to_19_reduces_5(self):
        assert _infamy_cost_reduction_for_defender(15) == 5

    def test_20_to_29_reduces_10(self):
        assert _infamy_cost_reduction_for_defender(25) == 10

    def test_30_to_49_reduces_15(self):
        assert _infamy_cost_reduction_for_defender(35) == 15

    def test_50_plus_reduces_25(self):
        assert _infamy_cost_reduction_for_defender(60) == 25

    def test_war_cost_never_goes_negative(self):
        # holy_war's base infamy cost is only 10; a high-infamy defender's 25
        # reduction must floor the result at 0, not go negative.
        cost = _get_infamy_for_war("holy_war", defender_infamy=100)
        assert cost >= 0

    def test_full_infamy_cost_reduced_by_defender_tier(self):
        # moderate_war costs 20 infamy normally; a 20-29 infamy defender
        # reduces that by 10.
        assert _get_infamy_for_war("moderate_war", defender_infamy=25) == 10

    def test_zero_infamy_defender_gets_no_discount(self):
        assert _get_infamy_for_war("moderate_war", defender_infamy=0) == 20


_ADMIN_DISCORD_ID = "discord_admin_user"


@pytest.fixture
def patch_change_helpers(mock_mongo, fake_category_data):
    with patch("helpers.change_helpers.mongo", mock_mongo), \
         patch("helpers.change_helpers.category_data", fake_category_data), \
         patch("helpers.change_helpers._calculate_and_attach_fields",
               side_effect=lambda data_type, obj: obj), \
         patch("helpers.change_helpers.propagate_updates", return_value=None):
        yield


@pytest.fixture
def admin_player(test_db):
    return test_db["players"].insert_one({"name": "AdminUser", "id": _ADMIN_DISCORD_ID, "is_admin": True}).inserted_id


class TestMercenaryHiringInfamyGate:
    """approve_change() blocks hiring a mercenary (setting `patron`) when the
    prospective patron nation has 50+ infamy — symmetric to the existing
    market_links join block. Releasing/unhiring (patron -> "") must never be
    blocked, matching "(Does not unhire mercs)" in the design table."""

    def _change(self, test_db, patron_id, before_patron=""):
        merc_id = test_db["mercenaries"].insert_one({"name": "Test Mercs", "patron": before_patron}).inserted_id
        change_id = test_db["changes"].insert_one({
            "target_collection": "mercenaries",
            "target": merc_id,
            "change_type": "Update",
            "before_requested_data": {"patron": before_patron},
            "after_requested_data": {"patron": patron_id},
            "status": "Pending",
        }).inserted_id
        return merc_id, change_id

    def test_hiring_blocked_when_patron_has_50_plus_infamy(self, test_db, admin_player, patch_change_helpers, flask_app):
        nation_id = test_db["nations"].insert_one({"name": "Infamous Nation", "infamy": 60}).inserted_id
        merc_id, change_id = self._change(test_db, str(nation_id))

        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is False
        merc = test_db["mercenaries"].find_one({"_id": merc_id})
        assert merc["patron"] == ""  # unchanged

    def test_hiring_allowed_when_patron_has_low_infamy(self, test_db, admin_player, patch_change_helpers, flask_app):
        nation_id = test_db["nations"].insert_one({"name": "Clean Nation", "infamy": 10}).inserted_id
        merc_id, change_id = self._change(test_db, str(nation_id))

        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True
        merc = test_db["mercenaries"].find_one({"_id": merc_id})
        assert merc["patron"] == str(nation_id)

    def test_unhiring_always_allowed_even_at_high_infamy(self, test_db, admin_player, patch_change_helpers, flask_app):
        """Releasing a mercenary (patron -> "") must never be blocked, even if
        the CURRENT patron has 100+ infamy — matches "Does not unhire mercs"."""
        nation_id = test_db["nations"].insert_one({"name": "Infamous Nation", "infamy": 100}).inserted_id
        merc_id, change_id = self._change(test_db, patron_id="", before_patron=str(nation_id))

        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True
        merc = test_db["mercenaries"].find_one({"_id": merc_id})
        assert merc["patron"] == ""
