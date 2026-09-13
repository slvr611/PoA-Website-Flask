"""
compute_merchant_income (calculations/compute_functions.py) — the merchant
equivalent of compute_money_income's trade-route wiring, without the
nation-only pop_count/undead-horde/bandit-camp pieces that don't apply to
merchant companies.
"""
from unittest.mock import patch

import calculations.compute_functions as cf


class TestComputeMerchantIncome:
    def test_base_value_plus_modifiers_with_no_routes(self):
        target = {"name": "Traders Inc"}
        with patch("helpers.trade_route_helpers._get_cached_routes", return_value=None):
            value = cf.compute_merchant_income("income", target, 200, {}, {})
        assert value == 200

    def test_reputation_law_value_flows_through_overall_total_modifiers(self):
        """The reputation law's income value (e.g. "Acceptable": income=200)
        is merged into overall_total_modifiers by field_calculations.py
        before compute_merchant_income ever runs — this just confirms the
        function actually adds it in (base_value itself stays 0, since
        merchants' income schema field declares no base_value)."""
        target = {"name": "Traders Inc"}
        with patch("helpers.trade_route_helpers._get_cached_routes", return_value=None):
            value = cf.compute_merchant_income("income", target, 0, {}, {"income": 200})
        assert value == 200

    def test_incoming_trade_route_money_is_added(self):
        target = {"name": "Traders Inc"}
        fake_routes = [{"nation_a": "SomeNation", "nation_b": "Traders Inc"}]
        with patch("helpers.trade_route_helpers._get_cached_routes", return_value=fake_routes), \
             patch("helpers.trade_route_helpers.get_trade_route_resource_net", return_value={"money": 50}):
            value = cf.compute_merchant_income("income", target, 200, {}, {})
        assert value == 250

    def test_outgoing_trade_route_money_is_subtracted(self):
        target = {"name": "Traders Inc"}
        fake_routes = [{"nation_a": "Traders Inc", "nation_b": "SomeNation"}]
        with patch("helpers.trade_route_helpers._get_cached_routes", return_value=fake_routes), \
             patch("helpers.trade_route_helpers.get_trade_route_resource_net", return_value={"money": -30}):
            value = cf.compute_merchant_income("income", target, 200, {}, {})
        assert value == 170

    def test_no_name_skips_trade_route_lookup(self):
        """Matches compute_money_income's own guard — a target with no name
        can't be matched against trade_routes.nation_a/nation_b at all."""
        target = {}
        with patch("helpers.trade_route_helpers._get_cached_routes") as mock_get_routes:
            value = cf.compute_merchant_income("income", target, 200, {}, {})
        mock_get_routes.assert_not_called()
        assert value == 200


class TestMerchantIncomeIsRegisteredForTheIncomeField:
    def test_income_field_dispatches_to_compute_merchant_income(self):
        assert cf.CUSTOM_COMPUTE_FUNCTIONS["income"] is cf.compute_merchant_income
