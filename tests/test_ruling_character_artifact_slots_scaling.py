"""
Regression tests for the new "per_x_ruling_character_artifact_slots" scaling
method (calculations/scaling_methods.py), added for the "Artificer State"
government law (json-data/schemas/nations.json), which scales a nation's
effective_territory bonus by the highest artifact_slots value among its
current ruling characters.

Also guards the bug this method replaces: the law previously referenced a
scaling key, "per_x_artifact_slots", that was never registered anywhere in
scaling_methods.py or scaling_types.json. get_scaling_multiplier silently
returns 1 (a no-op flat multiplier) for any unregistered scaling key, so
the modifier was behaving as an unscaled flat bonus instead of erroring or
being disabled.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

from calculations.scaling_methods import (
    per_x_ruling_character_artifact_slots,
    SCALING_METHODS,
    get_scaling_multiplier,
)


def _target(nation_id):
    return {"_id": nation_id}


class TestPerXRulingCharacterArtifactSlots:
    def test_registered_in_scaling_methods(self):
        assert SCALING_METHODS.get("per_x_ruling_character_artifact_slots") is per_x_ruling_character_artifact_slots

    def test_no_rulers_returns_zero(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        with patch("app_core.mongo", MagicMock(db=db)):
            assert per_x_ruling_character_artifact_slots(_target(nation_id)) == 0

    def test_uses_the_highest_ruling_character_not_a_sum(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_one({"_id": ObjectId(), "ruling_nation_org": str(nation_id), "artifact_slots": 12})
        db["characters"].insert_one({"_id": ObjectId(), "ruling_nation_org": str(nation_id), "artifact_slots": 32})
        with patch("app_core.mongo", MagicMock(db=db)):
            assert per_x_ruling_character_artifact_slots(_target(nation_id)) == 32

    def test_scaling_x_divides_and_floors(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_one({"_id": ObjectId(), "ruling_nation_org": str(nation_id), "artifact_slots": 32})
        with patch("app_core.mongo", MagicMock(db=db)):
            assert per_x_ruling_character_artifact_slots(_target(nation_id), scaling_x=5) == 6  # floor(32/5)

    def test_other_nations_rulers_are_not_counted(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        other_nation_id = ObjectId()
        db["characters"].insert_one({"_id": ObjectId(), "ruling_nation_org": str(other_nation_id), "artifact_slots": 50})
        with patch("app_core.mongo", MagicMock(db=db)):
            assert per_x_ruling_character_artifact_slots(_target(nation_id)) == 0

    def test_via_get_scaling_multiplier_registry_lookup(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["characters"].insert_one({"_id": ObjectId(), "ruling_nation_org": str(nation_id), "artifact_slots": 20})
        with patch("app_core.mongo", MagicMock(db=db)):
            assert get_scaling_multiplier("per_x_ruling_character_artifact_slots", _target(nation_id), scaling_x=1) == 20

    def test_the_old_broken_scaling_key_no_longer_appears_in_artificer_state(self):
        """Confirms the fix was actually wired into the law, not just built
        and left unused."""
        from app_core import category_data
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        artificer_mods = laws["Artificer State"]["_modifiers"]
        scalings_used = {m.get("scaling") for m in artificer_mods}
        assert "per_x_artifact_slots" not in scalings_used
        assert "per_x_ruling_character_artifact_slots" in scalings_used
