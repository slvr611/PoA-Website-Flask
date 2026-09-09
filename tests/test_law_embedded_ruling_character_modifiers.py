"""
Regression tests for a real bug found while auditing the "Artificer State"
government type law (json-data/schemas/nations.json): its
nation_ruling_characters-scoped "+20 artifact_slots" bonus, and the
identical (also previously dead) bonuses on "Fallen Monarchy" and
"Ruthless Meritocracy", could never reach a ruling character.

Root cause: characters.json's external_calculation_requirements.ruling_nation_org
already requested a nation's flat "modifiers" array (fixed by an earlier
bug — see tests/test_ruling_character_scoped_modifiers.py), but a
government_type LAW's own embedded "_modifiers" list is a completely
separate piece of schema data, nested under
government_type.laws.<value>._modifiers rather than the nation's top-level
"modifiers" array. Nothing ever requested "government_type" from a ruling
nation, and even if it had,
calculations.field_calculations.collect_external_modifiers_from_object's
enum/laws branch only ever extracted flat {target_data_type}_-prefixed
keys from a law — never a nested "_modifiers" list. Fixed by adding
"government_type" to characters.json's ruling_nation_org.fields and
extending both collect_external_modifiers_from_object and its tooltip
counterpart, _extract_labeled_from_object, to also walk a law's
"_modifiers" list using the same scope-based resolution already used for
a plain "modifiers" array.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

CHARACTERS_SCHEMA = category_data["characters"]["schema"]
NATIONS_SCHEMA = category_data["nations"]["schema"]


def _collect(db, char_doc):
    fake_mongo = MagicMock()
    fake_mongo.db = db
    with patch.object(fc, "mongo", fake_mongo):
        return fc.collect_external_requirements(char_doc, CHARACTERS_SCHEMA, "character")


def _totals(result):
    collected = {}
    for entry in result:
        for k, v in entry.items():
            collected[k] = collected.get(k, 0) + v
    return collected


def _labeled(db, nation_doc, char_doc):
    fake_mongo = MagicMock()
    fake_mongo.db = db
    with patch.object(fc, "mongo", fake_mongo):
        return fc._extract_labeled_from_object(
            nation_doc,
            CHARACTERS_SCHEMA["external_calculation_requirements"]["ruling_nation_org"]["fields"],
            NATIONS_SCHEMA,
            "character",
            "ruler",
            base_label="Ruled Nation",
            target=char_doc,
        )


class TestLawEmbeddedRulingCharacterModifiers:
    def test_artificer_state_artifact_slots_reaches_ruling_character(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "Test Artificer Nation",
            "government_type": "Artificer State",
        })
        db["characters"].insert_one({"_id": char_id, "name": "Test Ruler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        collected = _totals(_collect(db, char_doc))

        assert collected.get("artifact_slots", 0) >= 20, f"Artificer State's ruling-character artifact_slots bonus missing: {collected}"

    def test_nation_self_scoped_entries_in_the_same_law_do_not_leak(self):
        """Artificer State's other two _modifiers entries are nation_self-scoped
        (effective_territory) and must stay on the nation, never reach the
        character."""
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "Test Artificer Nation",
            "government_type": "Artificer State",
        })
        db["characters"].insert_one({"_id": char_id, "name": "Test Ruler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        collected = _totals(_collect(db, char_doc))

        assert "effective_territory" not in collected, f"nation_self modifier leaked to ruling character: {collected}"

    def test_fallen_monarchy_all_attributes_reaches_ruling_character(self):
        """Same law-embedded-_modifiers pathway, a second pre-existing
        government type with a nation_ruling_characters scope (All
        Attributes -1 / attribute_cap -1)."""
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "Test Fallen Monarchy Nation",
            "government_type": "Fallen Monarchy",
        })
        db["characters"].insert_one({"_id": char_id, "name": "Test Ruler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        collected = _totals(_collect(db, char_doc))

        for stat in ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]:
            assert collected.get(stat) == -1.0, f"{stat} missing from Fallen Monarchy's All-Attributes ruler penalty: {collected}"

    def test_character_with_unrelated_government_type_gets_nothing(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "Plain Nation",
            "government_type": "Chaotic Throng",
        })
        db["characters"].insert_one({"_id": char_id, "name": "Test Ruler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        collected = _totals(_collect(db, char_doc))

        assert "artifact_slots" not in collected
        assert "rulership" not in collected

    def test_tooltip_extraction_also_surfaces_the_law_embedded_bonus(self):
        """The same fix applied to the tooltip/breakdown path
        (_extract_labeled_from_object), not just the calculation path."""
        nation_doc = {
            "_id": ObjectId(), "name": "Test Artificer Nation",
            "government_type": "Artificer State",
        }
        char_doc = {"_id": ObjectId(), "name": "Test Ruler", "ruling_nation_org": str(nation_doc["_id"])}
        client = mongomock.MongoClient()
        db = client["test"]

        entries = _labeled(db, nation_doc, char_doc)
        found = [e for e in entries if e["modifiers"].get("artifact_slots", 0) >= 20]
        assert found, f"tooltip entries missing the law-embedded ruler artifact_slots bonus: {entries}"
