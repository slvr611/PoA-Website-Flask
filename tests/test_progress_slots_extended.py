"""
Regression tests for extending progress-quest slots (previously nation-only)
to characters, merchants, and mercenary companies: each now has a base of 3
"1_progress_slot"-tier slots (1 progress per session), reusing the exact
same 0-4_progress_slots calculated fields, progress_quests/total_progress_per_tick
computation, and progress_slots modifier_type nations already used — just
made applicable to the three new entity types, with the corresponding self
scopes (character_self/merchant_self/mercenary_self) and, for characters,
artifact_owner already in scope_definitions.json.
"""
import mongomock
from bson import ObjectId

from app_core import mongo as real_mongo, category_data
from calculations.field_calculations import calculate_all_fields


def _calc(entity_type, singular, target, db=None):
    original_db = real_mongo.db
    if db is not None:
        real_mongo.db = db
    try:
        schema = category_data[entity_type]["schema"]
        return calculate_all_fields(dict(target), schema, singular)
    finally:
        real_mongo.db = original_db


class TestBaseProgressSlots:
    def test_character_has_3_base_tier_1_slots(self):
        calc = _calc("characters", "character", {"name": "Test", "progress_quests": []})
        assert calc.get("1_progress_slots") == 3
        assert calc.get("0_progress_slots") == 0

    def test_merchant_has_3_base_tier_1_slots(self):
        calc = _calc("merchants", "merchant", {"name": "Test", "progress_quests": []})
        assert calc.get("1_progress_slots") == 3

    def test_mercenary_has_3_base_tier_1_slots(self):
        calc = _calc("mercenaries", "mercenary", {"name": "Test", "progress_quests": []})
        assert calc.get("1_progress_slots") == 3


class TestProgressQuestsActuallyProgressForEachEntityType:
    """total_progress_per_tick is computed generically (calculate_all_fields's
    unconditional 'if "progress_quests" in target' block) — confirm a quest
    assigned to the 1_progress_slot tier actually gets +1/tick for all three
    entity types, matching what nations already get."""

    def test_character_quest_progresses_by_one(self):
        calc = _calc("characters", "character", {
            "name": "Test",
            "progress_quests": [{"_id": "q1", "quest_name": "Study", "slot": "1_progress_slot",
                                  "current_progress": 0, "required_progress": 10}],
        })
        assert calc["progress_quests"][0]["total_progress_per_tick"] == 1

    def test_merchant_quest_progresses_by_one(self):
        calc = _calc("merchants", "merchant", {
            "name": "Test",
            "progress_quests": [{"_id": "q1", "quest_name": "Trade Route", "slot": "1_progress_slot",
                                  "current_progress": 0, "required_progress": 10}],
        })
        assert calc["progress_quests"][0]["total_progress_per_tick"] == 1

    def test_mercenary_quest_progresses_by_one(self):
        calc = _calc("mercenaries", "mercenary", {
            "name": "Test",
            "progress_quests": [{"_id": "q1", "quest_name": "Training", "slot": "1_progress_slot",
                                  "current_progress": 0, "required_progress": 10}],
        })
        assert calc["progress_quests"][0]["total_progress_per_tick"] == 1


class TestProgressSlotsModifierApplicableToNewEntityTypes:
    def test_progress_slots_modifier_type_applicable_to_new_entities(self):
        from app_core import json_data
        applicable_to = json_data["modifier_types"]["progress_slots"]["applicable_to"]
        for t in ("nation", "character", "merchant", "mercenary"):
            assert t in applicable_to

    def test_character_self_scoped_direct_modifier_grants_extra_slot(self):
        character = {
            "name": "Test",
            "progress_quests": [],
            "modifiers": [
                {"modifier_type": "progress_slots", "tier": "1", "value": 2, "scope": "character_self", "scaling": "flat"},
            ],
        }
        calc = _calc("characters", "character", character)
        assert calc.get("1_progress_slots") == 5  # 3 base + 2 from the direct modifier

    def test_merchant_self_scoped_direct_modifier_grants_extra_slot(self):
        merchant = {
            "name": "Test",
            "progress_quests": [],
            "modifiers": [
                {"modifier_type": "progress_slots", "tier": "2", "value": 1, "scope": "merchant_self", "scaling": "flat"},
            ],
        }
        calc = _calc("merchants", "merchant", merchant)
        assert calc.get("2_progress_slots") == 1

    def test_mercenary_self_scoped_direct_modifier_grants_extra_slot(self):
        mercenary = {
            "name": "Test",
            "progress_quests": [],
            "modifiers": [
                {"modifier_type": "progress_slots", "tier": "1", "value": 1, "scope": "mercenary_self", "scaling": "flat"},
            ],
        }
        calc = _calc("mercenaries", "mercenary", mercenary)
        assert calc.get("1_progress_slots") == 4  # 3 base + 1


class TestArtifactGrantsProgressSlotsToOwningCharacter:
    def test_equipped_artifact_progress_slots_modifier_reaches_owner(self):
        client = mongomock.MongoClient()
        db = client["test"]
        char_id = ObjectId()
        art_id = ObjectId()
        db["characters"].insert_one({"_id": char_id, "name": "Odirile"})
        db["artifacts"].insert_one({
            "_id": art_id, "name": "Scroll Case", "owner": str(char_id), "equipped": True,
            "modifiers": [
                {"modifier_type": "progress_slots", "tier": "1", "value": 2, "scope": "artifact_owner", "scaling": "flat"},
            ],
        })
        char_doc = db["characters"].find_one({"_id": char_id})
        calc = _calc("characters", "character", char_doc, db=db)
        assert calc.get("1_progress_slots") == 5  # 3 base + 2 from the equipped artifact

    def test_unequipped_artifact_does_not_grant_slots(self):
        client = mongomock.MongoClient()
        db = client["test"]
        char_id = ObjectId()
        art_id = ObjectId()
        db["characters"].insert_one({"_id": char_id, "name": "Odirile"})
        db["artifacts"].insert_one({
            "_id": art_id, "name": "Scroll Case", "owner": str(char_id), "equipped": False,
            "modifiers": [
                {"modifier_type": "progress_slots", "tier": "1", "value": 2, "scope": "artifact_owner", "scaling": "flat"},
            ],
        })
        char_doc = db["characters"].find_one({"_id": char_id})
        calc = _calc("characters", "character", char_doc, db=db)
        assert calc.get("1_progress_slots") == 3  # base only, artifact not equipped
