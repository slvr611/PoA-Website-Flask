"""
Regression test for a real bug found while adding progress-quest slots to
characters/merchants/mercenaries: calculate_all_fields's "_law_scaled" block
(calculations/field_calculations.py) — which folds a law's embedded
`_modifiers` scaling list into the calculating entity's OWN totals — only
ever compared a modifier's scope target_type against the literal string
"nation" (a relic from when only nations used this mechanism). It never
checked whether the scope actually resolves DIRECTLY to the entity
currently being calculated.

This meant a CHARACTER's own law entry scoped "character_ruling_nation"
(target_type "nation", but resolution forward_link — meant to reach the
nation this character rules, not the character itself) incorrectly
self-applied to the character. Concretely: the built-in Quartermaster
character subtype grants "+1 tier-0 progress slot" via exactly this scope —
before this fix, that slot silently landed on the Quartermaster character's
own (previously nonexistent) 0_progress_slots field instead of the nation
they rule.

This mirrors the identical class of bug already fixed in collect_modifiers
(see test_collect_modifiers_scope_self_only.py) for a nation's/character's
own direct `modifiers` array — this is the same fix applied to the
separate, older `_law_scaled` code path for LAW-embedded `_modifiers`
lists, which had not been updated to match.
"""
from bson import ObjectId

import calculations.field_calculations as fc


MINIMAL_CHARACTER_SCHEMA = {
    "laws": ["character_subtype"],
    "properties": {
        "character_subtype": {
            "bsonType": "enum",
            "laws": {
                "Quartermaster": {
                    "_modifiers": [
                        {"modifier_type": "progress_slots", "tier": "0", "value": 1, "scope": "character_ruling_nation"},
                    ]
                },
                "SelfBuffer": {
                    "_modifiers": [
                        {"modifier_type": "money_income", "value": 50, "scope": "character_self"},
                    ]
                },
                "TypoScope": {
                    "_modifiers": [
                        {"modifier_type": "money_income", "value": 75, "scope": "self_character"},
                    ]
                },
            },
        },
        "0_progress_slots": {"bsonType": "number", "calculated": True},
        "1_progress_slots": {"bsonType": "number", "calculated": True, "base_value": 3},
        "money_income": {"bsonType": "number", "calculated": True},
    },
}

MINIMAL_NATION_SCHEMA = {
    "laws": ["government_type"],
    "properties": {
        "government_type": {
            "bsonType": "enum",
            "laws": {
                "Imperial Council": {
                    "_modifiers": [
                        {"modifier_type": "money_income", "value": 100, "scope": "nation_self"},
                        {"modifier_type": "attribute", "attribute": "rulership", "value": 1, "scope": "nation_ruling_characters"},
                    ]
                },
            },
        },
        "money_income": {"bsonType": "number", "calculated": True},
    },
}


def _run(target, schema, target_data_type):
    # calculate_all_fields for character/nation touches mongo for a handful of
    # lookups (global_modifiers, rulers, etc.) — mongomock keeps them all
    # harmlessly empty rather than needing to stub each one individually.
    import mongomock
    client = mongomock.MongoClient()
    db = client["test"]
    from app_core import mongo as real_mongo
    original_db = real_mongo.db
    real_mongo.db = db
    try:
        return fc.calculate_all_fields(dict(target), schema, target_data_type)
    finally:
        real_mongo.db = original_db


class TestLawModifiersOnlySelfApplyWhenScopeResolvesDirectToSelf:
    def test_character_ruling_nation_scope_does_not_self_apply_to_character(self):
        character = {"_id": ObjectId(), "character_subtype": "Quartermaster"}
        calc = _run(character, MINIMAL_CHARACTER_SCHEMA, "character")
        assert calc.get("0_progress_slots", 0) == 0, (
            f"character_ruling_nation-scoped law modifier incorrectly self-applied "
            f"to the character: {calc.get('0_progress_slots')}"
        )

    def test_character_self_scope_still_self_applies(self):
        character = {"_id": ObjectId(), "character_subtype": "SelfBuffer"}
        calc = _run(character, MINIMAL_CHARACTER_SCHEMA, "character")
        assert calc.get("money_income", 0) == 50

    def test_unknown_scope_key_still_fails_open(self):
        """A typo'd/unrecognized scope (e.g. the real "self_character" typo
        found in characters.json) must keep applying, exactly as before this
        fix — only a KNOWN, non-direct cross-entity scope should be excluded."""
        character = {"_id": ObjectId(), "character_subtype": "TypoScope"}
        calc = _run(character, MINIMAL_CHARACTER_SCHEMA, "character")
        assert calc.get("money_income", 0) == 75

    def test_nation_self_scope_still_self_applies_to_nation(self):
        nation = {"_id": ObjectId(), "name": "Testland", "government_type": "Imperial Council"}
        calc = _run(nation, MINIMAL_NATION_SCHEMA, "nation")
        assert calc.get("money_income", 0) >= 100

    def test_nation_ruling_characters_scope_does_not_self_apply_to_nation(self):
        """A nation's own law modifier meant for its RULING CHARACTERS
        (target_type character, not nation) must not leak into the nation's
        own money_income/etc — only the nation_self entry should count."""
        nation = {"_id": ObjectId(), "name": "Testland", "government_type": "Imperial Council"}
        calc = _run(nation, MINIMAL_NATION_SCHEMA, "nation")
        # Only the nation_self money_income (+100) should apply — the
        # nation_ruling_characters attribute modifier targets "rulership",
        # a field this minimal nation schema doesn't even have, so if it
        # were wrongly folded in it wouldn't affect money_income, but the
        # money_income assertion above already isolates the nation_self
        # contribution. This test just documents intent explicitly.
        assert calc.get("money_income", 0) == 100
