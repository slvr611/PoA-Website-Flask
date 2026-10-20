"""
One-off data correction: district_duration_tick (helpers/tick_helpers.py) was
fixed to properly increment an EXISTING district_session_count modifier
(matched by modifier_type + district_key) each session instead of appending
a brand new duplicate entry every tick. That fix only stops NEW duplicates
going forward -- it doesn't clean up the duplicate entries that already
piled up on nations before it was deployed (one extra entry per tick the
bug ran -- e.g. Vandador has 6 separate entries for its forge district and
6 more for its workshop district).

per_x_district_sessions (calculations/scaling_methods.py), the scaling
method that actually reads these counters for gameplay effects, aggregates
every matching entry -- district_session_count by district_key, plus
legacy district_duration entries whose source text contains the district's
def_key or display_name -- with max(), not sum(). That turns out to be the
WRONG aggregation for this bug's failure mode, confirmed by replaying
Vandador's "Tick Update for Vandador" change-history (sessions 81-84):
district_duration_tick touches exactly one modifier per district per tick
(it `break`s after the first increment-or-append), so every extra fragment
entry the pre-fix bug created represents one additional, non-overlapping
session the district was genuinely active for -- not a redundant re-count
of the same session. Taking max() of the group therefore silently
UNDER-counts: Vandador's forge/workshop should each show 9 sessions (the
sum of [1,1,1,1,1,4]), not 4. This script restores that true total by
summing each group's values into the single surviving entry, exactly
preserving what per_x_district_sessions would read once there is only one
entry left to take the max/sum of.

For every nation, this:
  1. Finds all district_session_count entries, grouped by district_key.
  2. Finds legacy district_duration entries and folds one into an existing
     group ONLY when that nation already has a modern district_session_
     count entry for the same district_key (matched via the same fuzzy
     source-text check per_x_district_sessions uses: def_key or
     display_name as a case-insensitive substring of `source`, checked
     against every known district_defs key, not just the nation's current
     districts). Two bare legacy entries are never folded into each other
     purely off a text-substring match -- that produced a false positive
     for Lusariyya ("Library District" and "Doubled Library" both contain
     "library" but are not actually duplicates of the same counter; the
     fuzzy match can't tell a genuine duplicate from a differently-named,
     intentionally distinct modifier without a modern entry to anchor it).
  3. For every (nation, district_key) group with more than one contributing
     entry, keeps the entry with the highest value (normalizing its
     modifier_type/district_key/field to the clean district_session_count
     shape if it was a legacy district_duration entry), sets its value to
     the SUM of the whole group, and drops the rest.
  Groups with exactly one entry are left untouched -- already unambiguous,
  whether in the new or legacy format, and not part of the bug being fixed.

Vandador's orphaned legacy "Library District" entry (district it no longer
owns, value 19) is separately, explicitly removed -- confirmed dead data,
not something the general folding logic should guess at for other nations.

Writes go through system_request_change + system_approve_change per
nation, same as scripts/correct_premature_tick_karma_roll.py, so each is a
normal, audited "Consolidate District Session Modifiers for X" change.

SAFE BY DEFAULT: dry run only, no writes, unless run with --apply.

Run from the project root:
  python -m scripts.consolidate_district_session_modifiers            # preview only
  python -m scripts.consolidate_district_session_modifiers --apply    # writes for real
"""
import argparse
import sys
from collections import defaultdict
from copy import deepcopy

from app_core import mongo
from helpers.change_helpers import system_request_change, system_approve_change

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _to_num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _district_defs_by_key():
    return {d["key"]: d for d in mongo.db.district_defs.find({}) if d.get("key")}


def _attribute_legacy_entry(source, defs_by_key):
    """district_key a legacy district_duration entry's source text matches,
    via the same substring check per_x_district_sessions uses. Returns None
    if it matches zero or more than one district_key (ambiguous)."""
    src = (source or "").lower()
    if not src:
        return None
    matches = set()
    for dk, d in defs_by_key.items():
        display_name = (d.get("display_name") or "").lower()
        if dk.lower() in src or (display_name and display_name in src):
            matches.add(dk)
    if len(matches) == 1:
        return matches.pop()
    return None


def plan_for_nation(nation, defs_by_key):
    """{district_key: [entries]} for every district_key with >1
    contributing entry, plus a list of legacy entries that matched a
    district_key but had no modern entry to fold into (left untouched,
    not reported as a problem -- just not part of this bug)."""
    modifiers = nation.get("modifiers", []) or []
    groups = defaultdict(list)
    unfolded_legacy = []

    for m in modifiers:
        if not isinstance(m, dict):
            continue
        if m.get("modifier_type") == "district_session_count":
            dk = m.get("district_key", "")
            if dk:
                groups[dk].append(m)

    for m in modifiers:
        if not isinstance(m, dict) or m.get("modifier_type") != "district_duration":
            continue
        dk = _attribute_legacy_entry(m.get("source"), defs_by_key)
        if dk and dk in groups:
            groups[dk].append(m)
        else:
            unfolded_legacy.append(m)

    dupe_groups = {dk: g for dk, g in groups.items() if len(g) > 1}
    return dupe_groups, unfolded_legacy


def consolidate(new_nation, dupe_groups, defs_by_key):
    """Mutates new_nation['modifiers'] in place: for each group, keeps the
    highest-value entry (normalized), sets its value to the sum of the
    whole group -- each duplicate entry represents one additional, non-
    overlapping session the district was active for, not a redundant
    re-count (see module docstring) -- and drops the rest. Returns
    per-group summary strings."""
    summaries = []
    remove_ids = set()

    for dk, group in dupe_groups.items():
        keeper = max(group, key=lambda m: _to_num(m.get("value", 0)))
        total_value = sum(_to_num(m.get("value", 0)) for m in group)
        for m in group:
            if m is not keeper:
                remove_ids.add(id(m))

        keeper["modifier_type"] = "district_session_count"
        keeper["district_key"] = dk
        keeper["value"] = total_value
        field = keeper.get("field") or ""
        if not field.startswith("district_sessions_"):
            keeper["field"] = f"district_sessions_{dk}"

        display_name = defs_by_key.get(dk, {}).get("display_name", dk)
        values = sorted(_to_num(m.get("value", 0)) for m in group)
        summaries.append(
            f"{display_name} ({dk}): {len(group)} entries {values} -> 1 entry value={total_value}"
        )

    new_nation["modifiers"] = [
        m for m in new_nation.get("modifiers", []) if id(m) not in remove_ids
    ]
    return summaries


def remove_vandador_orphaned_library(new_nation):
    """Vandador no longer owns a library district; its leftover legacy
    'Library District' district_duration entry (value 19) is confirmed
    dead data, explicitly authorized for removal -- not a general rule for
    other nations' orphaned legacy entries, which are left for manual
    review."""
    if new_nation.get("name") != "Vandador":
        return False
    before = new_nation.get("modifiers", []) or []
    after = [
        m for m in before
        if not (
            isinstance(m, dict)
            and m.get("modifier_type") == "district_duration"
            and (m.get("source") or "").strip().lower() == "library district"
        )
    ]
    if len(after) == len(before):
        return False
    new_nation["modifiers"] = after
    return True


def main(apply: bool):
    defs_by_key = _district_defs_by_key()
    nations = list(mongo.db.nations.find({}).sort("name", 1))
    print(f"Checking {len(nations)} nations for duplicate district session modifiers.\n")

    plan = []
    for nation in nations:
        dupe_groups, _unfolded = plan_for_nation(nation, defs_by_key)
        new_nation = deepcopy(nation)
        vandador_fix = remove_vandador_orphaned_library(new_nation)

        summaries = []
        if dupe_groups:
            new_dupe_groups, _ = plan_for_nation(new_nation, defs_by_key)
            summaries = consolidate(new_nation, new_dupe_groups, defs_by_key)

        if summaries or vandador_fix:
            plan.append({
                "nation": nation, "new_nation": new_nation,
                "summaries": summaries, "vandador_fix": vandador_fix,
            })

    total_groups = sum(len(p["summaries"]) for p in plan)
    total_removed = sum(
        len(p["nation"].get("modifiers", [])) - len(p["new_nation"].get("modifiers", []))
        for p in plan
    )
    print(f"Nations with duplicate groups to consolidate: {sum(1 for p in plan if p['summaries'])}")
    print(f"Total groups consolidated: {total_groups}")
    print(f"Total redundant entries removed: {total_removed}\n")

    print("Per-nation detail:")
    for p in plan:
        name = p["nation"].get("name", "?")
        print(f"  {name}:")
        for s in p["summaries"]:
            print(f"    {s}")
        if p["vandador_fix"]:
            print("    removed orphaned legacy 'Library District' entry (value 19)")

    if not apply:
        print("\nDRY RUN -- no changes written. Re-run with --apply to commit.")
        return

    print("\nAPPLYING -- writing changes now...")
    written = 0
    for p in plan:
        nation = p["nation"]
        new_nation = p["new_nation"]
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason=(
                f"Consolidate District Session Modifiers for {nation.get('name', '?')} "
                f"(cleans up duplicate district_session_count/legacy district_duration "
                f"entries left behind by the pre-fix district_duration_tick bug; each "
                f"group is merged into a single entry whose value is the sum of the "
                f"group, restoring the true number of non-overlapping sessions the "
                f"district has been active for)"
            ),
        )
        if change_id is not None:
            ok = system_approve_change(change_id)
            if ok:
                written += 1
            else:
                print(f"  FAILED to approve change for {nation.get('name', '?')} (change_id={change_id})")
        else:
            print(f"  FAILED to request change for {nation.get('name', '?')}")

    print(f"\nDone. {written}/{len(plan)} nations updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = parser.parse_args()
    main(apply=args.apply)
