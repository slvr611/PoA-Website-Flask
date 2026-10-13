"""
One-off data correction: the session-84 tick was accidentally run BEFORE
clicking "Roll Events" on the karma helper (/karma_helper) for this session.

What went wrong: "Nation Update Rolling Karma Tick" (update_rolling_karma in
helpers/tick_helpers.py) reads each nation's CURRENT event_type (set by the
LAST roll_events() call — from a previous session, not this one) and nudges
rolling_karma by +1 (bad event) or -1 (good event) accordingly. Since this
session's events were never rolled first, that adjustment was based on
stale, wrong data instead of what this session's actual roll would have
produced.

karma (a calculated field) = base + modifiers + rolling_karma + temporary_karma
(see compute_field_karma in calculations/compute_functions.py) — rolling_karma
enters that formula purely additively with no other interactions, so the
wrong rolling_karma bump shifted each affected nation's current karma by
exactly the same amount, in the same direction.

This script, for every nation:
  1. Looks up how much (if any) rolling_karma changed for it in session 84's
     "Tick Update for X" change record (before/after diff in `changes`).
  2. Reverts that delta from both rolling_karma and the derived karma value,
     recovering what karma would have been immediately before the tick ran.
  3. Rolls a fresh event using that corrected karma — identical roll/
     classification logic to roll_events() (helpers/admin_tool_helpers.py).
  4. Applies the CORRECT rolling_karma adjustment for the fresh event type —
     identical law-aware logic to update_rolling_karma (respecting
     ignore_negative_event_karma / ignore_positive_event_karma).
  5. Writes event_type/event_roll/raw_roll/temporary_karma/previous_* fields
     exactly like roll_events() does, via system_request_change +
     system_approve_change, so it's a normal, audited "Karma Roll for X"
     change like any other.

SAFE BY DEFAULT: dry run only, no writes, unless run with --apply.

Run from the project root:
  python scripts/correct_premature_tick_karma_roll.py            # preview only
  python scripts/correct_premature_tick_karma_roll.py --apply    # writes for real
"""
import argparse
import random
from copy import deepcopy

from bson import ObjectId

from app_core import mongo, category_data
from helpers.change_helpers import system_request_change, system_approve_change
from calculations.field_calculations import collect_laws, sum_law_totals

SESSION_NUMBER = 84


def classify_event(raw_roll, event_roll):
    """Identical to roll_events()'s own classification (helpers/admin_tool_helpers.py)."""
    if raw_roll == 20 and event_roll >= 30:
        return "Wonderous"
    elif raw_roll == 20 or event_roll >= 23:
        return "Fantastic"
    elif raw_roll == 1:
        return "Abysmal"
    elif event_roll >= 18:
        return "Very Good"
    elif event_roll >= 15:
        return "Good"
    elif event_roll >= 7:
        return "Neutral"
    elif event_roll >= 4:
        return "Bad"
    elif event_roll >= 2:
        return "Very Bad"
    elif event_roll >= -9:
        return "Abysmal"
    else:
        return "Horrendous"


def correct_rolling_karma_delta(nation, schema, event_type):
    """Identical law-aware logic to update_rolling_karma (helpers/tick_helpers.py)."""
    law_totals = sum_law_totals(collect_laws(nation, schema))
    ignore_negative = law_totals.get("ignore_negative_event_karma", 0) >= 1
    ignore_positive = law_totals.get("ignore_positive_event_karma", 0) >= 1

    if event_type in ("Horrendous", "Abysmal", "Very Bad", "Bad"):
        return 0 if ignore_negative else 1
    elif event_type in ("Good", "Very Good", "Fantastic", "Wonderous"):
        return 0 if ignore_positive else -1
    return 0


def find_wrong_rolling_karma_deltas(session_number):
    """{nation_id: delta} for every nation whose rolling_karma changed in
    this session's tick (the mistaken update_rolling_karma application)."""
    changes = mongo.db.changes.find({
        "target_collection": "nations",
        "session_number": session_number,
        "request_reason": {"$regex": "^Tick Update"},
    })
    deltas = {}
    for c in changes:
        bd = c.get("before_implemented_data", {})
        ad = c.get("after_implemented_data", {})
        if "rolling_karma" in bd and "rolling_karma" in ad:
            deltas[c["target"]] = ad["rolling_karma"] - bd["rolling_karma"]
    return deltas


def main(apply: bool):
    schema = category_data["nations"]["schema"]
    wrong_deltas = find_wrong_rolling_karma_deltas(SESSION_NUMBER)
    print(f"Found {len(wrong_deltas)} nations with a rolling_karma change to revert "
          f"from session {SESSION_NUMBER}'s tick.")

    nations = list(mongo.db.nations.find({}).sort("name", 1))
    print(f"Processing {len(nations)} nations total.\n")

    plan = []
    for nation in nations:
        nid = nation["_id"]
        wrong_delta = wrong_deltas.get(nid, 0)

        current_karma = nation.get("karma", 0)
        current_rolling_karma = nation.get("rolling_karma", 0)

        corrected_karma = current_karma - wrong_delta
        reverted_rolling_karma = current_rolling_karma - wrong_delta

        raw_roll = random.randint(1, 20)
        event_roll = raw_roll + corrected_karma
        event_type = classify_event(raw_roll, event_roll)

        correct_delta = correct_rolling_karma_delta(nation, schema, event_type)
        final_rolling_karma = reverted_rolling_karma + correct_delta

        plan.append({
            "nation": nation,
            "wrong_delta_reverted": wrong_delta,
            "corrected_karma_used_for_roll": corrected_karma,
            "raw_roll": raw_roll,
            "event_roll": event_roll,
            "event_type": event_type,
            "reverted_rolling_karma": reverted_rolling_karma,
            "correct_delta_applied": correct_delta,
            "final_rolling_karma": final_rolling_karma,
        })

    # --- Summary ---
    reverted_count = sum(1 for p in plan if p["wrong_delta_reverted"] != 0)
    print(f"Nations with a wrong rolling_karma bump reverted: {reverted_count}")
    from collections import Counter
    print("New event_type distribution:", Counter(p["event_type"] for p in plan))
    print()
    print("Sample of nations whose rolling_karma bump is being reverted (first 10):")
    shown = 0
    for p in plan:
        if p["wrong_delta_reverted"] != 0:
            n = p["nation"]
            print(
                f"  {n.get('name', '?'):40s} karma {n.get('karma', 0):>4d} -> "
                f"{p['corrected_karma_used_for_roll']:>4d} (used for roll) | "
                f"rolling_karma {n.get('rolling_karma', 0):>3d} -> revert {p['reverted_rolling_karma']:>3d} "
                f"-> final {p['final_rolling_karma']:>3d} | new event: {p['event_type']}"
            )
            shown += 1
            if shown >= 10:
                break

    if not apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to commit.")
        return

    print("\nAPPLYING — writing changes now...")
    written = 0
    for p in plan:
        nation = p["nation"]
        new_nation = deepcopy(nation)
        new_nation.update({
            "temporary_karma": 0,
            "event_roll": p["event_roll"],
            "raw_roll": p["raw_roll"],
            "event_type": p["event_type"],
            "previous_karma": p["corrected_karma_used_for_roll"],
            "previous_temporary_karma": nation.get("temporary_karma", 0),
            "previous_rolling_karma": p["reverted_rolling_karma"],
            "rolling_karma": p["final_rolling_karma"],
        })
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason=(
                f"Karma Roll for {nation.get('name', '?')} "
                f"(correction: session {SESSION_NUMBER}'s tick ran before events were "
                f"rolled, so this re-rolls using pre-tick karma and corrects the "
                f"rolling_karma adjustment that was mistakenly applied based on a "
                f"stale event type)"
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
