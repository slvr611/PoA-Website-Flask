"""
Regression test for a real reported bug: Theofania Chappelle (ruler of the
Archonate of Vyssafia) was not receiving +1 Rulership from a modifier on her
nation scoped to "nation_ruling_characters" (nor the nation's +4
artifact_slots modifier using the same scope).

Root cause: characters.json's external_calculation_requirements.ruling_nation_org
entry never requested the nation's own "modifiers" array at all — only a
fixed list of specific calculated fields (via fields_as_modifiers) and the
nation's *wonders'* modifiers (via the nested {"wonders": [...]} entry). A
nation's plain modifiers array, which is where "nation_ruling_characters"-
scoped entries actually live, was simply never inspected.

This is the same class of bug as the nation_vassals fix (a scope on one
entity meant to flow to a linked entity, but the schema-declared requirement
never asked for the "modifiers" field in the first place) — see
tests/test_vassal_scoped_modifiers.py for that case. Unlike nation_vassals,
no additional "required_scope" restriction is needed here: of the four
scopes sourced from a nation (nation_self, nation_ruling_characters,
nation_vassals, nation_overlord), only nation_ruling_characters targets
"character" — so the existing plain target_type filter in
collect_external_modifiers_from_object is already unambiguous.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

CHARACTERS_SCHEMA = category_data["characters"]["schema"]


def _collect(db, char_doc):
    fake_mongo = MagicMock()
    fake_mongo.db = db
    with patch.object(fc, "mongo", fake_mongo):
        return fc.collect_external_requirements(char_doc, CHARACTERS_SCHEMA, "character")


class TestNationRulingCharactersScopedModifiers:
    def test_ruling_character_receives_nation_scoped_modifier(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "Archonate of Vyssafia",
            "modifiers": [
                {"modifier_type": "attribute", "scope": "nation_ruling_characters", "attribute": "rulership", "value": 1.0, "scaling": "flat"},
                {"modifier_type": "artifact_slots", "scope": "nation_ruling_characters", "value": 4.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({"_id": char_id, "name": "Theofania Chappelle", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        result = _collect(db, char_doc)
        collected = {}
        for entry in result:
            for k, v in entry.items():
                collected[k] = collected.get(k, 0) + v

        assert collected.get("rulership") == 1.0, f"nation_ruling_characters rulership bonus missing: {result}"
        assert collected.get("artifact_slots", 0) >= 4.0, f"nation_ruling_characters artifact_slots bonus missing: {result}"

    def test_all_attributes_sentinel_also_works_through_this_scope(self):
        """Same "All Attributes" expansion fixed for artifacts (The Firefather)
        must also apply when the source is a nation modifier, not just an
        artifact — it's the same shared collect_external_modifiers_from_object
        code path."""
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "TestNation",
            "modifiers": [
                {"modifier_type": "attribute", "scope": "nation_ruling_characters", "attribute": "attribute", "value": 1.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({"_id": char_id, "name": "TestRuler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        result = _collect(db, char_doc)
        collected = {}
        for entry in result:
            for k, v in entry.items():
                collected[k] = collected.get(k, 0) + v

        for stat in ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]:
            assert collected.get(stat) == 1.0, f"{stat} missing from All-Attributes nation modifier: {result}"
        assert "attribute" not in collected

    def test_other_nation_scopes_do_not_leak_to_ruling_character(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()

        db["nations"].insert_one({
            "_id": nation_id, "name": "TestNation",
            "modifiers": [
                {"modifier_type": "attribute", "scope": "nation_ruling_characters", "attribute": "rulership", "value": 1.0, "scaling": "flat"},
                {"modifier_type": "money_income", "scope": "nation_self", "value": 9999, "scaling": "flat"},
                {"modifier_type": "compliance_loss_chance", "scope": "nation_vassals", "value": 0.5, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({"_id": char_id, "name": "TestRuler", "ruling_nation_org": str(nation_id)})

        char_doc = db["characters"].find_one({"_id": char_id})
        result = _collect(db, char_doc)

        assert not any("money_income" in r for r in result), f"nation_self modifier leaked to ruling character: {result}"
        assert not any("compliance_loss_chance" in r for r in result), f"nation_vassals modifier leaked to ruling character: {result}"
        assert any(r.get("rulership") == 1.0 for r in result)

    def test_character_with_no_ruling_nation_gets_nothing(self):
        client = mongomock.MongoClient()
        db = client["test"]
        char_id = ObjectId()
        db["characters"].insert_one({"_id": char_id, "name": "Nobody", "ruling_nation_org": ""})

        char_doc = db["characters"].find_one({"_id": char_id})
        result = _collect(db, char_doc)

        assert result == [] or all(not r for r in result)
