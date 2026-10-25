"""
Reported bug: the built-in Quartermaster character subtype should grant the
nation it rules +1 tier-0 progress slot, via character_subtype's own law
entry (json-data/schemas/characters.json):

    "Quartermaster": {
        "_modifiers": [
            {"modifier_type": "progress_slots", "tier": "0", "value": 1,
             "scope": "character_ruling_nation"}
        ]
    }

It wasn't landing anywhere. calculate_all_fields's "_law_scaled" self-apply
guard (see test_law_modifiers_scope_self_only.py) correctly stops this
scope from applying to the CHARACTER itself -- character_ruling_nation
resolves via a forward_link to the nation, not directly to the character.
But nothing then forwarded it TO that nation either:

- collect_external_requirements' generic "rulers" lookup for a nation
  (nations.json's external_calculation_requirements.rulers.fields) only
  requests "positive_titles", "negative_titles", "modifiers", and
  "artifacts" from each ruler -- never "character_subtype".
- Even if it did, collect_external_modifiers_from_object's enum/laws
  branch only understands the legacy flat "{target_data_type}_{field}"
  key format, not a modern scope-based "_modifiers" list.

So the bonus was silently dropped in both directions. Fixed by adding
LawAdapter.collect_for_nation (calculations/source_adapters.py) -- the
exact mirror of the already-existing, already-correct
LawAdapter.collect_for_character (see
test_law_embedded_ruling_character_modifiers.py, which documents the
established, sole-source pattern this follows: a dedicated adapter method,
not a second generic-pathway entry, to avoid double-counting).

Of the three "laws" fields on the character schema (character_type,
character_subtype, health_status), Quartermaster is the only existing law
entry anywhere that uses a cross-entity scope in its _modifiers today --
but the fix is the general mechanism, not a Quartermaster-specific patch,
so any future subtype/character_type/health_status law using
character_ruling_nation works the same way without further changes.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from calculations.source_adapters import LawAdapter
from app_core import category_data


def _seed(character_subtype="Quartermaster"):
    client = mongomock.MongoClient()
    db = client["test"]
    nation_id = ObjectId()
    char_id = ObjectId()
    db["nations"].insert_one({"_id": nation_id, "name": "Test Nation"})
    db["characters"].insert_one({
        "_id": char_id, "name": "Test Quartermaster",
        "ruling_nation_org": str(nation_id), "character_subtype": character_subtype,
    })
    nation_doc = db["nations"].find_one({"_id": nation_id})
    return db, nation_doc


class TestLawAdapterCollectForNation:
    def test_quartermaster_grants_the_ruled_nation_one_tier0_progress_slot(self):
        db, nation_doc = _seed("Quartermaster")
        with patch("app_core.mongo", MagicMock(db=db)):
            contributions = LawAdapter.collect_for_nation(nation_doc)

        totals = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                totals[k] = totals.get(k, 0) + v
        assert totals.get("0_progress_slots") == 1, f"Quartermaster bonus missing/wrong: {totals}"

    def test_a_nation_with_no_rulers_gets_nothing(self):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        db["nations"].insert_one({"_id": nation_id, "name": "Rulerless Nation"})
        nation_doc = db["nations"].find_one({"_id": nation_id})
        with patch("app_core.mongo", MagicMock(db=db)):
            contributions = LawAdapter.collect_for_nation(nation_doc)
        assert contributions == []

    def test_a_non_quartermaster_subtype_grants_nothing_extra(self):
        db, nation_doc = _seed("None")
        with patch("app_core.mongo", MagicMock(db=db)):
            contributions = LawAdapter.collect_for_nation(nation_doc)
        assert contributions == []


class TestEndToEndCalculateAllFields:
    """Confirms the fix through the actual pipeline calculate_all_fields
    uses, not just the adapter in isolation -- and that the bonus still
    correctly does NOT land on the character itself (the already-fixed
    half of this bug, re-confirmed here end-to-end alongside the new
    forwarding fix)."""

    def _run(self, target, schema, target_data_type, mongo_db):
        fake_mongo = MagicMock()
        fake_mongo.db = mongo_db
        with patch.object(fc, "mongo", fake_mongo):
            with patch("app_core.mongo", fake_mongo):
                return fc.calculate_all_fields(dict(target), schema, target_data_type)

    def test_nation_calculation_gains_the_progress_slot(self):
        db, nation_doc = _seed("Quartermaster")
        nation_schema = category_data["nations"]["schema"]
        calc = self._run(nation_doc, nation_schema, "nation", db)
        base = nation_schema["properties"].get("0_progress_slots", {}).get("base_value", 0)
        assert calc.get("0_progress_slots", 0) == base + 1, (
            f"Quartermaster's +1 tier-0 progress slot did not reach the nation "
            f"it rules: got {calc.get('0_progress_slots')}, base was {base}"
        )

    def test_character_itself_does_not_also_gain_the_slot(self):
        db, nation_doc = _seed("Quartermaster")
        char_doc = db["characters"].find_one({"ruling_nation_org": str(nation_doc["_id"])})
        character_schema = category_data["characters"]["schema"]
        calc = self._run(char_doc, character_schema, "character", db)
        assert calc.get("0_progress_slots", 0) == 0, (
            f"character_ruling_nation-scoped Quartermaster bonus incorrectly "
            f"self-applied to the character too: {calc.get('0_progress_slots')}"
        )
