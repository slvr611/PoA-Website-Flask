"""
Tests for the two scaling methods added for "Mighty Warband"
(json-data/schemas/nations.json):

- per_x_land_unit_slots: scales effective_territory by the nation's own
  land_unit_capacity.
- per_x_active_wars: counts wars the nation is CURRENTLY, actively fighting
  (any stance), using the exact same liveness check as
  helpers/tick_helpers.py's nation_war_support_tick/nation_infamy_decay_tick
  (declared by the current session, not yet ended). Used as a condition
  (operator "<=" value 0) to gate Mighty Warband's -$400 money_income
  penalty on NOT being at war.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

from calculations.scaling_methods import (
    per_x_land_unit_slots,
    per_x_active_wars,
    SCALING_METHODS,
    get_scaling_multiplier,
)


class TestPerXLandUnitSlots:
    def test_registered(self):
        assert SCALING_METHODS.get("per_x_land_unit_slots") is per_x_land_unit_slots

    def test_scales_by_land_unit_capacity(self):
        target = {"land_unit_capacity": 20}
        assert per_x_land_unit_slots(target, scaling_x=5) == 4

    def test_missing_field_defaults_to_zero(self):
        assert per_x_land_unit_slots({}, scaling_x=5) == 0

    def test_wired_into_mighty_warband_law(self):
        from app_core import category_data
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        mods = laws["Mighty Warband"]["_modifiers"]
        scalings_used = {m.get("scaling") for m in mods}
        assert "per_x_land_unit_slots" in scalings_used


def _nation(nation_id=None):
    return {"_id": nation_id or ObjectId()}


class TestPerXActiveWars:
    def test_registered(self):
        assert SCALING_METHODS.get("per_x_active_wars") is per_x_active_wars

    def test_no_wars_at_all_returns_zero(self):
        nation_id = ObjectId()
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = []
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 0

    def test_currently_live_war_counts(self):
        nation_id = ObjectId()
        war_id = str(ObjectId())
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = [
            {"war": war_id, "participant": str(nation_id), "stance": "Attacker"},
        ]
        fake_mongo.db.wars.find_one.return_value = {
            "_id": ObjectId(war_id), "session_declared": 8, "session_ended": None,
        }
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 1

    def test_ended_war_does_not_count(self):
        nation_id = ObjectId()
        war_id = str(ObjectId())
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = [
            {"war": war_id, "participant": str(nation_id), "stance": "Attacker"},
        ]
        fake_mongo.db.wars.find_one.return_value = {
            "_id": ObjectId(war_id), "session_declared": 3, "session_ended": 5,  # ended before session 10
        }
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 0

    def test_not_yet_declared_war_does_not_count(self):
        nation_id = ObjectId()
        war_id = str(ObjectId())
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = [
            {"war": war_id, "participant": str(nation_id), "stance": "Attacker"},
        ]
        fake_mongo.db.wars.find_one.return_value = {
            "_id": ObjectId(war_id), "session_declared": 12, "session_ended": None,  # future
        }
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 0

    def test_defender_stance_also_counts(self):
        nation_id = ObjectId()
        war_id = str(ObjectId())
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = [
            {"war": war_id, "participant": str(nation_id), "stance": "Defender"},
        ]
        fake_mongo.db.wars.find_one.return_value = {
            "_id": ObjectId(war_id), "session_declared": 1, "session_ended": None,
        }
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 1

    def test_multiple_distinct_wars_counted_once_each(self):
        nation_id = ObjectId()
        war_id_1, war_id_2 = str(ObjectId()), str(ObjectId())
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = [
            {"war": war_id_1, "participant": str(nation_id), "stance": "Attacker"},
            {"war": war_id_2, "participant": str(nation_id), "stance": "Defender"},
        ]

        def _find_war(query):
            wid = str(query["_id"])
            return {"_id": query["_id"], "session_declared": 1, "session_ended": None}

        fake_mongo.db.wars.find_one.side_effect = _find_war
        with patch("app_core.mongo", fake_mongo):
            assert per_x_active_wars(_nation(nation_id)) == 2

    def test_condition_used_as_not_at_war_check(self):
        """Mighty Warband's actual usage: condition_operator '<=' value 0 —
        true (peace) only when the war count is exactly 0."""
        nation_id = ObjectId()
        fake_mongo = MagicMock()
        fake_mongo.db.global_modifiers.find_one.return_value = {"session_counter": 10}
        fake_mongo.db.war_links.find.return_value = []
        with patch("app_core.mongo", fake_mongo):
            at_peace = get_scaling_multiplier("per_x_active_wars", _nation(nation_id)) <= 0
        assert at_peace is True

    def test_missing_nation_id_returns_zero(self):
        assert per_x_active_wars({}) == 0

    def test_wired_into_mighty_warband_law(self):
        from app_core import category_data
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        mods = laws["Mighty Warband"]["_modifiers"]
        income_penalty = next(
            (m for m in mods if m.get("modifier_type") == "money_income"), None
        )
        assert income_penalty is not None
        assert income_penalty["value"] == -400
        assert income_penalty["condition_scaling"] == "per_x_active_wars"
        assert income_penalty["condition_operator"] == "<="
        assert income_penalty["condition_value"] == 0
