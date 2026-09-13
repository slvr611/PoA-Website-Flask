"""
Regression tests for a real gap found while adding "Owner's Ruling
Merchant"/"Owner's Ruling Mercenary" scope options for artifacts:

merchants.json's and mercenaries.json's external_calculation_requirements
for "leaders" only ever requested positive_titles/negative_titles — never
"modifiers", and never a nested {"artifacts": [...]} sub-requirement. So
even the PRE-EXISTING "character_ruling_merchant"/"character_ruling_mercenary"
scopes (a leading character's own modifiers array) had no path to ever
reach the merchant/mercenary they lead — exactly the same class of bug as
the nation-side "nation_ruling_characters" gap found and fixed earlier
(see tests/test_ruling_character_scoped_modifiers.py).

Fixed by adding "modifiers" and {"artifacts": [...]} to both schemas'
"leaders" requirement, mirroring nations.json's "rulers" requirement
exactly. This also makes the two brand-new scopes usable:
artifact_owner_ruling_merchant and artifact_owner_ruling_mercenary
(scope_definitions.json), which let an artifact's own modifiers reach the
merchant/mercenary run by whoever owns it.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data, json_data

MERCHANTS_SCHEMA = category_data["merchants"]["schema"]
MERCENARIES_SCHEMA = category_data["mercenaries"]["schema"]


def _collect(db, entity_doc, schema, target_data_type):
    fake_mongo = MagicMock()
    fake_mongo.db = db
    with patch.object(fc, "mongo", fake_mongo):
        return fc.collect_external_requirements(entity_doc, schema, target_data_type)


def _totals(result):
    collected = {}
    for entry in result:
        for k, v in entry.items():
            collected[k] = collected.get(k, 0) + v
    return collected


class TestScopeDefinitionsExist:
    def test_artifact_owner_ruling_merchant_is_defined(self):
        scope = json_data["scope_definitions"]["artifact_owner_ruling_merchant"]
        assert scope["source_type"] == "artifact"
        assert scope["target_type"] == "merchant"

    def test_artifact_owner_ruling_mercenary_is_defined(self):
        scope = json_data["scope_definitions"]["artifact_owner_ruling_mercenary"]
        assert scope["source_type"] == "artifact"
        assert scope["target_type"] == "mercenary"

    def test_grouped_under_artifact_source_type_for_the_ui_dropdown(self):
        """Mirrors routes/__init__.py's scopes_by_source_type grouping —
        this is what makes the new scopes actually show up in the artifact
        edit page's modifier scope dropdown, with no template changes
        needed since it's entirely derived from scope_definitions.json."""
        artifact_scopes = {
            key for key, data in json_data["scope_definitions"].items()
            if data.get("source_type") == "artifact"
        }
        assert "artifact_owner_ruling_merchant" in artifact_scopes
        assert "artifact_owner_ruling_mercenary" in artifact_scopes


class TestCharacterRulingMerchantNowActuallyDelivers:
    """The pre-existing (previously dead) character_ruling_merchant scope."""

    def test_leading_characters_own_modifier_reaches_the_merchant(self):
        client = mongomock.MongoClient()
        db = client["test"]
        merchant_id = ObjectId()
        char_id = ObjectId()

        db["merchants"].insert_one({"_id": merchant_id, "name": "Test Merchant Co"})
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Leader", "ruling_nation_org": str(merchant_id),
            "modifiers": [
                {"modifier_type": "money_income", "scope": "character_ruling_merchant", "value": 50.0, "scaling": "flat"},
            ],
        })

        merchant_doc = db["merchants"].find_one({"_id": merchant_id})
        collected = _totals(_collect(db, merchant_doc, MERCHANTS_SCHEMA, "merchant"))

        assert collected.get("money_income") == 50.0

    def test_other_character_scopes_do_not_leak_to_the_merchant(self):
        client = mongomock.MongoClient()
        db = client["test"]
        merchant_id = ObjectId()
        char_id = ObjectId()

        db["merchants"].insert_one({"_id": merchant_id, "name": "Test Merchant Co"})
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Leader", "ruling_nation_org": str(merchant_id),
            "modifiers": [
                {"modifier_type": "money_income", "scope": "character_ruling_merchant", "value": 50.0, "scaling": "flat"},
                {"modifier_type": "attribute", "scope": "character_self", "attribute": "rulership", "value": 9999.0, "scaling": "flat"},
            ],
        })

        merchant_doc = db["merchants"].find_one({"_id": merchant_id})
        collected = _totals(_collect(db, merchant_doc, MERCHANTS_SCHEMA, "merchant"))

        assert "rulership" not in collected
        assert collected.get("money_income") == 50.0


class TestCharacterRulingMercenaryNowActuallyDelivers:
    def test_leading_characters_own_modifier_reaches_the_mercenary_band(self):
        client = mongomock.MongoClient()
        db = client["test"]
        mercenary_id = ObjectId()
        char_id = ObjectId()

        db["mercenaries"].insert_one({"_id": mercenary_id, "name": "Test Mercenary Band"})
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Commander", "ruling_nation_org": str(mercenary_id),
            "modifiers": [
                {"modifier_type": "money_income", "scope": "character_ruling_mercenary", "value": 30.0, "scaling": "flat"},
            ],
        })

        mercenary_doc = db["mercenaries"].find_one({"_id": mercenary_id})
        collected = _totals(_collect(db, mercenary_doc, MERCENARIES_SCHEMA, "mercenary"))

        assert collected.get("money_income") == 30.0


class TestArtifactOwnerRulingMerchant:
    def test_equipped_artifacts_modifier_reaches_the_owners_merchant(self):
        client = mongomock.MongoClient()
        db = client["test"]
        merchant_id = ObjectId()
        char_id = ObjectId()
        artifact_id = ObjectId()

        db["merchants"].insert_one({"_id": merchant_id, "name": "Test Merchant Co"})
        db["artifacts"].insert_one({
            "_id": artifact_id, "name": "Test Artifact", "owner": str(char_id), "equipped": True,
            "modifiers": [
                {"modifier_type": "money_income", "scope": "artifact_owner_ruling_merchant", "value": 75.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Leader", "ruling_nation_org": str(merchant_id),
            "artifacts": [str(artifact_id)],
        })

        merchant_doc = db["merchants"].find_one({"_id": merchant_id})
        collected = _totals(_collect(db, merchant_doc, MERCHANTS_SCHEMA, "merchant"))

        assert collected.get("money_income") == 75.0

    def test_artifact_owner_scope_targeting_character_does_not_leak_to_merchant(self):
        """artifact_owner's target_type is "character", not "merchant" — must
        never show up when computing the merchant itself."""
        client = mongomock.MongoClient()
        db = client["test"]
        merchant_id = ObjectId()
        char_id = ObjectId()
        artifact_id = ObjectId()

        db["merchants"].insert_one({"_id": merchant_id, "name": "Test Merchant Co"})
        db["artifacts"].insert_one({
            "_id": artifact_id, "name": "Test Artifact", "owner": str(char_id), "equipped": True,
            "modifiers": [
                {"modifier_type": "attribute", "scope": "artifact_owner", "attribute": "rulership", "value": 9999.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Leader", "ruling_nation_org": str(merchant_id),
            "artifacts": [str(artifact_id)],
        })

        merchant_doc = db["merchants"].find_one({"_id": merchant_id})
        collected = _totals(_collect(db, merchant_doc, MERCHANTS_SCHEMA, "merchant"))

        assert "rulership" not in collected


class TestArtifactOwnerRulingMercenary:
    def test_equipped_artifacts_modifier_reaches_the_owners_mercenary_band(self):
        client = mongomock.MongoClient()
        db = client["test"]
        mercenary_id = ObjectId()
        char_id = ObjectId()
        artifact_id = ObjectId()

        db["mercenaries"].insert_one({"_id": mercenary_id, "name": "Test Mercenary Band"})
        db["artifacts"].insert_one({
            "_id": artifact_id, "name": "Test Artifact", "owner": str(char_id), "equipped": True,
            "modifiers": [
                {"modifier_type": "money_income", "scope": "artifact_owner_ruling_mercenary", "value": 40.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Commander", "ruling_nation_org": str(mercenary_id),
            "artifacts": [str(artifact_id)],
        })

        mercenary_doc = db["mercenaries"].find_one({"_id": mercenary_id})
        collected = _totals(_collect(db, mercenary_doc, MERCENARIES_SCHEMA, "mercenary"))

        assert collected.get("money_income") == 40.0

    def test_artifact_owner_ruling_merchant_does_not_leak_into_mercenary_calc(self):
        """A merchant-targeted artifact scope must not accidentally satisfy
        the mercenary's target_type check."""
        client = mongomock.MongoClient()
        db = client["test"]
        mercenary_id = ObjectId()
        char_id = ObjectId()
        artifact_id = ObjectId()

        db["mercenaries"].insert_one({"_id": mercenary_id, "name": "Test Mercenary Band"})
        db["artifacts"].insert_one({
            "_id": artifact_id, "name": "Test Artifact", "owner": str(char_id), "equipped": True,
            "modifiers": [
                {"modifier_type": "money_income", "scope": "artifact_owner_ruling_merchant", "value": 999.0, "scaling": "flat"},
            ],
        })
        db["characters"].insert_one({
            "_id": char_id, "name": "Test Commander", "ruling_nation_org": str(mercenary_id),
            "artifacts": [str(artifact_id)],
        })

        mercenary_doc = db["mercenaries"].find_one({"_id": mercenary_id})
        collected = _totals(_collect(db, mercenary_doc, MERCENARIES_SCHEMA, "mercenary"))

        assert "money_income" not in collected
