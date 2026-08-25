"""
Regression tests for compute_money_income's Illicit-market bandit-camp bonus
(calculations/compute_functions.py).

Bug fixed: the bandit-camp count was scoped to EVERY market a nation
belongs to (a nation can hold 2-3 trade_slots simultaneously), not just
Illicit ones, so camps in a non-Illicit market's territory contributed to
the bonus. Separately, the per-camp rate used was
overall_total_modifiers["money_income_per_bandit_camp"] — a value summed
across every Illicit market the nation belongs to (modifier_prefix
"member" in nations.json) — multiplied against a combined camp count
across all those markets, which double-applies the rate whenever a nation
is in more than one Illicit market (200+200=400 per camp instead of 200
per camp, summed across each market's own camps).
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import calculations.compute_functions as cf


_MARKET_SCHEMA = {
    "properties": {
        "market_type": {
            "laws": {
                "Illicit": {"trade_risk": 0.15, "member_nation_money_income_per_bandit_camp": 200},
                "Hub": {"resource_storage_capacity": 5},
            }
        }
    }
}
_FAKE_CATEGORY_DATA = {"markets": {"schema": _MARKET_SCHEMA}}


def _base_target(nation_id):
    return {
        "_id": nation_id,
        "name": "Test Nation",
        "pop_count": 0,
        "money": 0,
    }


def _run(mock_mongo, target, overall_total_modifiers):
    with patch("app_core.mongo", mock_mongo), \
         patch("calculations.compute_functions.category_data", _FAKE_CATEGORY_DATA), \
         patch("helpers.undead_horde_helpers.nation_is_undead_horde", return_value=False), \
         patch("helpers.trade_route_helpers._get_cached_routes", return_value=None):
        return cf.compute_money_income("money_income", target, 0, {}, overall_total_modifiers)


class TestBanditCampIncomeMarketTypeScoping:
    def test_camps_in_non_illicit_market_do_not_contribute(self, mock_mongo, test_db):
        nation_id = ObjectId()
        illicit_market_id = ObjectId()
        hub_market_id = ObjectId()

        test_db.market_links.insert_many([
            {"member": str(nation_id), "market": str(illicit_market_id)},
            {"member": str(nation_id), "market": str(hub_market_id)},
        ])
        test_db.markets.insert_many([
            {"_id": illicit_market_id, "name": "Black Market", "market_type": "Illicit"},
            {"_id": hub_market_id, "name": "Grand Bazaar", "market_type": "Hub"},
        ])
        # 2 camps tied to the Illicit market, 3 tied to the Hub market
        test_db.hex_map_tiles.insert_many([
            {"q": 0, "r": 0, "bandit_camp": {"market": "Black Market"}},
            {"q": 1, "r": 0, "bandit_camp": {"market": "Black Market"}},
            {"q": 2, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}},
            {"q": 3, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}},
            {"q": 4, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}},
        ])

        target = _base_target(nation_id)
        result = _run(mock_mongo, target, {"money_income_per_bandit_camp": 200})

        # Only the 2 camps in the Illicit market should count: 200 * 2 = 400
        assert result == 400

    def test_no_illicit_membership_contributes_nothing_even_with_camps_elsewhere(self, mock_mongo, test_db):
        nation_id = ObjectId()
        hub_market_id = ObjectId()

        test_db.market_links.insert_one({"member": str(nation_id), "market": str(hub_market_id)})
        test_db.markets.insert_one({"_id": hub_market_id, "name": "Grand Bazaar", "market_type": "Hub"})
        test_db.hex_map_tiles.insert_many([
            {"q": 0, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}},
            {"q": 1, "r": 0, "bandit_camp": {"market": "Grand Bazaar"}},
        ])

        target = _base_target(nation_id)
        # No Illicit membership means the law aggregation would never have
        # set this modifier in the first place, but exercise it explicitly
        # at 0 to confirm the gate short-circuits with no DB queries mattering.
        result = _run(mock_mongo, target, {"money_income_per_bandit_camp": 0})
        assert result == 0


class TestBanditCampIncomeMultiMarketRate:
    def test_two_illicit_markets_sum_per_market_contributions_not_double_rate(self, mock_mongo, test_db):
        """The core double-counting bug: being in 2 Illicit markets must
        yield 200*camps_A + 200*camps_B, not 400*(camps_A+camps_B)."""
        nation_id = ObjectId()
        market_a_id = ObjectId()
        market_b_id = ObjectId()

        test_db.market_links.insert_many([
            {"member": str(nation_id), "market": str(market_a_id)},
            {"member": str(nation_id), "market": str(market_b_id)},
        ])
        test_db.markets.insert_many([
            {"_id": market_a_id, "name": "Smugglers' Row", "market_type": "Illicit"},
            {"_id": market_b_id, "name": "Thieves' Den", "market_type": "Illicit"},
        ])
        # 2 camps in market A, 1 camp in market B
        test_db.hex_map_tiles.insert_many([
            {"q": 0, "r": 0, "bandit_camp": {"market": "Smugglers' Row"}},
            {"q": 1, "r": 0, "bandit_camp": {"market": "Smugglers' Row"}},
            {"q": 2, "r": 0, "bandit_camp": {"market": "Thieves' Den"}},
        ])

        target = _base_target(nation_id)
        # Law aggregation across 2 Illicit memberships would set this to 400
        # (200 + 200) — exactly the value that must NOT be used as the rate.
        result = _run(mock_mongo, target, {"money_income_per_bandit_camp": 400})

        # Correct: 200*2 + 200*1 = 600, NOT 400*3 = 1200
        assert result == 600

    def test_single_illicit_market_still_works_as_before(self, mock_mongo, test_db):
        nation_id = ObjectId()
        market_id = ObjectId()

        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.markets.insert_one({"_id": market_id, "name": "Black Market", "market_type": "Illicit"})
        test_db.hex_map_tiles.insert_many([
            {"q": 0, "r": 0, "bandit_camp": {"market": "Black Market"}},
            {"q": 1, "r": 0, "bandit_camp": {"market": "Black Market"}},
            {"q": 2, "r": 0, "bandit_camp": {"market": "Black Market"}},
        ])

        target = _base_target(nation_id)
        result = _run(mock_mongo, target, {"money_income_per_bandit_camp": 200})

        assert result == 600  # 200 * 3


class TestBanditCampIncomeBreakdown:
    """The value and the displayed ledger line item must come from the same
    source (get_bandit_camp_income_contributions) — this is what was
    missing before: compute_money_income's total silently included the
    bonus while the income ledger UI never showed a line for it, making a
    correctly-functioning bonus look broken to players (reported live for
    the nation "Dyeak": Total was 200 higher than every visible line
    summed to, with no line explaining the gap)."""

    def test_breakdown_entry_matches_value_contribution(self, mock_mongo, test_db):
        nation_id = ObjectId()
        market_id = ObjectId()

        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.markets.insert_one({"_id": market_id, "name": "Darklands", "market_type": "Illicit"})
        test_db.hex_map_tiles.insert_one({"q": 0, "r": 0, "bandit_camp": {"market": "Darklands"}})

        with patch("app_core.mongo", mock_mongo), \
             patch("calculations.compute_functions.category_data", _FAKE_CATEGORY_DATA):
            total, contributions = cf.get_bandit_camp_income_contributions(str(nation_id))

        assert total == 200
        assert contributions == [{"label": "Bandit Camp: Darklands (1 camp)", "value": 200}]

    def test_no_illicit_membership_returns_no_contributions(self, mock_mongo, test_db):
        with patch("app_core.mongo", mock_mongo), \
             patch("calculations.compute_functions.category_data", _FAKE_CATEGORY_DATA):
            total, contributions = cf.get_bandit_camp_income_contributions(str(ObjectId()))
        assert total == 0
        assert contributions == []

    def test_plural_label_for_multiple_camps(self, mock_mongo, test_db):
        nation_id = ObjectId()
        market_id = ObjectId()
        test_db.market_links.insert_one({"member": str(nation_id), "market": str(market_id)})
        test_db.markets.insert_one({"_id": market_id, "name": "Darklands", "market_type": "Illicit"})
        test_db.hex_map_tiles.insert_many([
            {"q": 0, "r": 0, "bandit_camp": {"market": "Darklands"}},
            {"q": 1, "r": 0, "bandit_camp": {"market": "Darklands"}},
        ])
        with patch("app_core.mongo", mock_mongo), \
             patch("calculations.compute_functions.category_data", _FAKE_CATEGORY_DATA):
            total, contributions = cf.get_bandit_camp_income_contributions(str(nation_id))
        assert total == 400
        assert contributions == [{"label": "Bandit Camp: Darklands (2 camps)", "value": 400}]
