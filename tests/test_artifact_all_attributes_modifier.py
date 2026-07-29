"""
Regression test for a real reported bug: Odirile's equipped artifact "The
Firefather" has two scope-based modifiers using the "All Attributes"
sentinel (extra field "attribute" sourced from "attributes", value literally
"attribute") — one modifier_type "attribute" (+1 to every stat) and one
"attribute_cap" (+1 to every stat cap) — that were silently doing nothing.

Root cause, in two parts:

1. characters.json's external_calculation_requirements.artifacts entry only
   requested "external_modifiers" from a character's own artifacts, never
   "modifiers" — so a scope-based modifier (scope: "artifact_owner") on an
   owned artifact was never even collected for the owning character.

2. Once collected, _resolve_modifier_type (calculations/source_adapters.py)
   substitutes the extra field's raw value into the field_template as-is —
   for the "attribute"/"attribute_cap" modifier_types that means the literal
   string "attribute" gets substituted into "{attribute}"/"{attribute}_cap",
   producing the field key "attribute" or "attribute_cap" (which nothing
   reads) instead of expanding into all six real stat fields. This exact
   "All Attributes" sentinel already had correct expand-to-all-six-stats
   handling in sum_modifier_totals (a Nation's/character's own direct
   modifiers array) — collect_external_modifiers_from_object's separate
   "modifiers" handling (used for cross-entity modifiers, e.g. an artifact's
   modifiers flowing to its owning character) just never had the same
   handling.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

CHARACTERS_SCHEMA = category_data["characters"]["schema"]


class TestArtifactAllAttributesModifierAppliesToOwner:
    def test_all_attributes_and_all_attribute_caps_expand_to_every_stat(self):
        client = mongomock.MongoClient()
        db = client["test"]

        char_id = ObjectId()
        art_id = ObjectId()

        db["characters"].insert_one({"_id": char_id, "name": "Odirile"})
        db["artifacts"].insert_one({
            "_id": art_id, "name": "The Firefather", "owner": str(char_id),
            "equipped": True,
            "modifiers": [
                {"modifier_type": "attribute", "scope": "artifact_owner", "attribute": "attribute", "value": 1.0, "scaling": "flat"},
                {"modifier_type": "attribute_cap", "scope": "artifact_owner", "attribute": "attribute", "value": 1.0, "scaling": "flat"},
            ],
        })

        fake_mongo = MagicMock()
        fake_mongo.db = db
        char_doc = db["characters"].find_one({"_id": char_id})

        with patch.object(fc, "mongo", fake_mongo):
            result = fc.collect_external_requirements(char_doc, CHARACTERS_SCHEMA, "character")

        collected = {}
        for entry in result:
            collected.update(entry)

        all_stats = ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]
        for stat in all_stats:
            assert collected.get(stat) == 1.0, f"{stat} bonus from The Firefather was not applied: {collected}"
            assert collected.get(f"{stat}_cap") == 1.0, f"{stat}_cap bonus from The Firefather was not applied: {collected}"

        # The bogus literal field names must NOT appear.
        assert "attribute" not in collected
        assert "attribute_cap" not in collected
