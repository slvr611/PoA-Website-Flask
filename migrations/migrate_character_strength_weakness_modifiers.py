"""
Migrate character modifiers from the old raw-field format to the current
modifier_type-based format, regardless of source.

The old format (written historically by generate_ai_character for strength/
weakness bonuses, and also found on many hand-edited player characters from
stat trainings, quest rewards, title bookkeeping, etc.):

    {"field": "cunning", "value": 2, "duration": -1, "source": "Strength"}

The current format:

    {"modifier_type": "attribute", "attribute": "cunning", "value": 2,
     "scope": "character_self", "duration": -1, "source": "Strength", ...}

The old format still numerically applies during calculation
(sum_modifier_totals falls back to the raw "field" key when "modifier_type"
is absent), but with no modifier_type the standard modifier edit UI renders
these rows with a blank "-- Select Modifier --" dropdown and a blank
Attribute field, so from an admin's view they look unset/broken.

A live survey of every old-format entry across all characters found ~3580
entries across ~50 distinct "field" values. Most (>3400) are the six
character attributes and their _cap variants. A confident, mechanical
mapping is applied for:

  - The six attributes (rulership/cunning/charisma/prowess/magic/strategy)
    -> modifier_type "attribute", scope character_self.
  - Their *_cap variants -> modifier_type "attribute_cap", scope character_self.
  - Fields that are themselves an existing character-applicable modifier_type
    key with a literal (non-parameterized) field_template: elderly_age,
    magic_point_income, ignore_elderly, death_chance, slow_aging, immortal,
    heal_chance, magic_point_capacity, artifact_slots -> scope character_self.
  - "nation_"-prefixed fields (nation_stability_loss_chance_on_leader_death,
    nation_money_income, nation_migration_distance, nation_stability_gain_chance)
    -> the matching nation-side modifier_type with the prefix stripped, scope
    character_ruling_nation (same pattern as the built-in Quartermaster title's
    progress_slots modifier — a character-held modifier that affects the
    nation they rule via forward_link on ruling_nation_org).
  - "1_progress_slots" -> modifier_type "progress_slots" with tier=1, scope
    character_ruling_nation.
  - A small set of known typos/case variants (Cunning/Magic/Prowess, the
    misspelling "eldery_age", and "mana_point_income" which doesn't exist as
    a schema field but clearly means magic_point_income) are normalized
    before the above lookup.

Everything else is deliberately left untouched and reported instead of
guessed at:

  - Fields suffixed " (not active)" — someone intentionally disabled that
    entry by breaking the field-key lookup on purpose, without deleting its
    history. Converting it would silently re-activate a modifier someone
    chose to turn off.
  - Fields with no corresponding modifier_type at all (e.g.
    death_chance_per_elderly_age, magic_healing_cost, stat_cap,
    magic_production, nation_bureaucrat_wood_upkeep) — inventing a mapping
    here risks applying a materially different effect than intended, which
    is worse than leaving a working (if UI-invisible) old-format entry alone.
  - Values that aren't real fields at all — free-text title bookkeeping like
    "The Tested One" or "Demon Hunter title in slot 3", evidently used to
    track which title slot holds what, not real modifiers.

Run (dry run by default):
    python -m migrations.migrate_character_strength_weakness_modifiers
    python -m migrations.migrate_character_strength_weakness_modifiers --apply
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_core import app, mongo

_ATTRIBUTES = {"rulership", "cunning", "charisma", "prowess", "magic", "strategy"}

# Case/typo/synonym aliases -> canonical field name, applied before all other
# lookups below. Kept small and explicit — only cases directly confirmed
# against the live data survey, never a fuzzy/guessed match.
_FIELD_ALIASES = {
    "Cunning": "cunning",
    "Magic": "magic",
    "Prowess": "prowess",
    "eldery_age": "elderly_age",
    "mana_point_income": "magic_point_income",
}

# Field name IS the modifier_type key (field_template has no {placeholder}),
# applied with scope=character_self.
_DIRECT_CHARACTER_TYPES = {
    "elderly_age", "magic_point_income", "ignore_elderly", "death_chance",
    "slow_aging", "immortal", "heal_chance", "magic_point_capacity",
    "artifact_slots",
}

# "nation_"-prefixed field on a character means "grant this to my ruling
# nation" -> the matching nation-side modifier_type, scope
# character_ruling_nation.
_RULING_NATION_TYPES = {
    "nation_stability_loss_chance_on_leader_death": "stability_loss_chance_on_leader_death",
    "nation_money_income": "money_income",
    "nation_migration_distance": "migration_distance",
    "nation_stability_gain_chance": "stability_gain_chance",
}

_NOT_ACTIVE_SUFFIX = " (not active)"


def _classify(field):
    """Return (modifier_type, attribute, scope, tier) for a recognized field,
    or None if it can't be safely mapped."""
    if not isinstance(field, str):
        return None
    if field.endswith(_NOT_ACTIVE_SUFFIX):
        return None

    canonical = _FIELD_ALIASES.get(field, field)

    if canonical in _ATTRIBUTES:
        return ("attribute", canonical, "character_self", None)

    if canonical.endswith("_cap") and canonical[:-4] in _ATTRIBUTES:
        return ("attribute_cap", canonical[:-4], "character_self", None)

    if canonical in _DIRECT_CHARACTER_TYPES:
        return (canonical, None, "character_self", None)

    if canonical in _RULING_NATION_TYPES:
        return (_RULING_NATION_TYPES[canonical], None, "character_ruling_nation", None)

    if canonical == "1_progress_slots":
        return ("progress_slots", None, "character_ruling_nation", 1)

    return None


def _is_old_format(m):
    return isinstance(m, dict) and "modifier_type" not in m


def _build_modifier(classification, m):
    modifier_type, attribute, scope, tier = classification
    return {
        "modifier_type": modifier_type,
        "scaling": "flat",
        "scaling_x": None,
        "scaling_extra": "",
        "scope": scope,
        "resource": "",
        "resource_from": "",
        "resource_to": "",
        "job": "",
        "attribute": attribute or "",
        "unit_category": "",
        "unit_stat": "",
        "tier": tier if tier is not None else "",
        "tech_category": "",
        "terrain": "",
        "node_resource": None,
        "terrain_as": None,
        "target_type": "",
        "target_value": "",
        "unit_name": "",
        "district_key": "",
        "value": m.get("value", 0),
        "max_value": None,
        "duration": m.get("duration", -1),
        "source": m.get("source", ""),
        "condition_scaling": "",
        "condition_scaling_x": 1.0,
        "condition_scaling_extra": "",
        "condition_operator": ">=",
        "condition_value": None,
        "_id": m.get("_id") or uuid.uuid4().hex[:8],
    }


def run(dry_run=True):
    with app.app_context():
        candidates = list(mongo.db.characters.find(
            {"modifiers": {"$elemMatch": {"modifier_type": {"$exists": False}}}},
            {"name": 1, "modifiers": 1},
        ))

        to_update = []
        skipped = []  # (name, field, source, value, reason)
        migrated_type_counts = {}

        for char in candidates:
            modifiers = char.get("modifiers", [])
            changed = False
            new_modifiers = []
            for m in modifiers:
                if _is_old_format(m):
                    field = m.get("field")
                    classification = _classify(field)
                    if classification:
                        new_modifiers.append(_build_modifier(classification, m))
                        migrated_type_counts[classification[0]] = migrated_type_counts.get(classification[0], 0) + 1
                        changed = True
                        continue
                    else:
                        reason = "not active" if isinstance(field, str) and field.endswith(_NOT_ACTIVE_SUFFIX) else "no known modifier_type"
                        skipped.append((char.get("name", "Unknown"), field, m.get("source", ""), m.get("value"), reason))
                new_modifiers.append(m)
            if changed:
                to_update.append((char["_id"], char.get("name", "Unknown"), new_modifiers))

        total_migrated = sum(migrated_type_counts.values())
        print(f"Found {len(to_update)} character(s) with old-format modifiers to migrate "
              f"({total_migrated} entries total) — {'DRY RUN, no changes' if dry_run else 'will update'}.")
        print("\nBy resulting modifier_type:")
        for mt, count in sorted(migrated_type_counts.items(), key=lambda x: -x[1]):
            print(f"  {mt}: {count}")

        if skipped:
            print(f"\nSkipped {len(skipped)} entr(y/ies) left untouched (no safe mapping):")
            for name, field, source, value, reason in skipped:
                print(f"  {name}: field={field!r} value={value} source={source!r} — {reason}")

        if not dry_run and to_update:
            for _id, name, new_modifiers in to_update:
                mongo.db.characters.update_one(
                    {"_id": _id},
                    {"$set": {"modifiers": new_modifiers}},
                )
            print(f"\nMigrated {len(to_update)} character(s).")
        elif dry_run:
            print("\n(dry_run=True — re-run with --apply to write changes)")


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    if dry:
        print("DRY RUN mode. Pass --apply to actually migrate modifiers.\n")
    run(dry_run=dry)
