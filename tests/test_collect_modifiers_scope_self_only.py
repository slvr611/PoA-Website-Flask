"""
Regression test for a real reported bug: Tsetsl (a vassal of The United
Duchies) has its own modifier scoped "nation_vassals" (+50% compliance_loss_chance,
meant for ITS OWN vassals) — but that modifier was also being applied to
Tsetsl itself, on top of the identical modifier correctly inherited from its
overlord, effectively doubling the intended penalty.

Root cause: collect_modifiers (calculations/field_calculations.py), which
decides which of a nation's/character's own modifiers apply to its OWN
calculation, only checked that a scope's target_type matched the entity's
own type. nation_vassals and nation_overlord both have target_type "nation"
too (scope_definitions.json), but they resolve to a DIFFERENT linked nation
(a vassal or an overlord) — not the entity that owns the modifier. Matching
on target_type alone couldn't tell "nation_self" apart from "nation_vassals"/
"nation_overlord", so a nation's own vassal-directed or overlord-directed
modifier incorrectly applied to itself as well as flowing outward correctly
via collect_external_requirements.

The fix checks scope_definitions[scope]["resolution"]["type"] == "direct"
instead — "nation_self"/"character_self"/etc. are the only scopes with
direct resolution (a scope literally means "this entity"), so this is an
unambiguous test for "does this scope apply to the owning entity itself".
"""
from app_core import json_data
import calculations.field_calculations as fc


class TestCollectModifiersOnlyKeepsSelfScopedEntries:
    def test_nation_vassals_scope_does_not_self_apply(self):
        nation = {
            "modifiers": [
                {"modifier_type": "compliance_loss_chance", "scope": "nation_vassals", "value": 0.5},
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert kept == [], f"nation_vassals-scoped modifier incorrectly applied to its own nation: {kept}"

    def test_nation_overlord_scope_does_not_self_apply(self):
        nation = {
            "modifiers": [
                {"modifier_type": "money_income", "scope": "nation_overlord", "value": 100},
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert kept == [], f"nation_overlord-scoped modifier incorrectly applied to its own nation: {kept}"

    def test_nation_self_scope_still_applies(self):
        nation = {
            "modifiers": [
                {"modifier_type": "money_income", "scope": "nation_self", "value": 100},
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert len(kept) == 1, f"nation_self-scoped modifier should still apply to its own nation: {kept}"

    def test_unscoped_legacy_modifier_still_applies(self):
        nation = {
            "modifiers": [
                {"modifier_type": "money_income", "value": 100},  # no "scope" key at all
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert len(kept) == 1, "unscoped (legacy) modifiers must still apply to their own entity"

    def test_unknown_scope_key_fails_open(self):
        """A renamed/removed scope key shouldn't silently drop a real modifier."""
        nation = {
            "modifiers": [
                {"modifier_type": "money_income", "scope": "some_scope_that_does_not_exist", "value": 100},
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert len(kept) == 1

    def test_nation_ruling_characters_scope_does_not_self_apply_to_nation(self):
        """Sanity: a nation's modifier meant for its ruling characters
        (target_type character, not nation) must also not leak into the
        nation's own calculation."""
        nation = {
            "modifiers": [
                {"modifier_type": "attribute", "scope": "nation_ruling_characters", "attribute": "rulership", "value": 1},
            ],
        }
        kept = fc.collect_modifiers(nation, "nation")
        assert kept == []

    def test_character_ruling_nation_scope_does_not_self_apply_to_character(self):
        """Mirror check on the character side: character_ruling_nation
        (target_type nation) must not apply to the character's own
        calculation, only character_self should."""
        character = {
            "modifiers": [
                {"modifier_type": "money_income", "scope": "character_ruling_nation", "value": 50},
                {"modifier_type": "stat_gain_chance", "scope": "character_self", "value": 0.1},
            ],
        }
        kept = fc.collect_modifiers(character, "character")
        assert len(kept) == 1
        assert kept[0]["scope"] == "character_self"

    def test_scope_definitions_direct_resolution_assumption_holds(self):
        """Guard the core assumption this fix relies on: every *_self scope
        (and only those) resolves as "direct". If a future scope breaks this
        pattern, this test should fail loudly rather than the bug silently
        coming back."""
        scope_defs = json_data.get("scope_definitions", {})
        for key, sdef in scope_defs.items():
            is_direct = sdef.get("resolution", {}).get("type") == "direct"
            if key.endswith("_self"):
                assert is_direct, f"{key} ends in _self but isn't a direct-resolution scope"
            if sdef.get("source_type") == sdef.get("target_type") and is_direct:
                assert key.endswith("_self"), f"{key} is a same-type direct scope but isn't named *_self"
