"""
Regression test for a real reported bug: Munira Ab-Talayin Nufayd (ruler of
a Ruthless Meritocracy nation) was receiving +2 to every attribute cap
instead of the intended +1, with nothing in the tooltip explaining the
second point.

Root cause: calculations/source_adapters.py's LawAdapter.collect_for_character
already walks a ruling nation's government_type law's embedded "_modifiers"
list (scope-filtered to target_type "character") and feeds the result into
both district_totals (the real calculation, see
field_calculations.py's calculate_all_fields, target_data_type == "character"
branch) and the character's tooltip ("Ruled Nation Law: ..."). This is the
correct, sole pathway for a law-embedded nation_ruling_characters-scoped
modifier to reach a ruling character.

A previous session mistakenly believed no such pathway existed (missing
this adapter) and added a second, duplicate extraction of the exact same
data into collect_external_modifiers_from_object and
_extract_labeled_from_object (reached via characters.json's
external_calculation_requirements.ruling_nation_org, by adding
"government_type" to its "fields" list) — so every nation_ruling_characters-
scoped law modifier (Ruthless Meritocracy, Fallen Monarchy, Artificer State)
was applied twice. Fixed by reverting that second pathway entirely:
"government_type" removed from characters.json's ruling_nation_org.fields,
and the _modifiers-in-law handling removed from both
collect_external_modifiers_from_object and _extract_labeled_from_object.
LawAdapter.collect_for_character remains the one and only source for this.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from calculations.source_adapters import LawAdapter
from app_core import category_data

CHARACTERS_SCHEMA = category_data["characters"]["schema"]


def _collect_external(db, char_doc):
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


class TestLawEmbeddedRulingCharacterModifiersNotDuplicated:
    def _seed(self, government_type):
        client = mongomock.MongoClient()
        db = client["test"]
        nation_id = ObjectId()
        char_id = ObjectId()
        db["nations"].insert_one({
            "_id": nation_id, "name": "Test Nation",
            "government_type": government_type,
        })
        db["characters"].insert_one({"_id": char_id, "name": "Test Ruler", "ruling_nation_org": str(nation_id)})
        char_doc = db["characters"].find_one({"_id": char_id})
        return db, char_doc

    def test_ruthless_meritocracy_applies_the_bonus_exactly_once(self):
        db, char_doc = self._seed("Ruthless Meritocracy")

        with patch("app_core.mongo", MagicMock(db=db)):
            contributions = LawAdapter.collect_for_character(char_doc)
        law_totals = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                law_totals[k] = law_totals.get(k, 0) + v

        for stat in ["rulership_cap", "cunning_cap", "charisma_cap", "prowess_cap", "magic_cap", "strategy_cap"]:
            assert law_totals.get(stat) == 1, f"{stat} should be exactly +1 from Ruthless Meritocracy, got: {law_totals}"

    def test_external_requirements_no_longer_also_produce_this_bonus(self):
        """Guards against reintroducing the duplicate: collect_external_requirements
        (the pull-based external_calculation_requirements pathway) must NOT
        also surface a ruling nation's law-embedded _modifiers — that would
        double the total on top of LawAdapter.collect_for_character."""
        db, char_doc = self._seed("Ruthless Meritocracy")
        collected = _totals(_collect_external(db, char_doc))

        assert "attribute_cap" not in collected
        assert "rulership_cap" not in collected
        assert not any(k.endswith("_cap") for k in collected), f"law-embedded ruler bonus leaked through external requirements: {collected}"

    def test_artificer_state_artifact_slots_still_applies_exactly_once(self):
        db, char_doc = self._seed("Artificer State")

        with patch("app_core.mongo", MagicMock(db=db)):
            contributions = LawAdapter.collect_for_character(char_doc)
        law_totals = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                law_totals[k] = law_totals.get(k, 0) + v

        assert law_totals.get("artifact_slots") == 20, f"Artificer State ruler bonus wrong via LawAdapter: {law_totals}"

        collected_external = _totals(_collect_external(db, char_doc))
        assert "artifact_slots" not in collected_external, f"Artificer State ruler bonus duplicated via external requirements: {collected_external}"
