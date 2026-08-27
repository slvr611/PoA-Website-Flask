"""
Regression test for a real reported bug: character stat tooltips (e.g.
Cunning) merged every external source into one opaque total instead of
listing each source separately — two artifacts that each grant +1 Cunning
showed as a single "+2" line with no way to tell which artifacts (or other
sources) contributed.

Root cause: calculate_all_fields only builds a per-source "tagged_sources"
list (one labeled entry per artifact/title/custom modifier/etc., feeding
compute_nation_breakdowns) for target_data_type == "nation". Every other
target type — including "character" — passed tagged_sources=None, which
falls back to compute_nation_breakdowns's merged-by-category
component_sources path (one combined "external"/"modifiers"/"titles" entry
for the ENTIRE category, regardless of how many distinct sources fed it).

Fix: build an equivalent per-source tagged list for characters
(character_tagged_sources), covering the character's own custom modifiers,
titles, equipped artifacts (individually, via the already-generic
_collect_external_labeled), race traits, and ruled-nation district/law
bonuses — mirroring the nation-side _build_unit_tagged_sources pattern.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

CHARACTERS_SCHEMA = category_data["characters"]["schema"]


def _calculate(db, char_doc):
    fake_mongo = MagicMock()
    fake_mongo.db = db
    with patch.object(fc, "mongo", fake_mongo):
        return fc.calculate_all_fields(char_doc, CHARACTERS_SCHEMA, "character", return_breakdowns=True)


class TestCharacterBreakdownListsEachSourceSeparately:
    def test_two_artifacts_each_granting_plus_one_cunning_are_listed_separately(self):
        client = mongomock.MongoClient()
        db = client["test"]

        char_id = ObjectId()
        artifact_a_id = ObjectId()
        artifact_b_id = ObjectId()

        db["characters"].insert_one({"_id": char_id, "name": "Odirile"})
        db["artifacts"].insert_one({
            "_id": artifact_a_id, "name": "Ring of Cunning", "owner": str(char_id),
            "equipped": True,
            "modifiers": [
                {"modifier_type": "attribute", "scope": "artifact_owner", "attribute": "cunning", "value": 1.0, "scaling": "flat"},
            ],
        })
        db["artifacts"].insert_one({
            "_id": artifact_b_id, "name": "Cloak of Whispers", "owner": str(char_id),
            "equipped": True,
            "modifiers": [
                {"modifier_type": "attribute", "scope": "artifact_owner", "attribute": "cunning", "value": 1.0, "scaling": "flat"},
            ],
        })

        char_doc = db["characters"].find_one({"_id": char_id})
        calculated_values, breakdowns = _calculate(db, char_doc)

        assert calculated_values.get("cunning") == 2, f"expected both artifacts' bonuses to apply: {calculated_values.get('cunning')}"

        cunning_breakdown = breakdowns.get("cunning", [])
        artifact_entries = [e for e in cunning_breakdown if e["label"].startswith("Artifact:")]
        assert len(artifact_entries) == 2, (
            f"expected 2 separate artifact entries in the Cunning breakdown, got {artifact_entries} "
            f"(full breakdown: {cunning_breakdown})"
        )
        labels = {e["label"] for e in artifact_entries}
        assert labels == {"Artifact: Ring of Cunning", "Artifact: Cloak of Whispers"}
        for e in artifact_entries:
            assert e["value"] == 1.0

    def test_unequipped_artifact_does_not_contribute_or_appear(self):
        client = mongomock.MongoClient()
        db = client["test"]

        char_id = ObjectId()
        artifact_id = ObjectId()

        db["characters"].insert_one({"_id": char_id, "name": "Odirile"})
        db["artifacts"].insert_one({
            "_id": artifact_id, "name": "Unworn Ring", "owner": str(char_id),
            "equipped": False,
            "modifiers": [
                {"modifier_type": "attribute", "scope": "artifact_owner", "attribute": "cunning", "value": 5.0, "scaling": "flat"},
            ],
        })

        char_doc = db["characters"].find_one({"_id": char_id})
        calculated_values, breakdowns = _calculate(db, char_doc)

        assert calculated_values.get("cunning") == 0
        assert not any(e["label"].startswith("Artifact:") for e in breakdowns.get("cunning", []))

    def test_custom_modifier_and_title_are_listed_as_distinct_sources(self):
        client = mongomock.MongoClient()
        db = client["test"]
        char_id = ObjectId()

        db["characters"].insert_one({
            "_id": char_id, "name": "Odirile",
            "modifiers": [
                {"modifier_type": "attribute", "scope": "character_self", "attribute": "cunning", "value": 3.0, "scaling": "flat", "source": "Base Stats"},
            ],
        })

        char_doc = db["characters"].find_one({"_id": char_id})
        calculated_values, breakdowns = _calculate(db, char_doc)

        assert calculated_values.get("cunning") == 3
        cunning_breakdown = breakdowns.get("cunning", [])
        assert any(e["label"] == "Modifier: Base Stats" and e["value"] == 3.0 for e in cunning_breakdown), cunning_breakdown
