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
from contextlib import contextmanager

import mongomock
from bson import ObjectId
from wtforms.validators import ValidationError

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


class TestValidateProgressQuestsPerTierForNonNationEntities:
    """Regression tests for the reported bug: BaseSchemaForm.validate_progress_quests's
    non-nation branch only ever tallied quests literally assigned to
    "1_progress_slot" and compared that count against a hardcoded 3 — ignoring
    tiers 0/2/3/4 entirely and rejecting a merchant/mercenary/character whose
    progress_slots modifiers legitimately granted MORE than 3 tier-1 slots.
    The fix makes this branch do the same per-tier slot_usage-vs-available_count
    comparison the (already correct) nation branch does, sourcing "available"
    from the entity's real calculated 0-4_progress_slots fields via a DB lookup
    (since those are "calculated": true fields and never present on form data).
    """

    def _quest(self, slot, qid="q"):
        return {
            "_id": qid, "quest_name": "Quest", "slot": slot,
            "current_progress": 0, "required_progress": 10,
        }

    @contextmanager
    def _form_for(self, flask_app, data_type, name, quests, db_fields):
        from forms import form_generator
        schema = category_data[data_type]["schema"]
        item = {"_id": ObjectId(), "name": name, "modifiers": [], "progress_quests": quests}

        db = mongomock.MongoClient()["test"][data_type]
        db.insert_one({"name": name, **db_fields})
        original_db = category_data[data_type]["database"]
        category_data[data_type]["database"] = db
        try:
            with flask_app.test_request_context(f"/{data_type}/edit/{name}"):
                yield form_generator.get_form(data_type, schema, item=item)
        finally:
            category_data[data_type]["database"] = original_db

    def test_merchant_with_modifier_granting_5_tier_1_slots_allows_4_quests(self, flask_app):
        """Previously rejected outright: 4 > hardcoded cap of 3, even though
        this merchant's progress_slots modifier legitimately grants 5."""
        with self._form_for(
            flask_app, "merchants", "Merchant A",
            [self._quest("1_progress_slot", f"q{i}") for i in range(4)],
            {"1_progress_slots": 5},
        ) as form:
            form.validate_progress_quests(None)  # must not raise

    def test_merchant_base_3_slots_rejects_a_4th_tier_1_quest(self, flask_app):
        """Baseline (no slot-granting modifier) must still correctly cap at
        the entity's real available count (3), with an accurate message."""
        with self._form_for(
            flask_app, "merchants", "Merchant B",
            [self._quest("1_progress_slot", f"q{i}") for i in range(4)],
            {"1_progress_slots": 3},
        ) as form:
            try:
                form.validate_progress_quests(None)
                assert False, "expected ValidationError"
            except ValidationError as e:
                assert "Available: 3, Used: 4" in str(e)

    def test_mercenary_tier_2_overuse_is_now_caught(self, flask_app):
        """Previously tiers 2-4 were never validated at all — only the
        literal string "1_progress_slot" was ever tallied."""
        with self._form_for(
            flask_app, "mercenaries", "Mercenary A",
            [self._quest("2_progress_slot", "q1"), self._quest("2_progress_slot", "q2")],
            {"1_progress_slots": 3, "2_progress_slots": 1},
        ) as form:
            try:
                form.validate_progress_quests(None)
                assert False, "expected ValidationError"
            except ValidationError as e:
                assert "Available: 1, Used: 2" in str(e)

    def test_character_tier_2_within_limit_passes(self, flask_app):
        with self._form_for(
            flask_app, "characters", "Character A",
            [self._quest("1_progress_slot", "q1"), self._quest("2_progress_slot", "q2")],
            {"1_progress_slots": 3, "2_progress_slots": 1},
        ) as form:
            form.validate_progress_quests(None)  # must not raise


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
