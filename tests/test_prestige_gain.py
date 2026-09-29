"""
Regression tests for compute_prestige_gain's (calculations/compute_functions.py)
disloyal-vassal prestige penalty: increased from -2/vassal (capped at -10) to
-3/vassal (capped at -15) at the user's request. The duplicate breakdown-UI
logic in calculations/field_calculations.py (the "prestige_gain — custom
breakdown matching compute_prestige_gain logic" block) uses the same two
constants and must stay in sync with it.
"""
import mongomock
from bson import ObjectId

from app_core import category_data
from calculations.compute_functions import compute_prestige_gain
from calculations.field_calculations import calculate_all_fields


def _patched_category_data(monkeypatch, db):
    for key in ("diplo_relations", "nations", "characters", "artifacts"):
        monkeypatch.setitem(category_data[key], "database", db[key])


def _vassal(overlord_id, compliance):
    return {"_id": ObjectId(), "overlord": str(overlord_id), "vassal_type": "Tributary", "compliance": compliance}


class TestComputePrestigeGainDisloyalVassalPenalty:
    def test_single_rebellious_vassal_loses_three(self, monkeypatch):
        client = mongomock.MongoClient()
        db = client["test"]
        _patched_category_data(monkeypatch, db)

        nation_id = ObjectId()
        db["nations"].insert_one(_vassal(nation_id, "Rebellious"))

        value = compute_prestige_gain("prestige_gain", {"_id": nation_id}, 0, {}, {})
        assert value == -3

    def test_single_defiant_vassal_loses_three(self, monkeypatch):
        client = mongomock.MongoClient()
        db = client["test"]
        _patched_category_data(monkeypatch, db)

        nation_id = ObjectId()
        db["nations"].insert_one(_vassal(nation_id, "Defiant"))

        value = compute_prestige_gain("prestige_gain", {"_id": nation_id}, 0, {}, {})
        assert value == -3

    def test_penalty_scales_with_vassal_count(self, monkeypatch):
        client = mongomock.MongoClient()
        db = client["test"]
        _patched_category_data(monkeypatch, db)

        nation_id = ObjectId()
        db["nations"].insert_many([
            _vassal(nation_id, "Rebellious"),
            _vassal(nation_id, "Defiant"),
        ])

        value = compute_prestige_gain("prestige_gain", {"_id": nation_id}, 0, {}, {})
        assert value == -6  # 2 disloyal vassals x -3 each

    def test_penalty_caps_at_fifteen(self, monkeypatch):
        client = mongomock.MongoClient()
        db = client["test"]
        _patched_category_data(monkeypatch, db)

        nation_id = ObjectId()
        # 6 disloyal vassals x -3 = -18 raw, capped at -15
        db["nations"].insert_many([_vassal(nation_id, "Rebellious") for _ in range(6)])

        value = compute_prestige_gain("prestige_gain", {"_id": nation_id}, 0, {}, {})
        assert value == -15

    def test_loyal_vassal_bonus_is_unchanged(self, monkeypatch):
        """Only the disloyal-vassal penalty changed — the loyal-vassal bonus
        (+1/vassal, capped at +3) must be untouched."""
        client = mongomock.MongoClient()
        db = client["test"]
        _patched_category_data(monkeypatch, db)

        nation_id = ObjectId()
        db["nations"].insert_many([_vassal(nation_id, "Loyal") for _ in range(5)])

        value = compute_prestige_gain("prestige_gain", {"_id": nation_id}, 0, {}, {})
        assert value == 3  # capped at +3, unchanged by this fix


class TestBreakdownMatchesComputeFunction:
    """The duplicated breakdown-UI logic in field_calculations.py must use
    the exact same -3/vassal, cap-15 constants as compute_prestige_gain."""

    def test_breakdown_disloyal_vassal_value_matches(self, monkeypatch):
        client = mongomock.MongoClient()
        db = client["test"]
        for key in ("diplo_relations", "nations", "characters", "artifacts", "regions", "pops"):
            monkeypatch.setitem(category_data[key], "database", db[key])

        nation_id = ObjectId()
        nation = {
            "_id": nation_id, "name": "Testland", "empire": True, "modifiers": [],
            "money": 0, "primary_race": "", "primary_culture": "", "primary_religion": "",
        }
        db["nations"].insert_one(nation)
        db["nations"].insert_many([_vassal(nation_id, "Rebellious") for _ in range(4)])

        schema = category_data["nations"]["schema"]
        calculated, breakdowns = calculate_all_fields(dict(nation), schema, "nation", return_breakdowns=True)

        pg_bd = breakdowns.get("prestige_gain", [])
        disloyal_entry = next((e for e in pg_bd if e.get("label") == "Disloyal Vassals"), None)
        assert disloyal_entry is not None
        assert disloyal_entry["value"] == -12  # 4 x -3, under the 15 cap
