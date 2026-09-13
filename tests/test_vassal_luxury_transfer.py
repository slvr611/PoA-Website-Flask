"""
Tests for _apply_vassal_tribute_modifiers's Mercantile vassal handling:
a Mercantile vassal's luxury resource production is gained entirely by its
overlord instead of the vassal itself (a full transfer, unlike Provincial's
own research mechanic — see TestProvincialResearchTransferIsFlatOneNotHalfOfProduction
below: the vassal loses 100% of its own research, but the overlord's gain
is a flat +1 per Provincial vassal, granted by nations.json's own
"Provincial" law modifier rather than anything in this function).

Also covers a bugfix found along the way: the overlord-side vassal query
projected only {pop_count, vassal_type, modifiers}, omitting
resource_production entirely — which silently zeroed out Mercantile's
luxury transfer too (vassal.get("resource_production", {}) was always
{}). Fixed by adding resource_production to the projection. (Originally
found via Provincial's research transfer, which at the time still lived
in this function — see TestProvincialResearchTransferIsFlatOneNotHalfOfProduction
below for why that overlord-side logic was later removed entirely.)
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

from calculations.field_calculations import _apply_vassal_tribute_modifiers


def _patched_mongo(vassal_docs):
    mock_mongo = MagicMock()
    mock_mongo.db.nations.find.return_value = vassal_docs
    return mock_mongo


class TestMercantileVassalSideRemovesOwnLuxuryProduction:
    def test_luxury_production_removed_from_self(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Mercantile",
            "resource_production": {"gold": 4, "tea": 2},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("gold_production") == -4
        assert overall.get("tea_production") == -2

    def test_zero_production_luxuries_are_not_touched(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Mercantile",
            "resource_production": {"gold": 0},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert "gold_production" not in overall

    def test_non_mercantile_vassal_keeps_its_own_luxury_production(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Tributary",
            "resource_production": {"gold": 4},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert "gold_production" not in overall


class TestMercantileOverlordSideGainsVassalLuxuryProduction:
    def test_overlord_gains_full_luxury_amount(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "resource_production": {"gold": 4, "tea": 2},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 4
        assert overall.get("tea_production") == 2

    def test_transfer_is_full_not_half(self):
        """Explicitly distinct from Provincial's 50%-lossy research split —
        Mercantile luxury production moves 1:1."""
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "resource_production": {"gold": 7},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7

    def test_non_mercantile_vassal_grants_no_luxury_transfer(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Tributary", "modifiers": [],
            "resource_production": {"gold": 4},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "gold_production" not in overall

    def test_multiple_mercantile_vassals_stack(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_docs = [
            {"pop_count": 50, "vassal_type": "Mercantile", "modifiers": [], "resource_production": {"gold": 4}},
            {"pop_count": 30, "vassal_type": "Mercantile", "modifiers": [], "resource_production": {"gold": 3}},
        ]
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo(vassal_docs)):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7


class TestProvincialResearchTransferIsFlatOneNotHalfOfProduction:
    """Provincial's overlord-side research gain used to be a hardcoded
    ceil(vassal_research * 0.5) added on top of json-data/schemas/nations.json's
    own "Provincial": {"overlord_nation_research_production": 1} law modifier
    — which was ALREADY correctly delivering a flat +1 per Provincial vassal
    via the standard external_calculation_requirements/modifier_prefix
    mechanism (nations.json's "vassals": {"fields": ["vassal_type"],
    "modifier_prefix": "overlord"}). So an overlord was silently double-
    dipping: +1 flat (schema law) plus ceil(vassal_research * 0.5) (this
    hardcoded Python addition) for every Provincial vassal.

    Fixed by removing the hardcoded addition entirely — _apply_vassal_
    tribute_modifiers (and its tooltip-breakdown twin in
    _build_computed_contributions) must no longer touch research_production
    for a Provincial vassal at all; the flat +1 the schema already grants is
    the sole, correct transfer amount. The vassal's OWN side is unaffected
    by this change — a Provincial vassal still loses 100% of its own
    research production (see the "Vassal side" test below), which is a
    separate, deliberate mechanic from what the overlord gains.
    """

    def test_overlord_side_no_longer_adds_a_hardcoded_research_bonus(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Provincial", "modifiers": [],
            "resource_production": {"research": 10},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "research_production" not in overall

    def test_vassal_side_still_loses_all_of_its_own_research(self):
        """Unaffected by this fix — a Provincial vassal still can't keep any
        of its own research (nations.json's "nation_max_research_production":
        0 for the Provincial law caps it, and this is the modifier-side
        mirror of that: the vassal's own production is fully zeroed out)."""
        target = {
            "overlord": "overlord123", "pop_count": 50, "vassal_type": "Provincial",
            "resource_production": {"research": 10},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("research_production") == -10

    def test_overlord_gets_exactly_one_flat_point_per_provincial_vassal_end_to_end(self):
        """End-to-end guard against the double-count regression: with the
        hardcoded half-of-research addition gone, collect_external_requirements
        (the schema-driven "vassals"/modifier_prefix:"overlord" pathway) must
        still deliver exactly +1 research_production per Provincial vassal —
        regardless of how much research that vassal actually produces — and
        nothing more."""
        import mongomock
        from calculations.field_calculations import collect_external_requirements
        from app_core import category_data

        client = mongomock.MongoClient()
        db = client["test"]
        overlord_id = ObjectId()
        db["nations"].insert_one({
            "_id": overlord_id, "name": "Overlord",
            "vassal_type": "Provincial", "resource_production": {"research": 500},
        })
        db["nations"].insert_one({
            "_id": ObjectId(), "name": "Vassal One", "overlord": str(overlord_id),
            "vassal_type": "Provincial", "resource_production": {"research": 3},
        })
        db["nations"].insert_one({
            "_id": ObjectId(), "name": "Vassal Two", "overlord": str(overlord_id),
            "vassal_type": "Provincial", "resource_production": {"research": 47},
        })

        overlord = db["nations"].find_one({"_id": overlord_id})
        schema = category_data["nations"]["schema"]
        fake_mongo = type("FakeMongo", (), {"db": db})()
        with patch("calculations.field_calculations.mongo", fake_mongo):
            result = collect_external_requirements(overlord, schema, "nation")
        total = {}
        for entry in result:
            for k, v in entry.items():
                total[k] = total.get(k, 0) + v

        assert total.get("research_production") == 2  # 1 per vassal, regardless of their output
