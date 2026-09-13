"""
Regression tests for the Grand Archive of Sapieni-Nabu's "+2 research per
session once the ruler is an adult" effect.

The wonder previously stored this as a legacy external_modifiers entry
({"type": "nation", "modifier": "research_production_with_adult_leader",
"value": 1.0}) — but "research_production_with_adult_leader" was never a
recognized modifier key anywhere in the engine (only "research_production"
is), so the bonus was a silent no-op for every nation that owned it,
including the Madhan Empire.

Fixed by:
  1. Adding a new scaling method, per_x_ruling_character_is_adult
     (calculations/scaling_methods.py), which returns 1 (as a flat 0/1
     gate, divided by scaling_x) if ANY of the target nation's current
     ruling characters (characters.ruling_nation_org == nation._id) has
     age > 0 (mirrors per_x_ruling_character_artifact_slots's "any/max of
     multiple rulers" pattern), else 0.
  2. Replacing the wonder's dead external_modifiers entry with a real
     modifiers[] entry using the standard modifier_type "resource_production"
     (extra_field resource="research"), scope "wonder_owner_nation" (so it
     flows to the owning nation, mirroring every other wonder bonus), and
     the new scaling method as a flat on/off multiplier (scaling_x=1).
"""
import mongomock
from bson import ObjectId
from unittest.mock import patch

from app_core import category_data
from calculations.field_calculations import calculate_all_fields
from calculations.scaling_methods import per_x_ruling_character_is_adult


def _grand_archive_modifier():
    return {
        "modifier_type": "resource_production", "resource": "research", "value": 2,
        "scope": "wonder_owner_nation", "scaling": "per_x_ruling_character_is_adult",
        "scaling_x": 1,
    }


class TestPerXRulingCharacterIsAdultScalingMethod:
    def test_returns_1_when_a_ruler_is_an_adult(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_one({"ruling_nation_org": str(nation_id), "age": 4})
        with patch("app_core.mongo", type("M", (), {"db": db})()):
            assert per_x_ruling_character_is_adult({"_id": nation_id}) == 1

    def test_returns_0_when_the_only_ruler_is_a_child(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_one({"ruling_nation_org": str(nation_id), "age": 0})
        with patch("app_core.mongo", type("M", (), {"db": db})()):
            assert per_x_ruling_character_is_adult({"_id": nation_id}) == 0

    def test_returns_0_when_the_nation_has_no_ruler(self):
        client = mongomock.MongoClient()
        db = client["test"]
        with patch("app_core.mongo", type("M", (), {"db": db})()):
            assert per_x_ruling_character_is_adult({"_id": ObjectId()}) == 0

    def test_any_adult_among_multiple_rulers_is_enough(self):
        """Mirrors per_x_ruling_character_artifact_slots's precedent of not
        requiring every ruler to individually qualify."""
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_many([
            {"ruling_nation_org": str(nation_id), "age": 0},
            {"ruling_nation_org": str(nation_id), "age": 5},
        ])
        with patch("app_core.mongo", type("M", (), {"db": db})()):
            assert per_x_ruling_character_is_adult({"_id": nation_id}) == 1


class TestGrandArchiveWonderEndToEnd:
    """Builds the exact wonder/nation/character shape live in the database
    and confirms the +2 research bonus actually reaches resource_production
    through calculate_all_fields — the real path a nation page recalculation
    takes."""

    def _setup(self, ruler_age):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        wonder_id = ObjectId()
        db["nations"].insert_one({"_id": nation_id, "name": "Test Nation", "money": 0})
        db["wonders"].insert_one({
            "_id": wonder_id, "name": "Grand Archive of Sapieni-Nabu",
            "owner_nation": str(nation_id), "modifiers": [_grand_archive_modifier()],
            "external_modifiers": [], "node": "",
        })
        db["characters"].insert_one({
            "name": "Test Ruler", "ruling_nation_org": str(nation_id), "age": ruler_age,
        })
        return db, nation_id

    def _research_production(self, db, nation_id):
        schema = category_data["nations"]["schema"]
        nation = db["nations"].find_one({"_id": nation_id})
        fake_mongo = type("M", (), {"db": db})()
        with patch("calculations.field_calculations.mongo", fake_mongo), \
             patch("app_core.mongo", fake_mongo):
            calc = calculate_all_fields(dict(nation), schema, "nation")
        return calc.get("resource_production", {}).get("research", 0)

    def test_adult_ruler_grants_plus_2_research(self):
        db, nation_id = self._setup(ruler_age=4)
        assert self._research_production(db, nation_id) == 2

    def test_child_ruler_grants_no_research_bonus(self):
        db, nation_id = self._setup(ruler_age=0)
        assert self._research_production(db, nation_id) == 0
