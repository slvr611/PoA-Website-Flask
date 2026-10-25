"""One-off backfill for the new artifact succession system
(helpers/tick_helpers.py's _run_artifact_succession, added 2026-10-25):
that system only reassigns an artifact going forward, from the moment its
new former_ruling_nation_orgs breadcrumb mechanism starts recording who
rules what. It does nothing for artifacts that were ALREADY orphaned
before this shipped -- their owning character died before any breadcrumb
was ever recorded, so the tick has nothing to match against.

This script finds exactly those artifacts and reassigns them to whoever
currently rules the org the dead owner used to rule, mirroring the tick's
own rule precisely (see _run_artifact_succession):

  1. The artifact's owner is a dead character (the only case the live
     ruling-character/player model can never self-heal from -- a LIVING
     owner with no player attached is a different, unrelated issue this
     script does not touch).
  2. The org that dead character used to rule is recovered from the
     "changes" collection's retained history for that character (the last
     change record where ruling_nation_org was still non-null before being
     wiped to None at death). Older deaths may have aged out of the
     changes collection's retention window (see archive_old_changes) --
     those are reported as UNRESOLVED, never guessed at.
  3. That org's current living ruling character becomes the artifact's
     new owner, exactly like the tick would assign it.

Applying also stamps former_ruling_nation_orgs on the new owner (if not
already present) so the ongoing tick mechanism recognizes this succession
chain from here on, consistent with what the tick itself would have
written.

SAFE BY DEFAULT: dry run only, no writes, unless run with --apply.

Run from the project root:
  python -m scripts.port_orphaned_artifacts_to_current_rulers            # preview only
  python -m scripts.port_orphaned_artifacts_to_current_rulers --apply    # writes for real
"""
import argparse
import sys
from bson import ObjectId

from app_core import mongo
from helpers.change_helpers import system_request_change, system_approve_change

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_RULING_ORG_COLLECTIONS = ("nations", "merchants", "mercenaries")

# Owners whose artifacts are deliberately left where they are rather than
# ported, even though a resolvable successor exists -- a per-nation/RP
# judgment call, not a data problem. Add a name here and re-run; nothing
# else about the resolution logic changes.
_EXCLUDED_OWNER_NAMES = {
    "Falxscura Andorator",  # left in place at the user's request (2026-10-25)
    "Nekreyn",  # left in place at the user's request (2026-10-25)
}

# Owner name -> (collection, org_name) for the "too old" cases the user
# resolved by hand (2026-10-25) -- their death predates the changes
# collection's retention window, so _resolve_former_org has nothing to go
# on, but the user knows (from RP context) which org they used to rule.
# Takes priority over the change-history lookup for these exact names.
_MANUAL_OVERRIDES = {
    "Erdan": ("factions", "Väinämöinen Cult"),
    "Diomedes Vasilis IX": ("nations", "Tychi"),
    "Markus Valtarion the 3nd": ("nations", "Sundralund"),
    "Markus Valtarion the 15th": ("nations", "Sundralund"),
    'Tifala II the "the Favored Daughter of a Flow"': ("nations", "Shinra"),
    "Piff Wang": ("nations", "Dyeak"),
    "Conqueror of Remnants of Athabasca": ("nations", "Remnants of Athabasca"),
}


def _find_org(org_id_str):
    """(collection_name, org_doc) for an org id across every collection a
    character can rule, or (None, None) if it no longer exists anywhere."""
    try:
        oid = ObjectId(org_id_str)
    except Exception:
        return None, None
    for collection in _RULING_ORG_COLLECTIONS:
        org = mongo.db[collection].find_one({"_id": oid})
        if org:
            return collection, org
    return None, None


def _find_org_by_name(collection, name):
    return mongo.db[collection].find_one({"name": name})


def _resolve_former_org(dead_character_id):
    """The org dead_character_id used to rule, recovered from the last
    retained change record where ruling_nation_org was still set, or None
    if that history has aged out of the changes collection."""
    changes = mongo.db.changes.find(
        {"target_collection": "characters", "target": dead_character_id}
    ).sort("time_implemented", -1)
    for change in changes:
        before = change.get("before_implemented_data") or {}
        org_id = before.get("ruling_nation_org")
        if org_id:
            return str(org_id)
    return None


def _current_ruler(org_id_str):
    return mongo.db.characters.find_one(
        {"ruling_nation_org": org_id_str, "health_status": {"$ne": "Dead"}},
        sort=[("name", 1)],
    )


def find_candidates():
    """Returns (transferable, unresolved, excluded) -- each a list of
    dicts describing one artifact. transferable entries have everything
    needed to actually move the artifact; unresolved ones are reported but
    never acted on; excluded ones are resolvable but deliberately skipped
    (see _EXCLUDED_OWNER_NAMES)."""
    transferable = []
    unresolved = []
    excluded = []

    for artifact in mongo.db.artifacts.find({
        "owner": {"$nin": [None, "", "Unknown", "Lost"]},
        "archived": {"$ne": True},
    }):
        owner_raw = artifact.get("owner")
        try:
            owner_oid = ObjectId(str(owner_raw))
        except Exception:
            continue  # flavor-text owner ("Lost in Verdant Plains", etc.) -- not a character, nothing to fix

        owner_char = mongo.db.characters.find_one({"_id": owner_oid})
        if not owner_char:
            unresolved.append({
                "artifact": artifact, "owner_char": None,
                "reason": f"owner character {owner_raw} no longer exists",
            })
            continue
        if owner_char.get("health_status") != "Dead":
            continue  # alive and well (or alive-but-playerless, a different issue this script doesn't touch)

        if owner_char.get("name") in _EXCLUDED_OWNER_NAMES:
            excluded.append({"artifact": artifact, "owner_char": owner_char})
            continue

        override = _MANUAL_OVERRIDES.get(owner_char.get("name"))
        if override:
            org_collection, org_name = override
            org_doc = _find_org_by_name(org_collection, org_name)
            if not org_doc:
                unresolved.append({
                    "artifact": artifact, "owner_char": owner_char,
                    "reason": f"manual override target {org_name!r} ({org_collection}) not found",
                })
                continue
            former_org_id = str(org_doc["_id"])
        else:
            former_org_id = _resolve_former_org(owner_char["_id"])
            if not former_org_id:
                unresolved.append({
                    "artifact": artifact, "owner_char": owner_char,
                    "reason": "no retained change history shows which org they ruled (too old / never recorded)",
                })
                continue
            org_collection, org_doc = _find_org(former_org_id)
            if not org_doc:
                unresolved.append({
                    "artifact": artifact, "owner_char": owner_char,
                    "reason": f"former org {former_org_id} no longer exists",
                })
                continue

        new_owner = _current_ruler(former_org_id)
        if not new_owner:
            unresolved.append({
                "artifact": artifact, "owner_char": owner_char,
                "reason": f"{org_doc.get('name', '?')} ({org_collection}) has no living ruler to inherit it",
            })
            continue

        transferable.append({
            "artifact": artifact, "owner_char": owner_char,
            "org_collection": org_collection, "org_doc": org_doc, "new_owner": new_owner,
        })

    return transferable, unresolved, excluded


def main(apply: bool):
    transferable, unresolved, excluded = find_candidates()

    if excluded:
        print(f"Artifacts deliberately left in place (excluded owner): {len(excluded)}")
        for entry in excluded:
            print(f"  {entry['artifact'].get('name', '?')!r}: owner {entry['owner_char'].get('name', '?')}")
        print()

    print(f"Artifacts that CAN be ported to their org's current ruler: {len(transferable)}")
    for entry in transferable:
        artifact, owner, org, new_owner = (
            entry["artifact"], entry["owner_char"], entry["org_doc"], entry["new_owner"],
        )
        print(
            f"  {artifact.get('name', '?')!r}: {owner.get('name', '?')} (dead) -> "
            f"{new_owner.get('name', '?')}, current ruler of {org.get('name', '?')} ({entry['org_collection']})"
        )

    print(f"\nArtifacts that CANNOT be auto-resolved (need manual admin review): {len(unresolved)}")
    for entry in unresolved:
        artifact, owner = entry["artifact"], entry["owner_char"]
        owner_desc = owner.get("name", "?") if owner else "(no owner character found)"
        print(f"  {artifact.get('name', '?')!r}: owner {owner_desc} -- {entry['reason']}")

    if not apply:
        print("\nDRY RUN -- no changes written. Re-run with --apply to commit.")
        return

    print("\nAPPLYING -- writing changes now...")
    written = 0
    for entry in transferable:
        artifact, new_owner = entry["artifact"], entry["new_owner"]
        new_owner_id_str = str(new_owner["_id"])

        change_id = system_request_change(
            data_type="artifacts",
            item_id=artifact["_id"],
            change_type="Update",
            before_data=artifact,
            after_data={**artifact, "owner": new_owner_id_str},
            reason=(
                f"Artifact succession backfill: passed from "
                f"{entry['owner_char'].get('name', '?')} to {new_owner.get('name', '?')}"
            ),
        )
        ok = change_id is not None and system_approve_change(change_id)
        if not ok:
            print(f"  FAILED to transfer {artifact.get('name', '?')!r}")
            continue
        written += 1

        former_orgs = new_owner.get("former_ruling_nation_orgs") or []
        org_id_str = str(entry["org_doc"]["_id"])
        if org_id_str not in former_orgs:
            char_change_id = system_request_change(
                data_type="characters",
                item_id=new_owner["_id"],
                change_type="Update",
                before_data=new_owner,
                after_data={**new_owner, "former_ruling_nation_orgs": former_orgs + [org_id_str]},
                reason=f"Recorded {new_owner.get('name', '?')} as a ruler of {entry['org_doc'].get('name', '?')}",
            )
            if char_change_id is not None:
                system_approve_change(char_change_id)

    print(f"\nDone. {written}/{len(transferable)} artifacts transferred.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = parser.parse_args()
    main(apply=args.apply)
