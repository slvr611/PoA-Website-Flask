"""
Tests for the "stasis" modifier: nations/characters carrying it should be
skipped by tick functions (except modifier decay, so stasis itself can
expire), have their market demands wiped, and have trade routes pause
(stay active, stop delivering) rather than end.
"""
from unittest.mock import patch

from helpers.tick_helpers import _in_stasis, modifier_decay_tick
from helpers.trade_route_helpers import _nations_in_stasis, get_trade_route_resource_net


def _stasis_modifier(duration=3):
    return {"modifier_type": "stasis", "value": 0, "duration": duration, "source": "test", "_id": "s1"}


class TestInStasis:
    def test_true_when_stasis_modifier_present(self):
        entity = {"modifiers": [_stasis_modifier()]}
        assert _in_stasis(entity) is True

    def test_false_when_no_modifiers(self):
        assert _in_stasis({}) is False
        assert _in_stasis({"modifiers": []}) is False

    def test_false_when_other_modifiers_present(self):
        entity = {"modifiers": [{"modifier_type": "administration", "value": 2, "duration": -1}]}
        assert _in_stasis(entity) is False

    def test_none_entity_is_safe(self):
        assert _in_stasis(None) is False


class TestModifierDecayStillRunsUnderStasis:
    def test_stasis_duration_counts_down(self):
        old_target = {"modifiers": [_stasis_modifier(duration=1)]}
        new_target = {"modifiers": [_stasis_modifier(duration=1)]}
        modifier_decay_tick(old_target, new_target, schema={})
        # duration decremented to 0 -> dropped entirely, stasis has expired
        assert new_target["modifiers"] == []

    def test_stasis_persists_while_duration_remains(self):
        old_target = {"modifiers": [_stasis_modifier(duration=3)]}
        new_target = {"modifiers": [_stasis_modifier(duration=3)]}
        modifier_decay_tick(old_target, new_target, schema={})
        assert len(new_target["modifiers"]) == 1
        assert new_target["modifiers"][0]["duration"] == 2
        assert new_target["modifiers"][0]["modifier_type"] == "stasis"


class TestNationsInStasis:
    def test_returns_only_names_with_stasis_modifier(self, test_db):
        test_db["nations"].insert_many([
            {"name": "Frozen", "modifiers": [_stasis_modifier()]},
            {"name": "Active", "modifiers": [{"modifier_type": "administration", "value": 1}]},
        ])
        with patch("helpers.trade_route_helpers.mongo") as mock_mongo:
            mock_mongo.db = test_db
            result = _nations_in_stasis(["Frozen", "Active", "Nonexistent"])
        assert result == {"Frozen"}

    def test_empty_input_short_circuits_without_query(self, test_db):
        with patch("helpers.trade_route_helpers.mongo") as mock_mongo:
            mock_mongo.db = test_db
            assert _nations_in_stasis([]) == set()
            assert _nations_in_stasis([None]) == set()


class TestTradeRoutePauseDuringStasis:
    def test_route_with_stasis_partner_contributes_nothing(self, test_db):
        test_db["nations"].insert_one({"name": "Frozen", "modifiers": [_stasis_modifier()]})
        routes = [{
            "nation_a": "Frozen", "nation_b": "Active",
            "accepted_session": 1, "delay": 0,
            "resources_a_to_b": [{"resource": "wood", "quantity": 10}],
            "resources_b_to_a": [{"resource": "money", "quantity": 5}],
        }]
        with patch("helpers.trade_route_helpers.mongo") as mock_mongo:
            mock_mongo.db = test_db
            net = get_trade_route_resource_net("Active", routes, session=1)
        assert net == {}

    def test_route_without_stasis_partner_still_delivers(self, test_db):
        routes = [{
            "nation_a": "NationA", "nation_b": "NationB",
            "accepted_session": 1, "delay": 0,
            "resources_a_to_b": [{"resource": "wood", "quantity": 10}],
            "resources_b_to_a": [{"resource": "money", "quantity": 5}],
        }]
        with patch("helpers.trade_route_helpers.mongo") as mock_mongo:
            mock_mongo.db = test_db
            net = get_trade_route_resource_net("NationB", routes, session=1)
        assert net == {"money": -5, "wood": 10}

    def test_route_pauses_but_is_not_mutated(self, test_db):
        """Pausing must not touch the route document itself (no lifecycle/status change)."""
        test_db["nations"].insert_one({"name": "Frozen", "modifiers": [_stasis_modifier()]})
        route = {
            "nation_a": "Frozen", "nation_b": "Active", "status": "active",
            "accepted_session": 1, "delay": 0,
            "resources_a_to_b": [], "resources_b_to_a": [],
        }
        with patch("helpers.trade_route_helpers.mongo") as mock_mongo:
            mock_mongo.db = test_db
            get_trade_route_resource_net("Active", [route], session=1)
        assert route["status"] == "active"
