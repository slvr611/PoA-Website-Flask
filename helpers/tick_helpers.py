import uuid
import math
import functools
from bson import ObjectId
from helpers.data_helpers import get_data_on_category
import gc
from helpers.ai_decision_helpers import (
    ai_decision_tick, ai_market_matching_tick, market_price_tick,
    run_ai_market_matching_standalone,
    _load_district_defs_cache, _clear_district_defs_cache,
)
from helpers.mech_rp_helpers import ai_mech_rp_tick
from helpers.trade_route_helpers import run_trade_route_lifecycle, _current_session as _tr_current_session
from calculations.field_calculations import calculate_all_fields, collect_laws, sum_law_totals
from pymongo import ASCENDING
from helpers.change_helpers import system_request_change, system_approve_change
from helpers.archive_helpers import archive_old_changes
from app_core import mongo, json_data, upload_to_s3, character_stats
from flask import flash
from app_core import backup_mongodb, category_data, temperament_enum, base_temperament_odds, cultural_trait_temperament_modifiers
from copy import deepcopy
import random
import os
import datetime

def _queue_change(pending, data_type, item_id, change_type, before_data, after_data, reason, already_calculated=False):
    """Defer a change instead of committing it immediately.

    Every tick function that used to call system_request_change +
    system_approve_change directly on a document OTHER than the one it's
    iterating (e.g. a character's death touching their nation, an artifact
    loss) now calls this instead, so the write only actually happens once the
    whole tick's compute phase has finished without error, as part of the
    commit phase at the end of tick()/era_tick() (see _commit_pending_changes).

    `already_calculated`: pass True when `after_data` already went through
    calculate_all_fields during this same tick's compute phase (true for
    every tick's main per-category loop, and for a few cross-cutting cases
    that recalculate their source object before mutating it — see call
    sites). Lets the commit phase skip system_approve_change's own
    recalculation, which is by far the dominant cost there (a nation: ~8s;
    a character: ~0.4s — measured directly against production data).
    Defaults to False (always recalculate at commit time — the original,
    always-safe behavior) for anything not verified to already be fresh.

    `pending` is the commit-phase accumulator list, threaded through only to
    the specific tick functions that need it (see _PENDING_AWARE_TICK_FUNCTIONS
    near the bottom of this file) — every other tick function's signature is
    untouched. If `pending` is None (e.g. a test or other caller invoking a
    tick function directly, outside of tick()/era_tick()), falls back to the
    original immediate commit so those callers keep working unchanged."""
    if pending is None:
        change_id = system_request_change(
            data_type=data_type, item_id=item_id, change_type=change_type,
            before_data=before_data, after_data=after_data, reason=reason,
        )
        if change_id is not None:
            system_approve_change(change_id, skip_recalculation=already_calculated)
        return change_id
    pending.append({
        "data_type": data_type,
        "item_id": item_id,
        "change_type": change_type,
        "before_data": before_data,
        "after_data": after_data,
        "reason": reason,
        "already_calculated": already_calculated,
    })
    return None


def _queue_tile_write(pending_tiles, tile_id, set_fields):
    """Defer a hex_map_tiles $set instead of writing immediately — the tile
    equivalent of _queue_change, for district/city claims made mid-tick.

    hex_map_tiles doesn't go through system_request_change/_queue_change at
    all: it carries no calculated fields and is deliberately kept outside the
    schema/change-tracking/approval system for performance (see
    routes/hex_map_routes.py's update_hex_map_tile, which writes directly).
    So this is a separate, much lighter queue — committed at the same point
    _commit_pending_changes runs, via _commit_pending_tile_writes — rather
    than folded into _queue_change's data-type grouping.

    Before this existed, _claim_district_tile/_claim_city_tile wrote to
    hex_map_tiles immediately, during the tick's compute phase — the one
    write in the whole tick that didn't respect "nothing touches the
    database until the single commit phase at the end" (see tick()'s
    matching comment). A tick that failed after claiming some tiles but
    before reaching the commit phase left those claims permanently on the
    map with no corresponding nation.districts/cities entry — see
    migrations/fix_orphaned_district_tile_claims.py, written to clean up
    exactly that shape of damage from before dry_run existed on the preview
    path; this closes the same class of gap for the live tick path too.

    If pending_tiles is None (a caller invoking a tile-claiming function
    directly, outside tick()), falls back to an immediate write so those
    callers keep working unchanged."""
    if pending_tiles is None:
        mongo.db.hex_map_tiles.update_one({"_id": tile_id}, {"$set": set_fields})
        return
    pending_tiles.append({"_id": tile_id, "set": dict(set_fields)})


def _commit_pending_tile_writes(pending_tiles):
    """Apply every hex_map_tiles write queued via _queue_tile_write, merged
    per tile (later-queued fields win on conflict, same precedent as
    _merge_after_data) — the tile-claim half of the tick's single
    end-of-compute-phase commit (_commit_pending_changes is the
    nations/characters/etc. half). No transaction/session needed: hex tiles
    carry no calculated fields or change-approval workflow, each tile is
    written independently, and this only ever runs after the whole compute
    phase has already finished without error. Plain per-tile update_one
    calls rather than bulk_write — nothing else in this codebase uses
    bulk_write, and tile counts per tick (a handful of district/city claims)
    don't need it."""
    if not pending_tiles:
        return
    merged = {}
    order = []
    for item in pending_tiles:
        tid = item["_id"]
        if tid not in merged:
            merged[tid] = {}
            order.append(tid)
        merged[tid].update(item["set"])
    for tid in order:
        mongo.db.hex_map_tiles.update_one({"_id": tid}, {"$set": merged[tid]})


def _commit_one_batch(session, items, skip_propagate_ids=None):
    """Commit a list of already-queued items inside an already-open
    transaction on `session`. Shared by every group _commit_pending_changes
    commits — see its docstring for the overall strategy.

    `skip_propagate_ids`: forwarded to system_approve_change/propagate_updates
    — the full set of (data_type, item_id) pairs queued anywhere in this
    tick's `pending`, not just this batch, so a dependency cascade never
    redundantly recalculates something that's getting its own fresh commit
    later in the same tick regardless of which batch it's in."""
    for i, item in enumerate(items):
        change_id = system_request_change(
            item["data_type"], item["item_id"], item["change_type"],
            item["before_data"], item["after_data"], item["reason"],
            session=session,
        )
        if change_id is None or not system_approve_change(
            change_id, session=session, skip_recalculation=item.get("already_calculated", False),
            skip_propagate_ids=skip_propagate_ids,
        ):
            raise RuntimeError(
                f"Tick commit aborted: a queued {item['change_type']} on "
                f"{item['data_type']} ({item['reason']}) could not be applied "
                f"— rolling back this batch so nothing in it is left partially applied."
            )
        # propagate_updates' recursive recalculation cascade can accumulate a
        # lot of temporary objects across a long batch — periodic GC here
        # mirrors what the old per-category commit loops already did.
        if i % 20 == 19:
            gc.collect()


def _looks_id_keyed(lst):
    """True if every item in `lst` is a dict with a stable `_id` — or `lst`
    is empty. Deliberately more permissive than change_helpers._all_have_ids
    (which treats an empty list as disqualifying) — here an empty list just
    means "this side removed everything," which must still be eligible for
    the ID-based 3-way merge below, not fall through to whole-list replace."""
    return all(isinstance(item, dict) and item.get("_id") is not None for item in lst)


def _merge_id_keyed_lists(base_list, a_list, b_list):
    """3-way merge of an ID-keyed list (modifiers, cities, ...) that two
    independent same-tick changes to the same entity both touched.

    `base_list` is the shared pre-tick ancestor both sides started from.
    An item missing from a side relative to base means that side removed
    it; an item present in a side but not in base means that side added
    it. Both sides' additions/removals/edits are combined rather than one
    side's whole list replacing the other's (which is what plain
    deep_merge does when both sides carry stable IDs — appropriate when
    merging a requested change onto the live document, but wrong here,
    where `a` and `b` are two independent deltas off the same base rather
    than one being authoritative)."""
    base_map = {i["_id"]: i for i in base_list if isinstance(i, dict) and "_id" in i}
    a_map = {i["_id"]: i for i in a_list if isinstance(i, dict) and "_id" in i}
    b_map = {i["_id"]: i for i in b_list if isinstance(i, dict) and "_id" in i}

    result_map = {}
    for item_id in set(base_map) | set(a_map) | set(b_map):
        in_base, in_a, in_b = item_id in base_map, item_id in a_map, item_id in b_map
        if not in_base:
            # Newly added by one or both sides — keep whichever added it;
            # if both added the same id (unlikely), the later side (b) wins.
            if in_b:
                result_map[item_id] = b_map[item_id]
            elif in_a:
                result_map[item_id] = a_map[item_id]
            continue
        a_edited = in_a and a_map[item_id] != base_map[item_id]
        b_edited = in_b and b_map[item_id] != base_map[item_id]
        if not in_a and not in_b:
            continue  # both removed it
        if not in_a and in_b:
            # a removed it; respect that unless b actually edited it (conflict — b wins)
            if b_edited:
                result_map[item_id] = b_map[item_id]
            continue
        if not in_b and in_a:
            if a_edited:
                result_map[item_id] = a_map[item_id]
            continue
        # present on both sides — later side's edit wins if it changed anything
        result_map[item_id] = b_map[item_id] if b_edited else a_map[item_id]

    # Preserve a stable order: base order first, then new items from a, then from b.
    ordered = []
    seen = set()
    for src in (base_list, a_list, b_list):
        for item in src:
            item_id = item.get("_id") if isinstance(item, dict) else None
            if item_id in result_map and item_id not in seen:
                ordered.append(result_map[item_id])
                seen.add(item_id)
    return ordered


def _merge_after_data(base, a, b):
    """3-way merge of two independent full-document mutations (`a`, `b`) of
    the same pre-tick `base` document, produced by two of a tick's own
    queued changes to the same entity (see _merge_pending_by_entity).

    For each key: if only one side changed it from base, use that side's
    value untouched. If both sides changed it to the same value, keep it.
    If both changed it to *different* values: ID-keyed lists (including
    ones where one side emptied it out entirely — see _looks_id_keyed) are
    merged item-by-item via _merge_id_keyed_lists; anything else (scalars,
    plain dicts) falls back to the later-queued side winning, same as
    deep_merge's existing behavior for non-list fields."""
    result = dict(a)
    for key, b_val in b.items():
        a_val = a.get(key)
        if b_val == a_val:
            continue
        base_val = base.get(key)
        b_changed = b_val != base_val
        a_changed = a_val != base_val
        if not b_changed:
            continue
        if not a_changed:
            result[key] = b_val
            continue
        if (isinstance(base_val, list) and isinstance(a_val, list) and isinstance(b_val, list)
                and _looks_id_keyed(base_val) and _looks_id_keyed(a_val) and _looks_id_keyed(b_val)
                and (base_val or a_val or b_val)):  # all-empty is not "ID-keyed", just empty
            result[key] = _merge_id_keyed_lists(base_val, a_val, b_val)
        else:
            result[key] = b_val
    return result


def _merge_pending_by_entity(items):
    """Collapse multiple queued changes to the same entity (same item_id)
    within one data-type group into a single change, in queue order, so at
    most one change — and one change-history record — is committed per
    entity per tick.

    Two of a tick's own queued changes can legitimately target the same
    entity — this isn't rare: it happens whenever a cross-cutting function
    (e.g. a character death queuing a nation-side stability/modifier
    update, or era_tick's ERA_GENERAL_TICK_FUNCTIONS running character
    aging *after* the main character loop already queued that same
    character) reaches into a data type that also gets its own per-entity
    "Tick Update for X" elsewhere in the same tick. Committing both as
    separate sequential changes either gets the second one blocked by
    check_no_other_changes (a real production failure — see
    change_helpers.py's skip_recalculation bypass, kept as a
    defense-in-depth fallback for anything that slips past this) or
    silently drops one side's list-field addition when both independently
    touched the same ID-keyed list (deep_merge replaces such lists
    wholesale rather than merging them). Merging here at queue-commit time
    avoids both: there's only ever one change to commit, so there's
    nothing for it to diverge from, and list fields are combined via
    _merge_after_data instead of one side clobbering the other."""
    merged_by_id = {}
    order = []
    for item in items:
        key = str(item["item_id"])
        if key not in merged_by_id:
            merged_by_id[key] = dict(item)
            order.append(key)
        else:
            existing = merged_by_id[key]
            existing["after_data"] = _merge_after_data(
                existing["before_data"], existing["after_data"], item["after_data"]
            )
            existing["reason"] = existing["reason"] + "; " + item["reason"]
            existing["already_calculated"] = existing["already_calculated"] and item["already_calculated"]
    return [merged_by_id[k] for k in order]


# Even with already_calculated=True skipping recalculation, each item still
# costs several sequential network round trips to Atlas (change insert,
# change approve, target update, dependency-lookup queries) — measured at
# roughly 0.5-0.7s/item against production. A single category can have 200+
# items (nations) or 400+ (characters), which alone exceeds MongoDB's 60s
# transaction limit regardless of recalculation cost. Chunking bounds the
# worst case; see _commit_pending_changes's docstring for the trade-off.
_COMMIT_CHUNK_SIZE = 30


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _commit_pending_changes(pending):
    """Apply every change queued via _queue_change, grouped into one
    transaction per CHUNK of up to _COMMIT_CHUNK_SIZE items of the same
    data type (nations, characters, artifacts, ...) rather than a single
    transaction for the whole tick, or even for a whole data type.

    A single all-or-nothing transaction for an entire full tick was tried
    first and doesn't hold up at real data volume: system_approve_change's
    recalculation step alone measured ~8s per nation, and a full tick can
    queue 200+ nations plus 400+ characters — many times over MongoDB's
    default 60-second transaction limit, which aborted the transaction
    outright (NoSuchTransaction/TransientTransactionError) partway through
    real runs. `already_calculated` (see _queue_change) removes most of that
    per-item cost for the categories that matter most — but even with it,
    each item still costs several sequential DB round trips (~0.5-0.7s/item
    measured live), so one transaction per whole category (e.g. all 200+
    nations at once) *still* blew the 60s limit in practice. Chunking each
    category into fixed-size groups, each its own transaction, is what
    actually keeps every transaction comfortably under the limit regardless
    of how large a category grows.

    Trade-off this accepts: atomicity is now per chunk, not per data type or
    per tick. If one nations chunk commits successfully and a later nations
    chunk (or a different data type) then fails, the earlier chunk's changes
    stay committed — they are NOT rolled back. Each chunk is still fully
    all-or-nothing internally, and a failure still stops every later chunk
    from starting, so nothing is silently skipped — but a tick that fails
    partway through no longer guarantees a clean pre-tick state, only that
    no chunk was left half-applied. Each queued item is still committed
    individually — its own system_request_change + system_approve_change
    call — so every entity still gets its own normal "Tick Update for X"
    change-history record.

    Multiple changes queued for the same entity within one tick — a real,
    not-rare occurrence (a character death queuing a cross-cutting nation
    update alongside the nations loop's own "Tick Update for X"; era_tick's
    ERA_GENERAL_TICK_FUNCTIONS queuing character-aging updates *after* the
    main character loop already queued that same character; etc — see
    _merge_pending_by_entity's docstring) — are merged into a single change
    per data-type group before chunking, via _merge_pending_by_entity. This
    is the single channel every tick-driven write goes through: at most one
    change, and one change-history record, per entity per tick, regardless
    of how many functions contributed to it. system_approve_change's
    skip_recalculation bypass of check_no_other_changes (change_helpers.py)
    is kept as a defense-in-depth fallback for anything that isn't a
    same-data-type-group duplicate and so slips past this merge.

    Each chunk uses session.with_transaction(), pymongo's own recommended
    pattern for transactions — it automatically retries the whole chunk on
    a TransientTransactionError (e.g. a brief replica-set election), which
    a plain start_transaction()/commit_transaction() pair does not.

    Returns the current session_counter (read fresh, after every chunk
    commits) for callers that need it afterward, e.g. archive_old_changes
    and snapshot_current_map — both must see the POST-commit value, since a
    queued "Tick Session Number" change may have just incremented it."""
    if pending:
        by_type = {}
        order = []
        for item in pending:
            dt = item["data_type"]
            if dt not in by_type:
                by_type[dt] = []
                order.append(dt)
            by_type[dt].append(item)

        # Every item queued anywhere in this tick, not just the chunk
        # currently committing — see _commit_one_batch's docstring for why.
        skip_propagate_ids = {(item["data_type"], str(item["item_id"])) for item in pending}

        for dt in order:
            merged_items = _merge_pending_by_entity(by_type[dt])
            for chunk in _chunked(merged_items, _COMMIT_CHUNK_SIZE):
                with mongo.cx.start_session() as session:
                    session.with_transaction(
                        lambda s, _items=chunk: _commit_one_batch(s, _items, skip_propagate_ids=skip_propagate_ids)
                    )

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    return global_modifiers.get("session_counter", 0) if global_modifiers else 0


# ---------------------------------------------------------------------------
# Player tick summary attribution helpers
# ---------------------------------------------------------------------------

# Tick functions whose results are never useful in the player-facing digest
# even for a player nation — too routine/frequent to be worth surfacing
# there (still recorded in the full tick summary).
_EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS = {
    "Nation Stability Tick",
}


def _org_leader_has_real_player(org_id_str, character_db):
    """True if any character whose ruling_nation_org is this org (a
    merchant/mercenary/faction _id) has a real player attached.

    "leaders" on merchants/mercenaries/factions is a queryTargetAttribute
    (reverse-lookup) field — it is NEVER actually stored on the org's own
    document (raw docs always carry leaders: [] or None), only populated at
    read time via get_linked_objects. Reading org.get("leaders", []) here
    always returned nothing, so no merchant/mercenary/faction tick result
    ever reached the player summary — the same class of bug fixed for
    ownership checks in helpers/visibility_helpers.py's is_item_owner.
    """
    if not org_id_str:
        return False
    try:
        leader = character_db.find_one(
            {"ruling_nation_org": org_id_str, "player": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 1},
        )
    except Exception:
        return False
    return leader is not None


def _character_tick_is_player_relevant(character, nation_db):
    """Whether a character's OWN tick events (aging, stat gains, heal/death
    rolls, artifact loss, ...) belong in the player-facing digest.

    Requires a real player (character.get("player") — note this can be
    None, "", or a real id string; only the last counts, so this must be a
    truthy check, not an `is not None` check, which the old code used and
    which incorrectly treated "" as "has a player").

    Beyond that: a character who currently rules a nation that is
    specifically an AI vassal of a player (the vassal itself isn't a
    player nation, but its overlord is) is excluded — that's just noise
    about the overlord's own vassal management, not something worth
    surfacing to the player audience. Ruling nothing, ruling a player
    nation, or ruling an AI nation that ISN'T a player's vassal are all
    still included.
    """
    if not character.get("player"):
        return False
    ruling_org_id = character.get("ruling_nation_org", "")
    if not ruling_org_id:
        return True
    try:
        ruled_nation = nation_db.find_one(
            {"_id": ObjectId(ruling_org_id)}, {"temperament": 1, "overlord": 1}
        )
    except Exception:
        return True
    if not ruled_nation:
        return True  # ruling_nation_org points to a merchant/mercenary, not a nation
    if ruled_nation.get("temperament") == "Player":
        return True
    overlord_id = ruled_nation.get("overlord")
    if not overlord_id:
        return True  # an AI nation with no overlord at all isn't "a player's vassal"
    try:
        overlord = nation_db.find_one({"_id": ObjectId(overlord_id)}, {"temperament": 1})
    except Exception:
        return True
    return not (overlord and overlord.get("temperament") == "Player")


def tick(form_data):
    if "run_Backup Database" in form_data:
        success, message = backup_database()
        if not success:
            return message
    player_tick_summary = ""
    full_tick_summary = ""

    # Every write this tick makes is queued here instead of being committed
    # immediately, and only actually applied — all at once, inside a single
    # MongoDB transaction — once the entire compute phase below has finished
    # with no exception. See _queue_change/_dispatch and the commit phase at
    # the bottom of this function.
    pending = []
    # Same idea for hex_map_tiles writes (district/city claims) — a separate
    # queue since tiles don't go through the schema/change-tracking system
    # _queue_change uses. See _queue_tile_write/_commit_pending_tile_writes.
    pending_tiles = []

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    old_target = global_modifiers
    new_target = deepcopy(global_modifiers)
    schema = category_data["global_modifiers"]["schema"]
    if global_modifiers:
        run_key = f"run_Tick Session Number"
        if run_key in form_data:
            print("Tick Session Number")
            full_tick_summary += tick_session_number(old_target, new_target, schema)



    collect_character_data = False
    for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_character_data = True
            break
    
    if collect_character_data:
        character_schema, character_db = get_data_on_category("characters")
        _, _char_nation_db = get_data_on_category("nations")
        old_characters = list(character_db.find().sort("name", ASCENDING))
        new_characters = []
        for character in old_characters:
            if character:
                character.update(calculate_all_fields(character, character_schema, "character"))
                new_characters.append(deepcopy(character))

        for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_characters)):
                    # Stasis blocks every tick function except modifier decay, so a
                    # stasis modifier's own duration still counts down and can expire.
                    if tick_function is not modifier_decay_tick and _in_stasis(old_characters[i]):
                        continue
                    result = _dispatch(tick_function, pending, old_characters[i], new_characters[i], character_schema)
                    if _character_tick_is_player_relevant(old_characters[i], _char_nation_db):
                        player_tick_summary += result
                    full_tick_summary += result



    collect_artifact_data = False
    for tick_function_label, tick_function in ARTIFACT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_artifact_data = True
            break
    
    if collect_artifact_data:
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        old_artifacts = list(artifact_db.find().sort("name", ASCENDING))
        new_artifacts = []
        for artifact in old_artifacts:
            if artifact:
                artifact.update(calculate_all_fields(artifact, artifact_schema, "artifact"))
                new_artifacts.append(deepcopy(artifact))
        
        for tick_function_label, tick_function in ARTIFACT_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_artifacts)):
                    result = _dispatch(tick_function, pending, old_artifacts[i], new_artifacts[i], artifact_schema)
                    owner_id = old_artifacts[i].get("owner", "")
                    if owner_id and owner_id != "None":
                        try:
                            owner = character_db.find_one({"_id": ObjectId(owner_id)})
                            if owner and owner.get("player"):
                                player_tick_summary += result
                        except Exception:
                            pass
                    full_tick_summary += result


    collect_merchant_data = False
    for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_merchant_data = True
            break
    
    if collect_merchant_data:
        merchant_schema, merchant_db = get_data_on_category("merchants")
        old_merchants = list(merchant_db.find().sort("name", ASCENDING))
        new_merchants = []
        for merchant in old_merchants:
            if merchant:
                merchant.update(calculate_all_fields(merchant, merchant_schema, "merchant"))
                new_merchants.append(deepcopy(merchant))
        
        for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_merchants)):
                    result = _dispatch(tick_function, pending, old_merchants[i], new_merchants[i], merchant_schema)
                    if _org_leader_has_real_player(str(old_merchants[i]["_id"]), character_db):
                        player_tick_summary += result
                    full_tick_summary += result



    collect_mercenary_data = False
    for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_mercenary_data = True
            break
    
    if collect_mercenary_data:
        mercenary_schema, mercenary_db = get_data_on_category("mercenaries")
        old_mercenaries = list(mercenary_db.find().sort("name", ASCENDING))
        new_mercenaries = []
        for mercenary in old_mercenaries:
            if mercenary:
                mercenary.update(calculate_all_fields(mercenary, mercenary_schema, "mercenary"))
                new_mercenaries.append(deepcopy(mercenary))
        
        for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_mercenaries)):
                    result = _dispatch(tick_function, pending, old_mercenaries[i], new_mercenaries[i], mercenary_schema)
                    if _org_leader_has_real_player(str(old_mercenaries[i]["_id"]), character_db):
                        player_tick_summary += result
                    full_tick_summary += result



    collect_faction_data = False
    for tick_function_label, tick_function in FACTION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_faction_data = True
            break
    
    if collect_faction_data:
        faction_schema, faction_db = get_data_on_category("factions")
        old_factions = list(faction_db.find().sort("name", ASCENDING))
        new_factions = []
        for faction in old_factions:
            if faction:
                faction.update(calculate_all_fields(faction, faction_schema, "faction"))
                new_factions.append(deepcopy(faction))

        for tick_function_label, tick_function in FACTION_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_factions)):
                    result = _dispatch(tick_function, pending, old_factions[i], new_factions[i], faction_schema)
                    if _org_leader_has_real_player(str(old_factions[i]["_id"]), character_db):
                        player_tick_summary += result
                    full_tick_summary += result



    collect_market_data = False
    for tick_function_label, tick_function in MARKET_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_market_data = True
            break
    
    if collect_market_data:
        market_schema, market_db = get_data_on_category("markets")
        old_markets = list(market_db.find().sort("name", ASCENDING))
        new_markets = []
        for market in old_markets:
            if market:
                market.update(calculate_all_fields(market, market_schema, "market"))
                new_markets.append(deepcopy(market))

        for tick_function_label, tick_function in MARKET_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_markets)):
                    full_tick_summary += _dispatch(tick_function, pending, old_markets[i], new_markets[i], market_schema, pending_tiles=pending_tiles)



    collect_nation_data = False
    for tick_function_label in list(NATION_TICK_FUNCTIONS) + list(NATION_CROSS_TICK_FUNCTIONS):
        if f"run_{tick_function_label}" in form_data:
            collect_nation_data = True
            break
    
    if collect_nation_data:
        nation_schema, nation_db = get_data_on_category("nations")
        old_nations = list(nation_db.find().sort("name", ASCENDING))
        new_nations = []
        for nation in old_nations:
            if nation:
                nation.update(calculate_all_fields(nation, nation_schema, "nation"))
                new_nations.append(deepcopy(nation))

        # Nations in stasis (or undead-horde nations, who can't generate or
        # lose resources of any kind) have their market demands wiped
        # completely for the duration of the tick (existing trade routes stay
        # but pause instead — handled separately in get_trade_route_resource_net).
        for i in range(len(old_nations)):
            if _in_stasis(old_nations[i]) or _undead_horde_tick_blocked(old_nations[i], ""):
                new_nations[i]["resource_desires"] = []

        # Accumulates {"from_id", "to_id", "to_temperament"} entries appended
        # by pop_flee_tick (see _FLEE_EVENT_AWARE_TICK_FUNCTIONS) — lets the
        # player_tick_summary check below see the DESTINATION nation's
        # temperament, not just the source nation this dispatch call was
        # made for, so a pop fleeing INTO a player nation from an AI source
        # is caught too, not just fleeing out of one.
        flee_events = []

        for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                # Pre-load district_defs into a module-level cache before the AI
                # Decision Tick so score_buildable_districts (called up to 6× per
                # nation × 180 nations) issues only ONE DB query instead of ~1,080.
                is_ai_tick = tick_function_label == "AI Decision Tick"
                if is_ai_tick:
                    _load_district_defs_cache()
                try:
                    for i in range(len(old_nations)):
                        # Stasis blocks every tick function except modifier decay, so
                        # a stasis modifier's own duration still counts down and expires.
                        if tick_function is not modifier_decay_tick and _in_stasis(old_nations[i]):
                            continue
                        # Undead-horde nations: same blanket freeze, but with their
                        # own small exemption list (AI goal selection, vassal
                        # compliance loss) instead of stasis's universal one.
                        if tick_function is not modifier_decay_tick and _undead_horde_tick_blocked(old_nations[i], tick_function_label):
                            continue
                        _flee_events_before = len(flee_events)
                        result = _dispatch(tick_function, pending, old_nations[i], new_nations[i], nation_schema, pending_tiles=pending_tiles, flee_events=flee_events)
                        fled_to_a_player_nation = any(
                            e.get("to_temperament") == "Player" for e in flee_events[_flee_events_before:]
                        )
                        if tick_function_label in _EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS:
                            pass
                        elif old_nations[i].get("temperament", "None") == "Player" or fled_to_a_player_nation:
                            player_tick_summary += result
                        elif tick_function_label in VASSAL_SPECIFIC_NATION_TICK_FUNCTIONS and old_nations[i].get("overlord", "None") != "None":
                            overlord = old_nations[i].get("overlord", "None")
                            try:
                                overlord = nation_db.find_one({"_id": ObjectId(overlord)})
                                if overlord.get("temperament", "None") == "Player":
                                    player_tick_summary += result
                            except:
                                pass
                        full_tick_summary += result
                finally:
                    # Clear the district_defs cache and run GC after each tick
                    # function to release temporary objects and the cached defs list.
                    if is_ai_tick:
                        _clear_district_defs_cache()
                    gc.collect()

        for tick_function_label, tick_function in NATION_CROSS_TICK_FUNCTIONS.items():
            if f"run_{tick_function_label}" in form_data:
                print(tick_function_label)
                result = _dispatch(tick_function, pending, old_nations, new_nations, nation_schema, pending_tiles=pending_tiles)
                full_tick_summary += result



    if "run_Tick Session Number" in form_data:
        _queue_change(
            pending,
            data_type="global_modifiers",
            item_id=old_target["_id"],
            change_type="Update",
            before_data=old_target,
            after_data=new_target,
            reason="Tick Update for Tick Session Number",
            already_calculated=True,
        )

    if collect_character_data:
        for i in range(len(old_characters)):
            _queue_change(
                pending,
                data_type="characters",
                item_id=old_characters[i]["_id"],
                change_type="Update",
                before_data=old_characters[i],
                after_data=new_characters[i],
                reason="Tick Update for " + old_characters[i]["name"],
                already_calculated=True,
            )

    if collect_artifact_data:
        for i in range(len(old_artifacts)):
            _queue_change(
                pending,
                data_type="artifacts",
                item_id=old_artifacts[i]["_id"],
                change_type="Update",
                before_data=old_artifacts[i],
                after_data=new_artifacts[i],
                reason="Tick Update for " + old_artifacts[i]["name"],
                already_calculated=True,
            )

    if collect_merchant_data:
        for i in range(len(old_merchants)):
            _queue_change(
                pending,
                data_type="merchants",
                item_id=old_merchants[i]["_id"],
                change_type="Update",
                before_data=old_merchants[i],
                after_data=new_merchants[i],
                reason="Tick Update for " + old_merchants[i]["name"],
                already_calculated=True,
            )

    if collect_mercenary_data:
        for i in range(len(old_mercenaries)):
            _queue_change(
                pending,
                data_type="mercenaries",
                item_id=old_mercenaries[i]["_id"],
                change_type="Update",
                before_data=old_mercenaries[i],
                after_data=new_mercenaries[i],
                reason="Tick Update for " + old_mercenaries[i]["name"],
                already_calculated=True,
            )

    if collect_faction_data:
        for i in range(len(old_factions)):
            _queue_change(
                pending,
                data_type="factions",
                item_id=old_factions[i]["_id"],
                change_type="Update",
                before_data=old_factions[i],
                after_data=new_factions[i],
                reason="Tick Update for " + old_factions[i]["name"],
                already_calculated=True,
            )

    if collect_market_data:
        for i in range(len(old_markets)):
            _queue_change(
                pending,
                data_type="markets",
                item_id=old_markets[i]["_id"],
                change_type="Update",
                before_data=old_markets[i],
                after_data=new_markets[i],
                reason="Tick Update for " + old_markets[i]["name"],
                already_calculated=True,
            )

    if collect_nation_data:
        for i in range(len(old_nations)):
            _queue_change(
                pending,
                data_type="nations",
                item_id=old_nations[i]["_id"],
                change_type="Update",
                before_data=old_nations[i],
                after_data=new_nations[i],
                reason="Tick Update for " + old_nations[i]["name"],
                already_calculated=True,
            )

    # ── Commit phase ─────────────────────────────────────────────────────
    # Everything above was pure computation — nothing has touched the
    # database yet except plain reads. Now that the whole tick has finished
    # without raising, apply every queued change as one all-or-nothing
    # transaction: if anything fails partway through (a conflicting edit, a
    # DB error, hitting Mongo's transaction time limit), pymongo aborts the
    # transaction automatically and NONE of this tick's changes are applied
    # — matching what used to happen only within a single system_approve_change
    # call, now extended to the entire tick. Each queued item is still
    # committed individually (its own system_request_change/system_approve_change
    # call), so every entity's change-history page shows the same one
    # "Tick Update for X" record it always has.
    current_session = _commit_pending_changes(pending)
    _commit_pending_tile_writes(pending_tiles)

    archive_message = archive_old_changes(current_session)
    full_tick_summary += f"\n\nArchival: {archive_message}"

    if "run_Snapshot Hex Map" in form_data:
        from helpers.hex_map_helpers import snapshot_current_map
        snap_message = snapshot_current_map(current_session)
        full_tick_summary += f"\n\n{snap_message}"

    if "run_Region Renaissance Check" in form_data:
        full_tick_summary += region_renaissance_tick()

    if "run_Give Tick Summary" in form_data:
        give_tick_summary(player_tick_summary, full_tick_summary)

    return full_tick_summary

def _run_tick_guarded(target, form_data, label):
    """Run tick()/era_tick() and make sure a failure is actually visible
    somewhere an admin will see it, instead of a bare thread crash whose
    only trace is whatever reached the process logs before it died (see the
    KeyError: 'overlord' incident that prompted this). Failure here always
    means the deferred-commit transaction was never opened or was aborted —
    per _commit_pending_changes, nothing from this run was applied."""
    try:
        target(form_data)
    except Exception as e:
        import traceback
        error_text = (
            f"{label} FAILED and was fully rolled back — no changes from this "
            f"run were applied.\n\nError: {e}\n\n{traceback.format_exc()}"
        )
        print(error_text)
        try:
            give_tick_summary(error_text, error_text)
        except Exception:
            pass


def run_tick_async(form_data):
    """Queue the tick process to run in the background"""
    from threading import Thread
    thread = Thread(target=_run_tick_guarded, args=(tick, form_data, "Tick"))
    thread.daemon = True
    thread.start()
    return "Tick process started in background. Check logs for results."


def run_ai_market_matching_async():
    """
    Start AI market matching in a background thread.

    Unlike the tick-based version, this is completely self-contained: it
    loads fresh nation data from the DB, runs all matching, and commits
    every changed nation directly via the change pipeline.  The HTTP
    response returns immediately; matching runs to completion in the
    background even if it takes several minutes.
    """
    from threading import Thread
    from app_core import app
    thread = Thread(target=run_ai_market_matching_standalone, args=(app,))
    thread.daemon = True
    thread.start()
    return "AI market matching started in background. Check server logs for results."

###########################################################
# General Tick Functions
###########################################################

def backup_database():
    """Runs the backup synchronously and waits for it to fully finish before
    returning. tick()/era_tick() both call this before touching any data, and
    both already run in their own background thread (run_tick_async/
    run_era_tick_async) rather than inline in an HTTP request — so blocking
    here doesn't risk a request timeout, and is exactly what's needed: the
    backup must capture a complete, consistent pre-tick snapshot rather than
    racing the tick's own writes to the same database (the previous
    fire-and-forget backup_mongodb_async spawned the backup in a SEPARATE
    thread and returned immediately, letting the tick start mutating data
    the backup might not have read yet)."""
    success, message = backup_mongodb()
    status = "success" if success else "failure"
    print(f"Backup completed with {status}: {message}")
    return success, message

def give_tick_summary(player_tick_summary, full_tick_summary):
    """Save tick summary to a file and optionally email it"""
    print(full_tick_summary)  # Keep console logging
    
    # Create a timestamp for the filename
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Create summaries directory if it doesn't exist
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    os.makedirs(summary_dir, exist_ok=True)
    
    # Save to file
    player_summary_filename = f"player_tick_summary_{timestamp}.txt"
    player_summary_path = os.path.join(summary_dir, player_summary_filename)
    
    with open(player_summary_path, 'w') as f:
        f.write(player_tick_summary)

    full_summary_filename = f"full_tick_summary_{timestamp}.txt"
    full_summary_path = os.path.join(summary_dir, full_summary_filename)
    
    with open(full_summary_path, 'w') as f:
        f.write(full_tick_summary)
    
    
    # If S3 is configured, upload the summary
    if os.getenv("S3_BUCKET_NAME"):
        upload_to_s3(player_summary_path, f"tick_summaries/{player_summary_filename}")
        upload_to_s3(full_summary_path, f"tick_summaries/{full_summary_filename}")
    
    return full_summary_path

def _in_stasis(entity):
    """True if `entity` (a nation or character dict) currently carries a stasis modifier.

    Checked against the pre-tick (old) copy so a modifier that decays to 0 duration
    THIS tick still blocks everything else from running during the same pass."""
    return any(m.get("modifier_type") == "stasis" for m in (entity or {}).get("modifiers", []))


# Tick functions that must keep running for undead-horde nations even though
# everything else about them is frozen (see _undead_horde_tick_blocked):
# AI Decision Tick so their strategic goal keeps resolving to war-prep every
# session, and Nation Vassal Compliance Tick so vassal compliance keeps
# dropping every session.
UNDEAD_HORDE_EXEMPT_TICKS = {"AI Decision Tick", "Nation Vassal Compliance Tick"}


def _undead_horde_tick_blocked(entity, tick_function_label):
    """True when `entity` is an undead-horde nation (see
    helpers.undead_horde_helpers.nation_is_undead_horde) and
    `tick_function_label` isn't one of the few ticks that must keep running
    for them. Mirrors _in_stasis's blanket-freeze pattern but with a small
    per-nation-type exemption list instead of stasis's universal one."""
    if tick_function_label in UNDEAD_HORDE_EXEMPT_TICKS:
        return False
    from helpers.undead_horde_helpers import nation_is_undead_horde
    return nation_is_undead_horde(entity)


def modifier_decay_tick(old_target, new_target, schema):
    new_modifiers = []
    for modifier in new_target.get("modifiers", []):
        new_modifier = deepcopy(modifier)
        if int(new_modifier.get("duration", -1)) > 0:
            new_modifier["duration"] = int(new_modifier["duration"]) - 1
        if int(new_modifier.get("duration", -1)) != 0:
            new_modifiers.append(new_modifier)
    new_target["modifiers"] = new_modifiers
    return ""

def progress_quests_tick(old_target, new_target, schema):
    for i in range(len(old_target.get("progress_quests", []))):
        new_target["progress_quests"][i]["current_progress"] = old_target["progress_quests"][i].get("current_progress", 0) + old_target["progress_quests"][i].get("total_progress_per_tick", 0)
        if new_target["progress_quests"][i].get("current_progress", 0) >= new_target["progress_quests"][i].get("required_progress", 0):
            new_target["progress_quests"][i]["current_progress"] = new_target["progress_quests"][i].get("required_progress", 0)
            
    return ""

def tick_session_number(old_target, new_target, schema):
    new_target["session_counter"] = old_target.get("session_counter", 0) + 1
    return ""

###########################################################
# Character Tick Functions
###########################################################

RULER_TYPE_STATS = {
    "Steward":          {"strength": "rulership", "weakness": "magic"},
    "Religious Leader": {"strength": "cunning",   "weakness": "strategy"},
    "Populist":         {"strength": "charisma",  "weakness": "prowess"},
    "Conqueror":        {"strength": "prowess",   "weakness": "charisma"},
    "Archmage":         {"strength": "magic",     "weakness": "rulership"},
    "General":          {"strength": "strategy",  "weakness": "cunning"},
}

RULER_SUBTYPES = {
    "Steward":          ["Quartermaster", "Administrator", "Noble"],
    "Religious Leader": ["Prophet", "Martyr", "Guardian"],
    "Populist":         ["Orator", "Diplomat", "Bard"],
    "Conqueror":        ["Duelist", "Champion", "Barbarian"],
    "Archmage":         ["Magus", "Warlock", "Scholar"],
    "General":          ["Tactician", "Tyrant", "Infiltrator"],
}

_RULER_TYPES = list(RULER_TYPE_STATS.keys())


def _pick_succession_titles(succession_type, previous_leader):
    """Return a list of title keys based on succession type (possibly empty).

    - Elected: one random tier-2 title and one random tier-1 title.
    - Strength: two random (distinct) tier-3 titles.
    - Inherited: the tier-1 version of every title the previous leader had.
    """
    positive_titles = json_data.get("positive_titles", {})
    positive_only = {k: v for k, v in positive_titles.items() if v.get("type") == "positive"}
    keys_ordered = list(positive_only.keys())

    # Group consecutive positive titles into lines of 3 (tier 1 -> 2 -> 3)
    title_lines = [keys_ordered[i:i + 3] for i in range(0, len(keys_ordered), 3)]
    title_to_line = {k: line for line in title_lines for k in line}

    tier1 = [k for k, v in positive_only.items() if v.get("tier") == 1]
    tier2 = [k for k, v in positive_only.items() if v.get("tier") == 2]
    tier3 = [k for k, v in positive_only.items() if v.get("tier") == 3]

    if succession_type == "Elected":
        titles = []
        if tier2:
            titles.append(random.choice(tier2))
        if tier1:
            titles.append(random.choice(tier1))
        return titles

    if succession_type == "Strength":
        return random.sample(tier3, min(2, len(tier3))) if tier3 else []

    # Inherited: tier-1 version of every title the previous leader had,
    # deduplicated by line (a leader can't hold two titles from the same line).
    if previous_leader:
        titles = []
        seen_lines = set()
        for t in previous_leader.get("positive_titles", []):
            line = title_to_line.get(t)
            if not line:
                continue
            line_key = line[0]
            if line_key in seen_lines:
                continue
            seen_lines.add(line_key)
            titles.append(line[0])  # tier-1 of that line
        if titles:
            return titles

    return [random.choice(tier1)] if tier1 else []


def generate_ai_character(org, org_schema, character_schema, previous_leader=None, pending=None):
    """Create and insert an AI ruler for the given nation/org. Returns a log string."""
    character_type = random.choice(_RULER_TYPES)
    character_subtype = random.choice(RULER_SUBTYPES[character_type])
    req_strength = RULER_TYPE_STATS[character_type]["strength"]
    req_weakness = RULER_TYPE_STATS[character_type]["weakness"]

    remaining = [s for s in character_stats if s not in (req_strength, req_weakness)]
    rand_strength = random.choice(remaining)
    remaining_for_weakness = [s for s in remaining if s != rand_strength]
    rand_weakness = random.choice(remaining_for_weakness)

    strengths = [req_strength, rand_strength]
    weaknesses = [req_weakness, rand_weakness]

    modifiers = []
    for s in strengths:
        modifiers.append({"field": s, "value": random.randint(2, 4), "duration": -1, "source": "Strength"})
    for w in weaknesses:
        modifiers.append({"field": w, "value": random.randint(-4, -2), "duration": -1, "source": "Weakness"})

    # Heir Training mech RPs (helpers/mech_rp_helpers.py) bank stat points on
    # the nation for whoever succeeds the current ruler. Apply them here as
    # permanent modifiers on the newly-generated heir, then clear the bank so
    # they aren't applied again to a future ruler.
    heir_training_bonuses = {k: v for k, v in (org.get("heir_training_bonuses") or {}).items() if v}
    for stat, amount in heir_training_bonuses.items():
        modifiers.append({"field": stat, "value": amount, "duration": -1, "source": "Heir Training"})

    org_name = org.get("name", "Unknown")
    succession_type = org.get("succession_type", "Inherited")

    # Determine ruler demographics and whether to update nation primaries
    ruler_race = str(org["primary_race"]) if org.get("primary_race") else None
    ruler_culture = str(org["primary_culture"]) if org.get("primary_culture") else None
    ruler_religion = str(org["primary_religion"]) if org.get("primary_religion") else None
    pop_selected = None

    if succession_type in ("Elected", "Strength"):
        nation_pops = list(mongo.db.pops.find({"nation": str(org["_id"])}))
        if nation_pops:
            pop_selected = random.choice(nation_pops)
            ruler_race = str(pop_selected["race"]) if pop_selected.get("race") else ruler_race
            ruler_culture = str(pop_selected["culture"]) if pop_selected.get("culture") else ruler_culture
            ruler_religion = str(pop_selected["religion"]) if pop_selected.get("religion") else ruler_religion

    titles = _pick_succession_titles(succession_type, previous_leader)

    char_props = character_schema.get("properties", {})
    positive_quirk_options = [q for q in char_props.get("positive_quirk", {}).get("enum", []) if q != "None"]
    negative_quirk_options = [q for q in char_props.get("negative_quirk", {}).get("enum", []) if q != "None"]
    positive_quirk = random.choice(positive_quirk_options) if positive_quirk_options else "None"
    negative_quirk = random.choice(negative_quirk_options) if negative_quirk_options else "None"

    magic_points = max(0, sum(m["value"] for m in modifiers if m.get("field") == "magic"))

    base_name = f"{character_type} of {org_name}"
    name = base_name
    counter = 2
    while mongo.db.characters.find_one({"name": name}):
        name = f"{base_name} {counter}"
        counter += 1

    char_doc = {
        "name": name,
        "character_type": character_type,
        "character_subtype": character_subtype,
        "health_status": "Healthy",
        "age_status": "Adult",
        "age": 1,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "modifiers": modifiers,
        "player": None,
        "creator": None,
        "ruling_nation_org": str(org["_id"]),
        "region": str(org["region"]) if org.get("region") else None,
        "race": ruler_race,
        "culture": ruler_culture,
        "religion": ruler_religion,
        "random_stats": 0,
        "positive_titles": titles,
        "negative_titles": [],
        "positive_quirk": positive_quirk,
        "negative_quirk": negative_quirk,
        "magic_points": magic_points,
    }

    # Generated up front (instead of letting Mongo auto-assign one at insert
    # time and reading it back afterward) so the artifact-reassignment step
    # below can reference the new character's id immediately — required now
    # that the Add itself may be deferred (see _queue_change) rather than
    # committed right here, so there's nothing to read back yet.
    new_char_id = ObjectId()
    char_doc["_id"] = new_char_id

    _queue_change(
        pending,
        data_type="characters",
        item_id=new_char_id,
        change_type="Add",
        before_data={},
        after_data=char_doc,
        reason=f"Auto-generated AI ruler for {org_name}",
    )
    result = f"Generated AI ruler '{name}' ({character_type} / {character_subtype}) for {org_name}.\n"

    if pop_selected or heir_training_bonuses:
        new_org = deepcopy(org)
        reasons = []
        if pop_selected:
            new_org["primary_race"] = ruler_race
            new_org["primary_culture"] = ruler_culture
            new_org["primary_religion"] = ruler_religion
            reasons.append(f"Succession ({succession_type}): primary demographics updated")
        if heir_training_bonuses:
            new_org["heir_training_bonuses"] = {}
            reasons.append("Heir Training bonuses applied, clearing the bank")
        _queue_change(
            pending,
            data_type="nations",
            item_id=org["_id"],
            change_type="Update",
            before_data=deepcopy(org),
            after_data=new_org,
            reason=f"{'; '.join(reasons)} for {org_name}",
        )
        if pop_selected:
            result += f"  -> Updated {org_name} primary demographics via {succession_type} succession.\n"
        if heir_training_bonuses:
            trained_summary = ", ".join(f"+{v} {k}" for k, v in heir_training_bonuses.items())
            result += f"  -> Applied banked Heir Training ({trained_summary}) to {name}.\n"

    if previous_leader:
        prev_id_str = str(previous_leader["_id"])
        predecessor_artifacts = list(mongo.db.artifacts.find({"owner": prev_id_str, "archived": {"$ne": True}}))
        for artifact in predecessor_artifacts:
            _queue_change(
                pending,
                data_type="artifacts",
                item_id=artifact["_id"],
                change_type="Update",
                before_data=artifact,
                after_data={"owner": str(new_char_id)},
                reason=f"Artifact inherited by {name} from predecessor",
            )
        if predecessor_artifacts:
            result += f"  -> Transferred {len(predecessor_artifacts)} artifact(s) from predecessor.\n"

    return result


def ai_ensure_leader_tick(old_nation, new_nation, schema, pending=None):
    """Nation tick: generate a fresh AI ruler immediately if the nation has no
    living leader when the tick starts.

    character_death_tick already generates a successor the moment a leader
    dies mid-session, so this only covers nations that end up leaderless
    outside that path (manual edits, imports, or any other gap) — checked
    fresh at the start of every session tick, not just at era boundaries.
    """
    if old_nation.get("players"):
        return ""
    nation_id = str(old_nation.get("_id", ""))
    has_living_leader = mongo.db.characters.find_one(
        {"ruling_nation_org": nation_id, "health_status": {"$ne": "Dead"}},
        {"_id": 1},
    )
    if has_living_leader:
        return ""
    character_schema, _ = get_data_on_category("characters")
    return generate_ai_character(old_nation, schema, character_schema, pending=pending)


def character_death_tick(old_character, new_character, schema):
    result = ""
    if new_character.get("health_status", "Healthy") == "Dead":
        return ""
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = old_character.get("death_chance", 0)
    if death_roll <= old_character.get("death_chance", 0):
        new_character["health_status"] = "Dead"
        new_character["ruling_nation_org"] = None
        new_character["region"] = None
        new_character["player"] = None
        result = f"{old_character.get('name', 'Unknown')} has died.\n"

        if old_character.get("ruling_nation_org", "") != "":
            nation_schema, nation_db = get_data_on_category("nations")
            try:
                old_nation = nation_db.find_one({"_id": ObjectId(old_character.get("ruling_nation_org", ""))})
            except:
                old_nation = None
            if old_nation:
                old_nation.update(calculate_all_fields(old_nation, nation_schema, "nation"))
                new_nation = deepcopy(old_nation)
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = old_nation.get("stability_loss_chance_on_leader_death", 0)

                amounts = []
                reasons = []

                if leader_death_stab_loss_roll <= old_nation.get("stability_loss_chance_on_leader_death", 0):
                    amounts.append(-1)
                    reasons.append("stability_loss_chance_on_leader_death")
                
                if old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) > 0:
                    stability_loss_chance = min(old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) * old_character.get("age", 1), old_nation.get("max_stability_loss_chance_on_leader_death_per_age", 0))
                    leader_death_age_stab_loss_roll = random.random()

                    new_nation["leader_death_age_stab_loss_roll"] = leader_death_age_stab_loss_roll
                    new_nation["leader_death_age_stab_loss_chance_at_tick"] = stability_loss_chance

                    amount = 0

                    while stability_loss_chance > 1:
                        amount += 1
                        stability_loss_chance -= 1
                    if leader_death_age_stab_loss_roll <= stability_loss_chance:
                        amount += 1
                    if amount > 0:
                        amounts.append(-amount)
                        reasons.append("autocracy_increased_stability_loss_chance_on_leader_death")
                
                result += adjust_stability(old_nation, new_nation, nation_schema, amounts, reasons)

                change_id = system_request_change(
                    data_type="nations",
                    item_id=old_nation["_id"],
                    change_type="Update",
                    before_data=old_nation,
                    after_data=new_nation,
                    reason="Death of " + old_character.get('name', 'Unknown') + " has caused an update for " + old_nation.get('name', 'Unknown')
                )
                system_approve_change(change_id)

                if not old_character.get("player") and not old_nation.get("players"):
                    result += generate_ai_character(old_nation, nation_schema, schema, previous_leader=old_character)

    return result

def character_heal_tick(old_character, new_character, schema):
    result = ""
    if new_character["health_status"] == "Healthy":
        return ""
    if new_character["health_status"] == "Dead":
        return ""
    
    health_status_enum = schema["properties"]["health_status"]["enum"]
    health_index = health_status_enum.index(old_character["health_status"])

    heal_roll = random.random()
    new_character["heal_roll"] = heal_roll
    new_character["heal_chance_at_tick"] = old_character.get("heal_chance", 0)
    if heal_roll <= old_character.get("heal_chance", 0):
        health_index = max(health_index - 1, 0)
        new_character["health_status"] = health_status_enum[health_index]
        result = f"{old_character.get('name', 'Unknown')} has healed from {old_character.get('health_status', 'Unknown')} to {new_character.get('health_status', 'Unknown')}.\n"
    return result

def character_heal_then_death_tick(old_character, new_character, schema, pending=None):
    result = ""
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""

    # Phase 1: Heal
    healed_to_healthy = False
    if old_character["health_status"] != "Healthy":
        health_status_enum = schema["properties"]["health_status"]["enum"]
        health_index = health_status_enum.index(old_character["health_status"])
        heal_roll = random.random()
        new_character["heal_roll"] = heal_roll
        new_character["heal_chance_at_tick"] = old_character.get("heal_chance", 0)
        if heal_roll <= old_character.get("heal_chance", 0):
            health_index = max(health_index - 1, 0)
            new_character["health_status"] = health_status_enum[health_index]
            result += f"{old_character.get('name', 'Unknown')} has healed from {old_character['health_status']} to {new_character['health_status']}.\n"
            healed_to_healthy = new_character["health_status"] == "Healthy"

    # Phase 2: Death chance — recalculate if healed to Healthy so injury modifier is removed
    if healed_to_healthy:
        recalculated = calculate_all_fields(new_character, schema, "character")
        death_chance = recalculated.get("death_chance", 0)
    else:
        death_chance = old_character.get("death_chance", 0)

    # Phase 3: Death roll
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = death_chance
    if death_roll <= death_chance:
        new_character["health_status"] = "Dead"
        new_character["ruling_nation_org"] = None
        new_character["region"] = None
        new_character["player"] = None
        result += f"{old_character.get('name', 'Unknown')} has died.\n"

        if old_character.get("ruling_nation_org", ""):
            nation_schema, nation_db = get_data_on_category("nations")
            try:
                old_nation = nation_db.find_one({"_id": ObjectId(old_character.get("ruling_nation_org", ""))})
            except Exception:
                old_nation = None
            if old_nation:
                old_nation.update(calculate_all_fields(old_nation, nation_schema, "nation"))
                new_nation = deepcopy(old_nation)
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = old_nation.get("stability_loss_chance_on_leader_death", 0)

                amounts = []
                reasons = []

                if leader_death_stab_loss_roll <= old_nation.get("stability_loss_chance_on_leader_death", 0):
                    amounts.append(-1)
                    reasons.append("stability_loss_chance_on_leader_death")

                if old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) > 0:
                    stability_loss_chance = min(
                        old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) * old_character.get("age", 1),
                        old_nation.get("max_stability_loss_chance_on_leader_death_per_age", 0)
                    )
                    leader_death_age_stab_loss_roll = random.random()
                    new_nation["leader_death_age_stab_loss_roll"] = leader_death_age_stab_loss_roll
                    new_nation["leader_death_age_stab_loss_chance_at_tick"] = stability_loss_chance

                    amount = 0
                    while stability_loss_chance > 1:
                        amount += 1
                        stability_loss_chance -= 1
                    if leader_death_age_stab_loss_roll <= stability_loss_chance:
                        amount += 1
                    if amount > 0:
                        amounts.append(-amount)
                        reasons.append("autocracy_increased_stability_loss_chance_on_leader_death")

                result += adjust_stability(old_nation, new_nation, nation_schema, amounts, reasons)

                _queue_change(
                    pending,
                    data_type="nations",
                    item_id=old_nation["_id"],
                    change_type="Update",
                    before_data=old_nation,
                    after_data=new_nation,
                    reason="Death of " + old_character.get('name', 'Unknown') + " has caused an update for " + old_nation.get('name', 'Unknown'),
                    already_calculated=True,
                )

    return result


def character_mana_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    new_character["magic_points"] = min(old_character.get("magic_points", 0) + old_character.get("magic_point_income", 0), old_character.get("magic_point_capacity", 0))
    return ""

def character_age_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    new_character["age"] = old_character["age"] + 1
    return ""

def character_stat_gain_tick(old_character, new_character, schema):
    """Passive stat gain: one random eligible stat per tick, same odds and
    same one-roll shape for AI and players alike.

    AI used to roll independently for EVERY stat each tick (up to 6
    gains/session instead of 1), which is what caused AI characters to
    snowball far past player stat levels — fixed by sharing this single-roll
    path with players instead of branching. AI also gets deliberate stat
    growth on top of this through the Character Training / Heir Training mech
    RPs (helpers/mech_rp_helpers.py).
    """
    result = ""
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""

    # stat_gain_chance is fully computed by compute_stat_gain_chance (cunning
    # scaling, immortal bonus, and any title/district/tech modifiers), already
    # clamped to [0, 1] — trust it directly instead of re-deriving an AI-only
    # bonus here, which used to double-count cunning and bypass the cap.
    effective_chance = old_character.get("stat_gain_chance", 0)
    name = old_character.get("name", "Unknown")

    if effective_chance <= 0:
        return ""

    new_character["stat_gain_chance_at_tick"] = effective_chance

    stat_gain_roll = random.random()
    new_character["stat_gain_roll"] = stat_gain_roll
    if stat_gain_roll <= effective_chance:
        possible_stats = [s for s in character_stats if old_character.get(s, 0) < old_character.get(s + "_cap", 4)]
        if possible_stats:
            stat = random.choice(possible_stats)
            modifiers = new_character.get("modifiers", [])
            modifiers.append({"_id": uuid.uuid4().hex[:8], "modifier_type": "attribute", "attribute": stat, "value": 1, "duration": -1, "source": "Stat gain tick"})
            new_character["modifiers"] = modifiers
            result = f"{name} has gained a level of {stat}.\n"

    return result

def artifact_loss_tick(old_character, new_character, schema, pending=None):
    result = ""

    artifact_loss_chance = old_character.get("artifact_loss_chance", 0)
    if artifact_loss_chance <= 0:
        return ""
    artifact_loss_roll = random.random()
    new_character["artifact_loss_roll"] = artifact_loss_roll
    new_character["artifact_loss_chance_at_tick"] = artifact_loss_chance
    if artifact_loss_roll <= artifact_loss_chance:
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        unequipped_artifacts = list(artifact_db.find({"owner": str(old_character.get("_id", "")), "equipped": False}))
        if unequipped_artifacts:
            old_artifact = random.choice(unequipped_artifacts)
            new_artifact = deepcopy(old_artifact)
            new_artifact["owner"] = "Lost"
            result = f"{old_character.get('name', 'Unknown')} has lost {old_artifact.get('name', 'Unknown')}.\n"
            _queue_change(
                pending,
                data_type="artifacts",
                item_id=old_artifact["_id"],
                change_type="Update",
                before_data=old_artifact,
                after_data=new_artifact,
                reason=old_artifact.get('name', 'Unknown') + " has been lost due to passive loss chance"
            )
        else:
            equipped_artifacts = list(artifact_db.find({"owner": str(old_character.get("_id", "")), "equipped": True}))
            if equipped_artifacts:
                old_artifact = random.choice(equipped_artifacts)
                new_artifact = deepcopy(old_artifact)
                new_artifact["owner"] = "Lost"
                result = f"{old_character.get('name', 'Unknown')} has lost {old_artifact.get('name', 'Unknown')}.\n"
                _queue_change(
                    pending,
                    data_type="artifacts",
                    item_id=old_artifact["_id"],
                    change_type="Update",
                    before_data=old_artifact,
                    after_data=new_artifact,
                    reason="Loss of " + old_character.get('name', 'Unknown') + " has caused " + old_artifact.get('name', 'Unknown') + " to be lost"
                )
    return result

###########################################################
# Artifact Tick Functions
###########################################################

###########################################################
# Merchant Tick Functions
###########################################################

def merchant_income_tick(old_merchant, new_merchant, schema):
    income = old_merchant.get("income", 0)
    new_merchant["treasury"] = int(old_merchant.get("treasury", 0)) + income

    capacity = old_merchant.get("resource_capacity", {})
    new_merchant["resource_storage"] = {}
    for resource, amount in old_merchant.get("resource_production", {}).items():
        stored = old_merchant.get("resource_storage", {}).get(resource, 0) + amount
        stored = min(stored, capacity.get(resource, 0))
        new_merchant["resource_storage"][resource] = max(int(stored), 0)
    name = old_merchant.get("name", "Unknown")
    return f"{name}: +{income} gold -> {new_merchant['treasury']} treasury\n"

###########################################################
# Mercenary Tick Functions
###########################################################

def mercenary_upkeep_tick(old_mercenary, new_mercenary, schema):
    upkeep = old_mercenary.get("upkeep", 0)
    new_mercenary["treasury"] = int(old_mercenary.get("treasury", 0)) - upkeep
    name = old_mercenary.get("name", "Unknown")
    return f"{name}: -{upkeep} gold upkeep -> {new_mercenary['treasury']} treasury\n"

###########################################################
# Faction Tick Functions
###########################################################

def faction_income_tick(old_faction, new_faction, schema):
    income = old_faction.get("influence_income", 0)
    new_faction["influence"] = int(old_faction.get("influence", 0)) + income
    name = old_faction.get("name", "Unknown")
    return f"{name}: +{income} influence -> {new_faction['influence']}\n"

###########################################################
# Market Tick Functions
###########################################################

def market_income_tick(old_market, new_market, schema):
    new_market["resource_storage"] = {}
    for resource, amount in old_market.get("resource_production", {}).items():
        new_market["resource_storage"][resource] = old_market.get("resource_storage", {}).get(resource, 0) + amount
        new_market["resource_storage"][resource] = min(new_market["resource_storage"][resource], old_market.get("market_resource_capacity", {}).get(resource, 0))
        new_market["resource_storage"][resource] = max(new_market["resource_storage"][resource], 0)
        new_market["resource_storage"][resource] = int(new_market["resource_storage"][resource])

    # Luxury market: re-roll this session's luxury offerings (1 rolled per
    # market tier, duplicates allowed). These are not accumulated/stored —
    # the whole list is replaced every session, never carried over.
    from calculations.field_calculations import collect_laws, sum_law_totals
    law_totals = sum_law_totals(collect_laws(old_market, schema))
    gen_per_tier = law_totals.get("generate_luxury_resources_per_market_tier", 0)
    luxury_log = ""
    if gen_per_tier > 0:
        tier_mult = int(law_totals.get("market_tier_multiplier", 1))
        roll_count = max(0, int(round(gen_per_tier * tier_mult)))
        luxury_resources = json_data.get("luxury_resources", [])
        if roll_count and luxury_resources:
            rolled = [random.choice(luxury_resources)["key"] for _ in range(roll_count)]
            new_market["market_luxury_resources"] = rolled
            luxury_log = f", luxuries for sale: {', '.join(rolled)}"
        else:
            new_market["market_luxury_resources"] = []
    else:
        new_market["market_luxury_resources"] = []

    name = old_market.get("name", "Unknown")
    produced = [f"+{amt} {r}" for r, amt in old_market.get("resource_production", {}).items() if amt]
    produced_str = ", ".join(produced) if produced else "no production"
    return f"{name}: {produced_str}{luxury_log}\n"


def _market_member_nation_names(market_id_str):
    """Return the list of nation names that are members of a market (via
    market_links), resolved from the member ids stored there."""
    member_ids = [
        lnk["member"] for lnk in mongo.db.market_links.find({"market": market_id_str}, {"member": 1})
        if lnk.get("member")
    ]
    if not member_ids:
        return []
    object_ids = []
    for mid in member_ids:
        try:
            object_ids.append(ObjectId(mid))
        except Exception:
            pass
    if not object_ids:
        return []
    return [
        n["name"] for n in mongo.db.nations.find({"_id": {"$in": object_ids}}, {"name": 1})
        if n.get("name")
    ]


def bandit_camp_spawn_tick(old_market, new_market, schema, pending_tiles=None):
    """Each session, a market has a chance (bandit_camp_spawn_chance — base
    5%, moved by market type and member protection stances, see
    markets.json/market_links.json) to spawn a new bandit camp preying on
    its own trade network.

    Eligible tiles are road ("route") tiles owned by one of the market's
    member nations — bandits camp out on the roads nations actually use to
    trade, not just anywhere on the map. Prefers a tile with no city/
    district/wonder/bandit_camp already on it, but falls back to an
    occupied route tile rather than never spawning if every member's roads
    are already fully built up.

    Runs before nations are recalculated this same tick (MARKET_TICK_FUNCTIONS
    runs first in tick()), but the write itself is deferred via
    pending_tiles/_queue_tile_write like every other tile claim this tick —
    so a freshly spawned camp only starts affecting trade_risk/raids from
    next session onward, once the deferred write actually commits.
    """
    market_name = old_market.get("name", "Unknown")
    spawn_chance = old_market.get("bandit_camp_spawn_chance", 0.05)
    if random.random() >= spawn_chance:
        return ""

    member_names = _market_member_nation_names(str(old_market.get("_id", "")))
    if not member_names:
        return f"{market_name}: bandit camp spawn roll succeeded, but the market has no members to prey on.\n"

    route_tiles = list(mongo.db.hex_map_tiles.find(
        {"owner": {"$in": member_names}, "route": {"$exists": True, "$ne": None}},
        {"_id": 1, "city": 1, "district": 1, "wonder": 1, "bandit_camp": 1},
    ))
    if not route_tiles:
        return f"{market_name}: bandit camp spawn roll succeeded, but no member has any road tiles to place one on.\n"

    empty_tiles = [
        t for t in route_tiles
        if not (t.get("city") or t.get("district") or t.get("wonder") or t.get("bandit_camp"))
    ]
    tile = random.choice(empty_tiles or route_tiles)

    camp_name = f"{market_name} Bandit Camp"
    _queue_tile_write(pending_tiles, tile["_id"], {
        "bandit_camp": {"name": camp_name, "tier": 1, "market": market_name},
    })
    return f"{market_name}: a new bandit camp (\"{camp_name}\") has spawned, preying on the market's trade routes.\n"


def complex_trade_bandit_loss_tick(old_market, new_market, schema, pending_tiles=None):
    """Complex (ongoing) trade routes have a flat 25% chance, each session,
    of having that session's delivery lost to a bandit camp if this market
    has one anywhere in it.

    Runs as a market tick — before nations are recalculated this same
    session — so a raided route's resources_a_to_b/resources_b_to_a net to
    zero for BOTH sides starting with THIS session's money/resource income,
    not one session late. The stolen cargo is logged on the raiding bandit
    camp tile itself (bandit_camp.stolen_goods) so it's visible on the map.

    A route between two nations who share more than one market with a camp
    would otherwise get rolled once per shared market on the same session —
    raid_checked_sessions guards against that, regardless of which market's
    tick reaches the route first.
    """
    market_name = old_market.get("name", "Unknown")
    camp_tiles = list(mongo.db.hex_map_tiles.find(
        {"bandit_camp.market": market_name}, {"_id": 1, "bandit_camp": 1},
    ))
    if not camp_tiles:
        return ""

    member_names = _market_member_nation_names(str(old_market.get("_id", "")))
    if len(member_names) < 2:
        return ""

    from helpers.trade_route_helpers import is_delivering, _current_session as _tr_current_session, _nations_in_stasis
    current_session = _tr_current_session()
    stasis_names = _nations_in_stasis(member_names)

    routes = list(mongo.db.trade_routes.find({
        "status": {"$in": ["active", "ending"]},
        "nation_a": {"$in": member_names},
        "nation_b": {"$in": member_names},
    }))
    if not routes:
        return ""

    # Cumulative local snapshot of each camp tile's bandit_camp dict — every
    # raid re-queues the FULL dict (rather than one re-read from camp_tiles)
    # so two routes raiding the same tile in this same call don't clobber
    # each other's stolen_goods entry (_commit_pending_tile_writes merges
    # queued writes per top-level key, last write wins — not a deep merge).
    camp_state = {t["_id"]: dict(t.get("bandit_camp") or {}) for t in camp_tiles}
    camp_tile_ids = list(camp_state.keys())

    log_lines = []
    for route in routes:
        if not is_delivering(route, current_session):
            continue
        if route.get("nation_a") in stasis_names or route.get("nation_b") in stasis_names:
            continue  # paused, nothing moving to lose
        if current_session in route.get("raid_checked_sessions", []):
            continue  # already resolved this session via another shared market

        mongo.db.trade_routes.update_one(
            {"_id": route["_id"]},
            {"$addToSet": {"raid_checked_sessions": current_session}},
        )

        if random.random() >= 0.25:
            continue

        mongo.db.trade_routes.update_one(
            {"_id": route["_id"]},
            {"$addToSet": {"raided_sessions": current_session}},
        )

        tile_id = random.choice(camp_tile_ids)
        stolen_goods = list(camp_state[tile_id].get("stolen_goods", []))
        stolen_goods.append({
            "session": current_session,
            "nation_a": route.get("nation_a", ""),
            "nation_b": route.get("nation_b", ""),
            "resources_a_to_b": route.get("resources_a_to_b", []),
            "resources_b_to_a": route.get("resources_b_to_a", []),
        })
        camp_state[tile_id]["stolen_goods"] = stolen_goods
        _queue_tile_write(pending_tiles, tile_id, {"bandit_camp": camp_state[tile_id]})

        log_lines.append(
            f"{market_name}: trade route {route.get('nation_a')} <-> {route.get('nation_b')} raided by a bandit camp this session.\n"
        )

    return "".join(log_lines)


###########################################################
# Nation Tick Functions
###########################################################

def isolated_diplo_stance_tick(old_nation, new_nation, schema):
    if old_nation.get("diplomatic_stance", "None") != "Isolated":
        modifiers = new_nation.get("modifiers", [])
        for modifier in modifiers:
            if modifier.get("field", "") == "stability_gain_chance" and modifier.get("source", "") == "Isolated Diplomatic Stance":
                removed_stab_gain = modifier["value"]
                modifiers.remove(modifier)
                new_nation["modifiers"] = modifiers
                old_nation["stability_gain_chance"] -= removed_stab_gain #Remove the stab gain chance because if the nation swapped off Isolated, they should not keep the stab gain chance
                return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance removed because they are no longer Isolated.\n"
        return ""
    else:
        gain_rate = old_nation.get("isolated_stab_gain_rate", 0)
        cap = old_nation.get("isolated_stab_gain_max", 0)
        print(f"Gain Rate: {gain_rate}, Cap: {cap}")
        modifiers = new_nation.get("modifiers", [])
        for modifier in modifiers:
            if modifier.get("field", "") == "stability_gain_chance" and modifier.get("source", "") == "Isolated Diplomatic Stance":
                old_value = modifier["value"]
                new_value = round(min(modifier["value"] + gain_rate, cap), 4)
                modifier["value"] = new_value
                new_nation["modifiers"] = modifiers
                return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased from {old_value} to {new_value}.\n"
        modifiers.append({"_id": uuid.uuid4().hex[:8], "field": "stability_gain_chance", "value": gain_rate, "duration": -1, "source": "Isolated Diplomatic Stance"})
        new_nation["modifiers"] = modifiers
        return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased from 0 to {gain_rate}.\n"

def ai_resource_desire_tick(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    general_resources = [resource["key"] for resource in json_data["general_resources"]]
    unique_resources = [resource["key"] for resource in json_data["unique_resources"]]
    luxury_resources = [resource["key"] for resource in json_data["luxury_resources"]]
    common_resources = general_resources + unique_resources
    general_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["general_resources"]}
    unique_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["unique_resources"]}
    luxury_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["luxury_resources"]}
    resource_prices = {**general_resource_prices, **unique_resource_prices, **luxury_resource_prices}
    new_nation["resource_desires"] = []
    for resource in common_resources:
        desire_roll = random.random()
        base_price = resource_prices[resource]
        price_roll = random.random() / 10 #Rolls somewhere between 0 and 10
        trade_type = "None"
        price = 0
        quantity = random.randint(1, 5)  # Random quantity between 1-5
        if desire_roll <= 0.1:
            price = base_price * (1.15 + price_roll)
            trade_type = "Need to Buy"
        elif desire_roll <= 0.25:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Buy"
        elif desire_roll >= 0.75:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Sell"
        elif desire_roll >= 0.9:
            price = base_price * (0.75 + price_roll)
            trade_type = "Need to Sell"
        price = int(round(price / 5)) * 5
        if price != 0:
            new_nation["resource_desires"].append({"resource": resource, "trade_type": trade_type, "price": price, "quantity": quantity})
    
    for resource in luxury_resources:
        desire_roll = random.random()
        base_price = resource_prices[resource]
        price_roll = random.random() / 10 #Rolls somewhere between 0 and 0.1
        trade_type = "None"
        price = 0
        quantity = 1
        if desire_roll <= 0.05:
            price = base_price * (1.15 + price_roll)
            trade_type = "Need to Buy"
        elif desire_roll <= 0.1:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Buy"
        price = int(round(price / 5)) * 5
        if price != 0:
            new_nation["resource_desires"].append({"resource": resource, "trade_type": trade_type, "price": price, "quantity": quantity})
        
    return ""

def nation_income_tick(old_nation, new_nation, schema):
    money_income = old_nation.get("money_income", 0)
    new_nation["money"] = int(old_nation.get("money", 0)) + money_income
    if new_nation["money"] > new_nation.get("money_capacity", 0):
        new_nation["money"] = new_nation.get("money_capacity", 0)
    new_nation["resource_storage"] = {}
    new_nation["production_at_tick"] = old_nation.get("resource_excess", {})
    for resource, amount in old_nation.get("resource_excess", {}).items():
        new_nation["resource_storage"][resource] = min(
            old_nation.get("resource_storage", {}).get(resource, 0) + amount,
            old_nation.get("nation_resource_capacity", {}).get(resource, 0)
        )

    if old_nation.get("temperament") != "Player":
        return ""
    name = old_nation.get("name", "Unknown")
    lines = [f"{name}: +{money_income} gold -> {new_nation['money']}"]
    notable = [
        f"{'+' if amt > 0 else ''}{amt} {r}"
        for r, amt in old_nation.get("resource_excess", {}).items()
        if amt != 0
    ]
    if notable:
        lines.append("  Resources: " + ", ".join(notable))
    return "\n".join(lines) + "\n"

def _grant_resource_windfall(old_nation, new_nation, rolls, event_label):
    """Grant `rolls` units of random non-research general resources (one roll
    = one unit of one randomly chosen resource, so the same resource can be
    hit more than once), added directly to storage.

    Deliberately NOT capped at nation_resource_capacity: windfalls are meant
    to give the nation a chance to spend an overflow before the cap gets
    reapplied by Nation Income Tick at the START of next session. Capping here
    would silently destroy the whole point of a windfall for a nation already
    near capacity.

    Shared by the tick-driven "resource windfall on X" modifiers (tech
    researched, stability loss, expansion) — mirrors era_formal_storage_bonus_tick's
    grant pattern. Returns a log line, or "" if rolls <= 0.

    Must run reading/writing new_nation["resource_storage"] (not old_nation's)
    since Nation Income Tick already rebuilds it earlier in NATION_TICK_FUNCTIONS.
    """
    if rolls <= 0:
        return ""
    resource_pool = [r["key"] for r in json_data.get("general_resources", []) if r["key"] != "research"]
    if not resource_pool:
        return ""

    gained = {}
    for _ in range(int(rolls)):
        resource = random.choice(resource_pool)
        gained[resource] = gained.get(resource, 0) + 1

    storage = new_nation.get("resource_storage") or {}
    for resource, amount in gained.items():
        storage[resource] = storage.get(resource, 0) + amount
    new_nation["resource_storage"] = storage

    gained_str = ", ".join(f"{v} {k}" for k, v in gained.items())
    name = old_nation.get("name", "Unknown")
    return f"{name} gained {gained_str} from a resource windfall ({event_label}).\n"


def nation_tech_tick(old_nation, new_nation, schema):
    new_nation["research_production_at_tick"] = old_nation.get("resource_production", {}).get("research", 0)
    new_nation["research_consumption_at_tick"] = old_nation.get("resource_consumption", {}).get("research", 0)
    json_tech_data = json_data["tech"]

    techs = old_nation.get("technologies")
    new_nation["technologies"] = deepcopy(techs) if isinstance(techs, dict) else {"political_philosophy": {"researched": True}}
    result = ""
    name = old_nation.get("name", "Unknown")
    for tech, value in new_nation["technologies"].items():
        if value.get("investing", 0) > 0:
            value["invested"] = value.get("invested", 0) + value.get("investing", 0)
            value["investing"] = 0
            _tech_def = json_tech_data.get(tech, {})
            _cat_mod = (old_nation.get("technology_category_cost_modifiers", {}) or {}).get((_tech_def.get("type") or "").lower(), 0)
            cost = value.get("cost", _tech_def.get("cost", 0) + old_nation.get("technology_cost_modifier", 0) + _cat_mod)
            if value["invested"] >= cost:
                value["researched"] = True
                display = json_tech_data.get(tech, {}).get("display_name", tech)
                result += f"{name} has researched {display}.\n"
                windfall_rolls = old_nation.get("resource_windfall_on_tech_researched", 0)
                # Percentage-of-base-cost variant: rolls scale with how expensive
                # the tech was (its BASE cost, unmodified by the nation's own
                # cost reductions), so a 50% modifier on a 20-cost tech grants 10.
                base_cost = _tech_def.get("cost", 0)
                pct = old_nation.get("resource_windfall_pct_of_tech_cost", 0)
                windfall_rolls += round(base_cost * pct)
                result += _grant_resource_windfall(old_nation, new_nation, windfall_rolls, f"researched {display}")
        new_nation["technologies"][tech] = value
    return result

def update_rolling_karma(old_nation, new_nation, schema):
    event_type = old_nation.get("event_type", "Unknown")

    law_totals = sum_law_totals(collect_laws(old_nation, schema))
    ignore_negative = law_totals.get("ignore_negative_event_karma", 0) >= 1
    ignore_positive = law_totals.get("ignore_positive_event_karma", 0) >= 1

    if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
        if not ignore_negative:
            new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) + 1
    elif event_type in ["Good", "Very Good", "Fantastic", "Wonderous"]:
        if not ignore_positive:
            new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) - 1

    return ""

def nation_infamy_decay_tick(old_nation, new_nation, schema):
    infamy = int(old_nation.get("infamy", 0))
    if infamy == 0:
        return ""

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    current_session = global_modifiers.get("session_counter", 0) if global_modifiers else 0

    nation_id_str = str(old_nation.get("_id", ""))
    attacker_links = list(mongo.db.war_links.find({"participant": nation_id_str, "stance": "Attacker"}))
    for link in attacker_links:
        war = mongo.db.wars.find_one({"_id": ObjectId(link["war"])}) if link.get("war") else None
        if war:
            session_declared = war.get("session_declared", 0)
            session_ended = war.get("session_ended", None)
            if session_declared <= current_session and (session_ended is None or session_ended >= current_session):
                new_nation["infamy"] = infamy
                return ""

    decay = max(min(math.floor((infamy / 2) / 5) * 5, 20), 5)
    new_nation["infamy"] = max(0, infamy - decay)
    return ""

def nation_war_support_tick(old_nation, new_nation, schema):
    """War support drops each session the nation is actively at war — more
    for fighting offensively (-20) than defensively (-10); both apply and
    stack if the nation is doing both at once in different wars — and
    recovers at peace (+10, capped at 100). Same live-war liveness check as
    nation_infamy_decay_tick (session_declared/session_ended against the
    current session), just checked for both stances instead of only
    Attacker."""
    war_support = int(old_nation.get("war_support", 100))

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    current_session = global_modifiers.get("session_counter", 0) if global_modifiers else 0

    nation_id_str = str(old_nation.get("_id", ""))
    is_offensive = False
    is_defensive = False
    links = mongo.db.war_links.find({"participant": nation_id_str, "stance": {"$in": ["Attacker", "Defender"]}})
    for link in links:
        war = mongo.db.wars.find_one({"_id": ObjectId(link["war"])}) if link.get("war") else None
        if not war:
            continue
        session_declared = war.get("session_declared", 0)
        session_ended = war.get("session_ended", None)
        if session_declared <= current_session and (session_ended is None or session_ended >= current_session):
            if link.get("stance") == "Attacker":
                is_offensive = True
            elif link.get("stance") == "Defender":
                is_defensive = True

    if is_offensive:
        war_support -= 20
    if is_defensive:
        war_support -= 10
    if not is_offensive and not is_defensive:
        war_support += 10

    new_nation["war_support"] = max(0, min(100, war_support))
    return ""

def nation_infamy_consequences_tick(old_nation, new_nation, schema):
    """Guaranteed consequences for ending a session at 100+ infamy: flags the
    nation for a civil war (an admin picks the breakaway details via the
    Civil War Helper — territory/unit splits aren't something to decide
    algorithmically), and immediately forces every vassal into open rebellion.
    """
    infamy = old_nation.get("infamy", 0)
    if infamy < 100:
        return ""

    result = ""
    name = old_nation.get("name", "Unknown")

    if not old_nation.get("pending_civil_war"):
        new_nation["pending_civil_war"] = True
        result += f"{name} has ended a session at 100 or more infamy and is guaranteed a civil war — flagged for admin review in the Civil War Helper.\n"

    nation_id_str = str(old_nation.get("_id", ""))
    vassals = list(mongo.db.nations.find({"overlord": nation_id_str}, {"name": 1, "rebellion_chance": 1}))
    for vassal in vassals:
        mongo.db.nations.update_one(
            {"_id": vassal["_id"]},
            {"$set": {
                "rebellion_roll": 0.0,
                "rebellion_chance_at_tick": vassal.get("rebellion_chance", 0),
            }}
        )
        result += f"{vassal.get('name', 'Unknown')} has rebelled against their overlord due to {name}'s overwhelming infamy.\n"

    return result

def nation_prestige_gain_tick(old_nation, new_nation, schema):
    if not old_nation.get("empire", False):
        return ""
    old_nation_prestige = old_nation.get("prestige", 0)
    if old_nation_prestige == "":
        old_nation_prestige = 0
    new_nation["prestige"] = old_nation_prestige + old_nation.get("prestige_gain", 0)
    new_nation["prestige"] = min(max(new_nation["prestige"], 0), 100)
    return ""

def nation_civil_war_tick(old_nation, new_nation, schema):
    if old_nation.get("civil_war_chance", 0) == 0:
        return ""
    civil_war_roll = random.random()
    new_nation["passive_civil_war_roll"] = civil_war_roll
    new_nation["passive_civil_war_chance_at_tick"] = old_nation.get("civil_war_chance", 0)
    if civil_war_roll <= old_nation.get("civil_war_chance", 0):
        new_nation["stability"] = "Unsettled"
        return f"{old_nation.get('name', 'Unknown')} has experienced a civil war due to passive civil war chance.\n"
    return ""


def nation_stability_tick(old_nation, new_nation, schema):
    result = ""

    stability_gained = 0
    stability_lost = 0

    stability_gain_roll = random.random()
    new_nation["stability_gain_roll"] = stability_gain_roll
    new_nation["stability_gain_chance_at_tick"] = old_nation.get("stability_gain_chance", 0)

    while old_nation.get("stability_gain_chance", 0) > 1:
        stability_gained += 1
        old_nation["stability_gain_chance"] -= 1

    if stability_gain_roll <= old_nation.get("stability_gain_chance", 0):
        stability_gained += 1

    stab_loss_chance = old_nation.get("stability_loss_chance", 0)
    stability_loss_roll = random.random()
    new_nation["stability_loss_roll"] = stability_loss_roll
    new_nation["stability_loss_chance_at_tick"] = stab_loss_chance

    while stab_loss_chance > 1:
        stability_lost += 1
        stab_loss_chance -= 1

    if stability_loss_roll <= stab_loss_chance:
        stability_lost += 1

    amounts = []
    reasons = []

    if stability_gained > 0:
        amounts.append(stability_gained)
        reasons.append("stability_gain_chance")
    if stability_lost > 0:
        amounts.append(-stability_lost)
        reasons.append("stability_loss_chance")

    result += adjust_stability(old_nation, new_nation, schema, amounts, reasons)

    return result

def _resource_storage_capacity(nation_doc):
    """Best-effort {resource_key: capacity} for a nation, from whatever is
    currently cached on its document (calculated field, refreshed whenever
    the nation is recalculated). An empty/missing dict yields no unique
    resources — only the caller's own base-storage general resources."""
    return nation_doc.get("nation_resource_capacity") or {}


def nation_concessions_tick(old_nation, new_nation, schema):
    if old_nation.get("overlord", "") == "":
        return ""

    result = ""

    if old_nation.get("concessions", {}) and old_nation.get("concessions", {}) != {} and old_nation.get("concessions", {}) != "":
        new_nation["concessions"] = {}
        compliance_enum = schema["properties"]["compliance"]["enum"]
        compliance_index = compliance_enum.index(old_nation["compliance"])
        if compliance_index <= 1:
            if random.random() <= 0.5:
                result += f"{old_nation.get('name', 'Unknown')} has immediately rebelled against their overlord!\n"
        else:
            new_nation["compliance"] = compliance_enum[compliance_index - 1]
            result += (
                f"{old_nation.get('name', 'Unknown')} has had their compliance reduced"
                f" from {old_nation.get('compliance', 'Unknown')} to {new_nation.get('compliance', 'Unknown')}"
                f" due to concessions not being paid.\n"
            )

    # Cooldown: a vassal that was granted concessions last session cannot be
    # granted them again this session (win or lose, paid or not) — consumed
    # after skipping exactly one session.
    if old_nation.get("concessions_granted_last_session", False):
        new_nation["concessions_granted_last_session"] = False
        new_nation["concessions_roll"] = 1
        new_nation["concessions_chance_at_tick"] = 0
        return result

    concessions_roll = random.random()
    new_nation["concessions_roll"] = concessions_roll
    new_nation["concessions_chance_at_tick"] = old_nation.get("concessions_chance", 0)
    if concessions_roll <= old_nation.get("concessions_chance", 0):
        concessions_qty = old_nation.get("concessions_qty", 0)

        overlord = None
        overlord_id = old_nation.get("overlord", "")
        if overlord_id:
            try:
                overlord = mongo.db.nations.find_one(
                    {"_id": ObjectId(overlord_id)}, {"nation_resource_capacity": 1}
                )
            except Exception:
                overlord = None

        # Only demand resources BOTH sides can actually hold — e.g. gunpowder
        # (base storage 0) shouldn't come up unless both the vassal and the
        # overlord have unlocked storage capacity for it. General resources
        # (food/wood/stone/mounts/magic) have positive base storage for every
        # nation, so this mainly filters unique resources like gunpowder.
        vassal_capacity = _resource_storage_capacity(old_nation)
        overlord_capacity = _resource_storage_capacity(overlord or {})

        resources = []
        for resource in json_data["general_resources"] + json_data["unique_resources"]:
            key = resource["key"]
            if key == "research":
                continue
            if vassal_capacity.get(key, 0) > 0 and overlord_capacity.get(key, 0) > 0:
                resources.append(key)

        if len(resources) < 2:
            # Not enough mutually-storable resources to demand a two-resource
            # concession this tick. No concessions granted, so the cooldown
            # is not triggered.
            new_nation["concessions_granted_last_session"] = False
            return result

        first_resource = random.choice(resources)
        resources.remove(first_resource)
        second_resource = random.choice(resources)
        first_amount = random.randint(1, concessions_qty - 1)
        second_amount = concessions_qty - first_amount

        new_nation["concessions"] = {
            first_resource: first_amount,
            second_resource: second_amount
        }
        new_nation["concessions_granted_last_session"] = True

        result += f"{old_nation.get('name', 'Unknown')} has demanded concessions from their overlord.\n"
    else:
        new_nation["concessions_granted_last_session"] = False
    return result

def nation_rebellion_tick(old_nation, new_nation, schema):
    if old_nation.get("overlord", "") == "":
        return ""
    result = ""
    rebellion_roll = random.random()
    new_nation["rebellion_roll"] = rebellion_roll
    new_nation["rebellion_chance_at_tick"] = old_nation.get("rebellion_chance", 0)
    if rebellion_roll <= old_nation.get("rebellion_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has rebelled against their overlord.\n"
        overlord_id = old_nation.get("overlord", "")
        if overlord_id:
            try:
                join_chances = {"Defiant": 0.25, "Rebellious": 0.75}
                other_vassals = list(mongo.db.nations.find(
                    {"overlord": overlord_id, "_id": {"$ne": old_nation.get("_id")}},
                    {"name": 1, "compliance": 1}
                ))
                for vassal in other_vassals:
                    join_chance = join_chances.get(vassal.get("compliance", ""), 0)
                    if join_chance > 0 and random.random() <= join_chance:
                        result += f"{vassal.get('name', 'Unknown')} has joined the rebellion against their overlord.\n"
            except Exception:
                pass

    return result

def nation_vassal_compliance_tick(old_nation, new_nation, schema):
    if not old_nation.get("overlord"):
        return ""

    result = ""
    compliance_gained = 0
    compliance_lost = 0

    gain_chance = old_nation.get("compliance_gain_chance", 0)
    gain_roll = random.random()
    new_nation["compliance_gain_roll"] = gain_roll
    new_nation["compliance_gain_chance_at_tick"] = gain_chance
    while gain_chance > 1:
        compliance_gained += 1
        gain_chance -= 1
    if old_nation.get("compliance", "None") != "Loyal" and gain_roll <= gain_chance:
        compliance_gained += 1

    loss_chance = old_nation.get("compliance_loss_chance", 0)
    loss_roll = random.random()
    new_nation["compliance_loss_roll"] = loss_roll
    new_nation["compliance_loss_chance_at_tick"] = loss_chance
    while loss_chance > 1:
        compliance_lost += 1
        loss_chance -= 1
    if loss_roll <= loss_chance:
        compliance_lost += 1

    amounts = []
    reasons = []
    if compliance_gained > 0:
        amounts.append(compliance_gained)
        reasons.append("compliance_gain_chance")
    if compliance_lost > 0:
        amounts.append(-compliance_lost)
        reasons.append("compliance_loss_chance")

    if amounts:
        result += adjust_compliance(old_nation, new_nation, schema, amounts, reasons)

    return result
    return ""


def ai_vassal_concessions_payment_tick(old_nations, new_nations, schema):
    """Cross-nation tick: AI overlords pay the resources listed in a vassal's
    pending concessions demand (see nation_concessions_tick, which runs
    earlier in the same session and is what actually rolls/expires demands).

    Paying normally just prevents the demand from going unpaid into next
    session — nation_concessions_tick drops a vassal's compliance a level
    (or risks immediate rebellion) if its concessions dict is still non-empty
    when that tick next runs, so clearing it here (in the same session it was
    demanded) avoids that loss entirely without otherwise touching compliance.

    When the overlord's subject_stance law is Benevolence, the demand is
    already inflated by that law's vassal_nation_concessions_qty_mult (a flat
    x2 on the vassal's own concessions_qty, applied generically the same way
    every other vassal_nation_* law modifier is) — paying that larger, already
    more expensive demand also raises the vassal's compliance by a level,
    instead of merely preventing a loss.

    Registered in NATION_CROSS_TICK_FUNCTIONS (not NATION_TICK_FUNCTIONS)
    because it must write to a DIFFERENT nation's (the vassal's) document.
    An ordinary per-nation tick function only ever touches its own
    old_nation/new_nation pair — mutating a sibling nation's Mongo document
    directly from inside one would get silently overwritten later, when the
    tick's own batch save loop commits that sibling's separately-tracked
    new_nations[i] snapshot back over it. Cross tick functions instead
    receive the full old_nations/new_nations lists and mutate new_nations[i]
    by index directly, exactly like ai_market_matching_tick — the batch save
    loop picks up those in-place edits naturally.
    """
    result = ""
    id_to_idx = {str(n.get("_id", "")): i for i, n in enumerate(old_nations)}

    for vidx, old_vassal in enumerate(old_nations):
        # Read from new_vassal, not old_vassal — nation_concessions_tick (an
        # earlier NATION_TICK_FUNCTIONS pass, same session) is what actually
        # rolls a fresh demand, so by the time this cross tick runs,
        # new_nations[vidx] already holds this session's current demand.
        new_vassal = new_nations[vidx]
        concessions = new_vassal.get("concessions")
        if not isinstance(concessions, dict) or not concessions:
            continue

        overlord_id = str(old_vassal.get("overlord") or "")
        overlord_idx = id_to_idx.get(overlord_id)
        if overlord_idx is None:
            continue
        old_overlord = old_nations[overlord_idx]
        if old_overlord.get("temperament", "Player") == "Player":
            continue
        new_overlord = new_nations[overlord_idx]

        overlord_storage = dict(new_overlord.get("resource_storage") or {})
        if any(overlord_storage.get(res, 0) < qty for res, qty in concessions.items()):
            continue  # can't afford the full demand this session — leave it pending

        for res, qty in concessions.items():
            overlord_storage[res] = overlord_storage.get(res, 0) - qty
        new_overlord["resource_storage"] = overlord_storage

        vassal_storage = dict(new_vassal.get("resource_storage") or {})
        vassal_caps = _resource_storage_capacity(new_vassal) or _resource_storage_capacity(old_vassal)
        for res, qty in concessions.items():
            new_amount = vassal_storage.get(res, 0) + qty
            cap = vassal_caps.get(res)
            if cap is not None:
                new_amount = min(new_amount, cap)
            vassal_storage[res] = new_amount
        new_vassal["resource_storage"] = vassal_storage
        new_vassal["concessions"] = {}

        demand_desc = ", ".join(f"{qty} {res}" for res, qty in concessions.items())
        result += (
            f"{old_overlord.get('name', 'Unknown')} paid {old_vassal.get('name', 'Unknown')}'s "
            f"concessions demand ({demand_desc}).\n"
        )

        if old_overlord.get("subject_stance") == "Benevolence":
            result += adjust_compliance(
                new_vassal, new_vassal, schema, [1],
                ["its overlord's benevolent payment of concessions"],
            )

    return result


def nation_enclave_compliance_tick(old_nation, new_nation, schema):
    if not old_nation.get("overlord") or old_nation.get("vassal_type") != "Enclave":
        return ""
    compliance = old_nation.get("compliance", "None")
    if compliance == "None":
        return ""
    try:
        overlord = mongo.db.nations.find_one(
            {"_id": ObjectId(old_nation["overlord"])}, {"primary_religion": 1}
        )
    except Exception:
        return ""
    if not overlord:
        return ""
    vassal_religion = str(old_nation.get("primary_religion") or "")
    overlord_religion = str(overlord.get("primary_religion") or "")
    if not overlord_religion or vassal_religion == overlord_religion:
        return ""
    compliance_enum = schema["properties"]["compliance"]["enum"]
    idx = compliance_enum.index(compliance)
    if idx <= 1:
        if random.random() <= 0.5:
            return (
                f"{old_nation.get('name', 'Unknown')} has immediately rebelled against their overlord"
                f" due to religious differences!\n"
            )
        return ""
    new_compliance = compliance_enum[idx - 1]
    new_nation["compliance"] = new_compliance
    return (
        f"{old_nation.get('name', 'Unknown')}'s compliance fell from {compliance} to {new_compliance}"
        f" due to religious differences with their overlord.\n"
    )



def nation_passive_expansion_tick(old_nation, new_nation, schema, pending_tiles=None):
    from helpers.hex_map_helpers import select_passive_expansion_tiles

    result = ""

    expansion_chance = old_nation.get("passive_expansion_chance", 0)
    new_nation["expansion_chance_at_tick"] = expansion_chance

    # One roll per tick for every nation, AI and Player alike.
    roll = random.random()
    new_nation["expansion_roll"] = roll
    successes = 1 if roll <= expansion_chance else 0

    if successes == 0:
        return result

    nation_name = old_nation.get("name", "Unknown")

    # Fetch tiles once; reuse across multiple successful rolls so that tiles
    # claimed in earlier rounds are visible to later rounds. Need _id (unlike
    # before) since claims now go through _queue_tile_write, which addresses
    # tiles by _id rather than the q/r filter a direct update_one used.
    all_tiles = list(mongo.db.hex_map_tiles.find(
        {},
        {"q": 1, "r": 1, "terrain": 1, "owner": 1,
         "city": 1, "district": 1, "wonder": 1, "capital": 1,
         "portal": 1, "route": 1, "_id": 1},
    ))
    tile_map = {(t["q"], t["r"]): t for t in all_tiles}

    # For each successful roll, select and claim tiles
    claimed = []
    expansion_events = 0  # rounds that actually claimed tiles, not just rolled a success
    for _ in range(successes):
        to_claim = select_passive_expansion_tiles(old_nation, all_tiles)
        if not to_claim:
            break
        expansion_events += 1
        for (q, r) in to_claim:
            tile = tile_map.get((q, r))
            if tile is None:
                continue
            # Deferred (not an immediate write) so a tick that fails later —
            # e.g. at the final commit transaction — doesn't leave these
            # claims permanently on the map despite the tick reporting a
            # full rollback. See _queue_tile_write's docstring: this closes
            # the same class of gap already fixed there for district/city
            # claims.
            _queue_tile_write(pending_tiles, tile["_id"], {"owner": nation_name})
            claimed.append((q, r))
            # Update the in-memory tile so subsequent rounds see the new ownership
            tile["owner"] = nation_name

    if claimed:
        # Resync territory_types from the in-memory tile ownership above —
        # the actual DB write is only queued, not yet applied, so an
        # aggregate query against hex_map_tiles here would still see the
        # pre-claim owners.
        counts = {}
        for tile in tile_map.values():
            if tile.get("owner") == nation_name and tile.get("terrain"):
                counts[tile["terrain"]] = counts.get(tile["terrain"], 0) + 1
        new_nation["territory_types"] = counts
        result += f"{nation_name} expanded into {len(claimed)} tile(s).\n"
        windfall_rolls = old_nation.get("resource_windfall_on_expansion", 0) * expansion_events
        result += _grant_resource_windfall(old_nation, new_nation, windfall_rolls, "expanded territory")
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()

    return result

def nation_job_cleanup_tick(old_nation, new_nation, schema):
    # Vampire/undead counts migrated to per-pop disease infections; the only
    # remaining static job set by other systems is revolutionary.
    new_jobs = {}
    for job in old_nation.get("jobs", {}).keys():
        if job != "revolutionary":
            new_jobs[job] = 0
    new_nation["jobs"] = new_jobs
    return ""

def pop_loss_tick(old_nation, new_nation, schema):
    result = ""
    if old_nation.get("pop_loss_chance", 0) <= 0:
        return ""
    pop_loss_roll = random.random()
    new_nation["pop_loss_roll"] = pop_loss_roll
    new_nation["pop_loss_chance_at_tick"] = old_nation.get("pop_loss_chance", 0)
    if pop_loss_roll <= old_nation.get("pop_loss_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has lost a pop.\n"
    return result

def nation_disease_spread_tick(old_nation, new_nation, schema):
    """Per-nation disease spread + stage escalation.

    For each disease among the nation's pops (a discovered cure does NOT stop
    this — see nation_disease_natural_cure_tick for what "cured" actually
    does): roll the infectivity chance (base + per-infected). On success, the
    new infection lands via the same 50/50 internal-vs-external targeting an
    accepted nation's voluntary spread uses (see attempt_dual_spread /
    nation_accepted_spread_tick): either another pop of this same nation, or
    a pop of a nation within 5 hexes of the border (trade/road-connected
    nations twice as likely). Capped at the infectivity's max share of pops.
    Then checks for stage escalation — newly reached stages can trigger an
    automatic civil war that splits the infected pops into a breakaway nation.
    """
    from helpers.disease_helpers import (
        get_nation_infection_counts, resolve_diseases, get_infectivity_settings,
        active_stage_index, get_stage, infect_random_pops, execute_disease_civil_war,
        attempt_dual_spread,
    )

    nation_id = str(old_nation.get("_id", ""))
    nation_name = old_nation.get("name", "Unknown")
    counts = get_nation_infection_counts(nation_id)
    if not counts:
        return ""

    result = ""
    diseases = resolve_diseases(counts.keys())
    pop_count = old_nation.get("pop_count", 0)
    spread_rolls = {}
    disease_stages = dict(old_nation.get("disease_stages", {}) or {})

    from helpers.disease_helpers import nation_accepts_disease
    for disease_id, infected in counts.items():
        disease = diseases.get(disease_id)
        # Note: a discovered cure ("cured") does NOT stop outbreak/spread — it
        # only doubles natural_cure_chance (nation_disease_natural_cure_tick).
        if not disease:
            continue
        # Accepted nations (primary race = the disease's derived race) are not
        # outbreak sites — conversions there are voluntary, via the accepted
        # spread tick, never the disease mechanics.
        if nation_accepts_disease(old_nation, disease):
            continue
        disease_name = disease.get("name", disease_id)

        prev_stage = disease_stages.get(disease_id, -1)
        cur_stage_idx = active_stage_index(disease, infected, pop_count)
        cur_stage = get_stage(disease, cur_stage_idx)

        # ── Spread roll ────────────────────────────────────────────────────
        settings = get_infectivity_settings(disease, old_nation)
        cap = math.floor(settings.get("max_infected_pct", 0) * pop_count)
        halted = bool(cur_stage and cur_stage.get("halts_spread"))
        if not halted and infected < cap and infected < pop_count:
            chance = min(
                settings.get("base_chance", 0)
                + settings.get("chance_per_infected", 0) * infected,
                1.0,
            )
            roll = random.random()
            spread_rolls[disease_name] = {"roll": roll, "chance_at_tick": chance}
            if roll <= chance:
                def _internal():
                    nonlocal infected
                    n = infect_random_pops(nation_id, disease, 1)
                    if n:
                        infected += n
                        return True
                    return False

                succeeded, target_type, ext_target = attempt_dual_spread(old_nation, disease, _internal)
                if succeeded and target_type == "internal":
                    result += f"{disease_name} has spread to another pop in {nation_name} ({infected}/{pop_count} infected).\n"
                    cur_stage_idx = active_stage_index(disease, infected, pop_count)
                elif succeeded:
                    result += (
                        f"{disease_name} has spread from {nation_name} to "
                        f"{ext_target.get('name', 'a nearby nation')}.\n"
                    )
                else:
                    result += f"{disease_name} found no new host near {nation_name}.\n"

        # ── Stage escalation (one-shot on entry) ───────────────────────────
        if cur_stage_idx > prev_stage:
            disease_stages[disease_id] = cur_stage_idx
            stages = disease.get("stages") or []
            for idx in range(prev_stage + 1, cur_stage_idx + 1):
                stage = stages[idx] if idx < len(stages) and isinstance(stages[idx], dict) else None
                if not stage:
                    continue
                stage_label = stage.get("stage_name") or f"stage {idx + 1}"
                result += f"{disease_name} in {nation_name} has escalated to {stage_label}.\n"
                if stage.get("trigger_civil_war"):
                    if infected >= pop_count:
                        # No healthy pops to leave behind — nothing to split from.
                        # The nation has fully succumbed; it IS the disease nation
                        # now. For race-changing diseases that means ACCEPTANCE:
                        # primary race becomes the derived race, pops keep their
                        # derived races and stop being "sick".
                        result += (
                            f"{nation_name} has fully succumbed to {disease_name} — "
                            f"no civil war, the nation itself is now theirs.\n"
                        )
                        if disease.get("changes_race") and disease.get("race_prefix"):
                            from helpers.disease_helpers import get_or_create_derived_race
                            base_race = None
                            try:
                                base_race = mongo.db.races.find_one(
                                    {"_id": ObjectId(str(old_nation.get("primary_race", "")))})
                            except Exception:
                                pass
                            derived_id = get_or_create_derived_race(
                                base_race, disease.get("race_prefix", ""),
                                disease.get("race_positive_trait", ""),
                                disease.get("race_negative_trait", ""))
                            if derived_id:
                                new_nation["primary_race"] = derived_id
                            mongo.db.pops.update_many(
                                {"nation": nation_id, "disease": disease_id},
                                {"$unset": {"disease": "", "pre_disease_race": ""}})
                            disease_stages.pop(disease_id, None)
                            result += (
                                f"{nation_name} has accepted {disease_name}: its people "
                                f"now embrace their new nature.\n"
                            )
                        continue
                    new_name, moved = execute_disease_civil_war(old_nation, disease, infected)
                    if new_name:
                        new_nation["stability"] = "Unsettled"
                        result += (
                            f"CIVIL WAR: the {disease_name} infected of {nation_name} have split off, "
                            f"forming {new_name} with {moved} pop(s).\n"
                        )
                        # The infected pops are gone from this nation — reset
                        # the stage bookkeeping so a fresh outbreak re-escalates.
                        disease_stages.pop(disease_id, None)
                    else:
                        result += f"{disease_name} civil war in {nation_name} failed to form a breakaway nation.\n"
                    break

    new_nation["disease_spread_rolls"] = spread_rolls
    new_nation["disease_stages"] = disease_stages
    return result

def nation_disease_natural_cure_tick(old_nation, new_nation, schema):
    """Give every infected pop of this nation an individual per-tick chance of
    natural recovery, independent of shared-quest cure research.

    A pop carrying multiple diseases rolls separately against EACH one's own
    natural_cure_chance — it can recover from more than one in the same tick.
    Once a disease's cure has been discovered (cured=True — set by
    disease_cure_cross_tick when cure_progress reaches the difficulty's
    required amount), that chance permanently doubles for every infected pop,
    everywhere. Discovering the cure does not itself heal anyone or stop
    spread — it only makes natural recovery twice as likely going forward.
    """
    from helpers.disease_helpers import resolve_diseases, cure_pop

    nation_id = str(old_nation.get("_id", ""))
    nation_name = old_nation.get("name", "Unknown")
    try:
        infected_pops = list(mongo.db.pops.find(
            {"nation": nation_id, "diseases": {"$exists": True, "$ne": []}}))
    except Exception:
        return ""
    if not infected_pops:
        return ""

    all_disease_ids = set()
    for p in infected_pops:
        all_disease_ids.update(str(d) for d in (p.get("diseases") or []) if d)
    diseases = resolve_diseases(all_disease_ids)

    recovered_counts = {}
    for pop in infected_pops:
        # Mutable per-pop snapshot, updated as cures land this tick so a
        # pop's later rolls (and cure_pop's own "is this the last disease"
        # check) see accurate, not-yet-stale state.
        current_diseases = list(pop.get("diseases") or [])
        for disease_id in list(current_diseases):
            disease = diseases.get(str(disease_id))
            if not disease:
                continue
            base_chance = disease.get("natural_cure_chance", 0) or 0
            if base_chance <= 0:
                continue
            chance = min(base_chance * (2 if disease.get("cured") else 1), 1.0)
            if random.random() <= chance:
                cure_pop({**pop, "diseases": current_diseases}, disease_id)
                current_diseases.remove(disease_id)
                name = disease.get("name", "")
                recovered_counts[name] = recovered_counts.get(name, 0) + 1

    result = ""
    for name, count in recovered_counts.items():
        result += f"{count} pop(s) in {nation_name} naturally recovered from {name}.\n"
    return result

def nation_disease_effects_tick(old_nation, new_nation, schema):
    """Per-nation, per-session disease effects that aren't part of the
    virtual job overlay (collect_disease_effects) because they either need
    to re-roll every session (the resource windfall — job_production is a
    fixed value recomputed continuously, not something that changes
    session to session) or reach into a different data type (vassal
    compliance, on the nation itself, but gated by a game-mechanic
    threshold rather than a plain per-pop modifier).

    For each disease currently infecting this nation:
      - `random_resource_windfall_per_infected_pop`: grants that many rolls
        of a random non-research resource PER currently-infected pop, via
        the same mechanic as the tech-research/expansion/stability-loss
        windfalls (_grant_resource_windfall).
      - `compliance_loss_at_max_infection`: if this nation is a vassal
        (has an overlord) and is currently at or above this disease's
        effective (region-aware) max_infected_pct cap, it loses one level
        of vassal compliance — reuses adjust_compliance, the same helper
        Nation Vassal Compliance Tick uses, so it floor-clamps at
        "Rebellious" the same way.
    """
    from helpers.disease_helpers import get_nation_infection_counts, resolve_diseases, get_infectivity_settings

    nation_id = str(old_nation.get("_id", ""))
    counts = get_nation_infection_counts(nation_id)
    if not counts:
        return ""

    diseases = resolve_diseases(counts.keys())
    pop_count = old_nation.get("pop_count", 0)
    result = ""

    for disease_id, infected in counts.items():
        disease = diseases.get(disease_id)
        if not disease or infected <= 0:
            continue
        disease_name = disease.get("name", disease_id)

        windfall_rate = disease.get("random_resource_windfall_per_infected_pop", 0) or 0
        if windfall_rate:
            result += _grant_resource_windfall(
                old_nation, new_nation, windfall_rate * infected, disease_name
            )

        if disease.get("compliance_loss_at_max_infection") and old_nation.get("overlord"):
            settings = get_infectivity_settings(disease, old_nation)
            cap = math.floor(settings.get("max_infected_pct", 0) * pop_count)
            if infected >= cap and cap > 0:
                result += adjust_compliance(
                    old_nation, new_nation, schema, [-1], [f"{disease_name} (max infection)"]
                )

    return result

def nation_accepted_spread_tick(old_nation, new_nation, schema):
    """Voluntary spread from ACCEPTED nations (e.g. vampire nations).

    A nation whose primary race carries a race-changing disease's derived-race
    prefix has accepted it. Pops it assigns to the disease's accepted-spread
    jobs (e.g. full_vampire) each add the disease's accepted_spread_chance per
    tick. On success, 50/50:
      - internal: one of the nation's own pops is converted to the derived
        race (permanent, no disease state), or
      - external: a pop of a nation within 5 hexes of the border is INFECTED
        with the disease (trade-route / road-connected nations twice as
        likely to be hit).
    Falls through to the other target type when the chosen one has no
    valid victims.
    """
    from helpers.disease_helpers import (
        nation_accepts_disease, convert_random_own_pop, attempt_dual_spread,
    )

    try:
        # Note: a discovered cure does not stop accepted-nation spread either —
        # only nation_disease_natural_cure_tick reacts to it (doubled chance).
        diseases = list(mongo.db.diseases.find({
            "changes_race": True,
            "accepted_spread_jobs.0": {"$exists": True},
        }))
    except Exception:
        return ""
    if not diseases:
        return ""

    result = ""
    nation_name = old_nation.get("name", "Unknown")
    jobs_assigned = old_nation.get("jobs", {}) or {}

    for disease in diseases:
        if not nation_accepts_disease(old_nation, disease):
            continue
        disease_name = disease.get("name", "")
        chance_per = disease.get("accepted_spread_chance", 0) or 0
        spreaders = sum(jobs_assigned.get(j, 0) or 0
                        for j in disease.get("accepted_spread_jobs", []))
        if chance_per <= 0 or spreaders <= 0:
            continue

        chance = min(chance_per * spreaders, 1.0)
        roll = random.random()
        rolls = new_nation.setdefault("disease_spread_rolls", {})
        rolls[f"{disease_name} (accepted)"] = {"roll": roll, "chance_at_tick": chance}
        if roll > chance:
            continue

        def _internal():
            return convert_random_own_pop(old_nation, disease) is not None

        succeeded, target_type, ext_target = attempt_dual_spread(old_nation, disease, _internal)
        if succeeded and target_type == "internal":
            result += (
                f"A pop of {nation_name} has embraced {disease_name}, "
                f"joining the {disease.get('race_prefix', '')} majority.\n"
            )
        elif succeeded:
            result += (
                f"{disease_name} has spread from {nation_name} to "
                f"{ext_target.get('name', 'a nearby nation')}!\n"
            )
        else:
            result += (
                f"{disease_name} stirred in {nation_name} but found no one "
                f"left to claim.\n"
            )

    return result

def disease_cure_cross_tick(old_nations, new_nations, schema, pending=None):
    """Cross-nation tick: sum shared-quest contributions into each disease's
    cure progress. Reaching the difficulty's required progress sets a
    permanent "cure discovered" flag (cured=True) — it does NOT heal anyone
    or stop the disease; it just doubles natural_cure_chance for every
    infected pop from then on (see nation_disease_natural_cure_tick). Diseases
    already marked cured are skipped — there's nothing further this tick does
    for them."""
    from helpers.disease_helpers import get_global_infection_counts, get_global_accepted_count, get_difficulty_settings

    result = ""
    try:
        diseases = list(mongo.db.diseases.find({"cured": {"$ne": True}}))
    except Exception:
        return ""
    if not diseases:
        return ""

    global_counts = get_global_infection_counts()

    for disease in diseases:
        disease_id = str(disease["_id"])
        disease_name = disease.get("name", disease_id)
        difficulty = get_difficulty_settings(disease)
        required = difficulty.get("required_progress", 0)
        # Include "accepted" pops (permanently converted to the disease's
        # derived race, e.g. a vampire nation's own-born vampires) alongside
        # actively infected pops — both are hosts of the disease.
        total_infected = global_counts.get(disease_id, 0) + get_global_accepted_count(disease)

        # Sum every nation's shared-quest contribution for this disease.
        contribution = 0
        contributors = 0
        for nation in old_nations:
            for quest in nation.get("shared_quests", []) or []:
                if isinstance(quest, dict) and str(quest.get("disease", "")) == disease_id:
                    per_tick = quest.get("total_progress_per_tick", 0) or 0
                    if per_tick > 0:
                        contribution += per_tick
                        contributors += 1

        if contribution <= 0:
            continue

        if total_infected < difficulty.get("min_infected_pops", 0):
            result += (
                f"Cure research for {disease_name} is gated — {total_infected} infected pop(s), "
                f"needs {difficulty.get('min_infected_pops', 0)}.\n"
            )
            continue

        new_progress = min(disease.get("cure_progress", 0) + contribution, required)
        after_data = {**disease, "cure_progress": new_progress}
        completed = new_progress >= required
        if completed:
            after_data["cured"] = True

        _queue_change(
            pending,
            data_type="diseases",
            item_id=disease["_id"],
            change_type="Update",
            before_data=deepcopy(disease),
            after_data=after_data,
            reason=f"Disease cure tick: +{contribution} progress from {contributors} nation(s)",
        )

        result += f"{disease_name} cure progress: +{contribution} ({new_progress}/{required}).\n"

        if completed:
            result += (
                f"{disease_name}'s CURE HAS BEEN DISCOVERED — natural recovery is now "
                f"twice as likely for every infected pop, everywhere. The disease itself "
                f"is not removed and keeps spreading/producing as before.\n"
            )

    return result

def disease_job_death_tick(_old_nations, _new_nations, _schema, pending=None):
    """Cross-nation tick: roll job_death_chance for every pop currently
    infected with a disease that has one set (> 0). A pop that dies has a
    Remove change auto-requested and approved against it — mirroring the
    manual pop-deletion flow (routes/pops_routes.py's bulk delete) — rather
    than being deleted directly, so it shows up in change history like any
    other pop removal.

    A pop carrying 2+ death-chance diseases only gets queued for removal
    once per tick (already_queued), even though it's found again in a later
    disease's loop iteration — otherwise the same pop could be queued twice
    in one pass."""
    result = ""
    diseases = list(mongo.db.diseases.find({"job_death_chance": {"$gt": 0}}))
    if not diseases:
        return ""

    already_queued = set()

    for disease in diseases:
        base_death_chance = disease.get("job_death_chance", 0)
        disease_id = str(disease["_id"])
        disease_name = disease.get("name", disease_id)
        role_condition = disease.get("prosperity_role_condition") or "None"
        override_death_chance = disease.get("override_job_death_chance")
        infected_pops = list(mongo.db.pops.find({"diseases": disease_id}))

        # Batch-resolve infected pops' nations' prosperity_role once (not
        # per-pop) for the role-conditional override.
        nation_roles = {}
        if role_condition != "None" and override_death_chance is not None:
            nation_object_ids = []
            for p in infected_pops:
                try:
                    nation_object_ids.append(ObjectId(p.get("nation", "")))
                except Exception:
                    continue
            for n in mongo.db.nations.find(
                {"_id": {"$in": nation_object_ids}}, {"prosperity_role": 1}
            ):
                nation_roles[str(n["_id"])] = n.get("prosperity_role")

        died = 0
        died_by_nation_id = {}
        for pop in infected_pops:
            pop_id_str = str(pop["_id"])
            if pop_id_str in already_queued:
                continue
            death_chance = base_death_chance
            if role_condition != "None" and override_death_chance is not None:
                if nation_roles.get(str(pop.get("nation", ""))) == role_condition:
                    death_chance = override_death_chance
            if random.random() <= death_chance:
                before = {k: v for k, v in pop.items() if k != "_id"}
                _queue_change(
                    pending,
                    data_type="pops",
                    item_id=pop["_id"],
                    change_type="Remove",
                    before_data=before,
                    after_data={},
                    reason=f"Died from {disease_name} (job death chance)",
                )
                already_queued.add(pop_id_str)
                died += 1
                nation_id_str = str(pop.get("nation", ""))
                died_by_nation_id[nation_id_str] = died_by_nation_id.get(nation_id_str, 0) + 1

        if died:
            # Name which nation(s) actually lost pops — an aggregate count
            # with no nation attribution (the previous behavior) meant a
            # nation's pop loss was effectively invisible in the tick
            # summary, discoverable only by manually diffing pop counts.
            nation_names = {}
            nation_object_ids = []
            for nid_str in died_by_nation_id:
                try:
                    nation_object_ids.append(ObjectId(nid_str))
                except Exception:
                    continue
            if nation_object_ids:
                for n in mongo.db.nations.find({"_id": {"$in": nation_object_ids}}, {"name": 1}):
                    nation_names[str(n["_id"])] = n.get("name", "Unknown")
            breakdown = ", ".join(
                f"{nation_names.get(nid, 'Unknown Nation')}: {count}"
                for nid, count in died_by_nation_id.items()
            )
            result += f"{disease_name}: {died} pop(s) died from job death chance ({breakdown}).\n"

    return result

def _forced_flee_destination_for_region(region_id):
    """If the region has a "Forced Flee Destination" modifier, return the
    named destination nation doc ({_id, name}); otherwise None.

    Unlike the normal random-candidate pool, a forced destination bypasses
    the "Closed" citizenship_stance check entirely — it's a hard override,
    not a preference within the normal pool.
    """
    try:
        region = mongo.db.regions.find_one({"_id": ObjectId(region_id)}, {"modifiers": 1})
    except Exception:
        return None
    if not region:
        return None
    for mod in region.get("modifiers", []):
        if mod.get("modifier_type") == "forced_flee_destination":
            target_name = mod.get("target_value", "")
            if target_name:
                return mongo.db.nations.find_one({"name": target_name}, {"_id": 1, "name": 1, "temperament": 1})
    return None

def pop_flee_tick(old_nation, new_nation, schema, pending=None, flee_events=None):
    """Roll nation's pop_flee_chance once; on success one excess pop flees to
    a random non-Closed nation in the same region — unless the region has a
    Forced Flee Destination modifier, in which case it always goes there.

    When flee_events (a list) is given, a successful flee appends
    {"from_id": ..., "to_id": ..., "to_temperament": ...} to it — this is
    how tick()'s player_tick_summary attribution learns the DESTINATION
    nation's temperament, since fleeing INTO a player nation from an AI
    source is just as relevant to the player audience as fleeing OUT of one,
    but the outer loop only ever sees old_nation (the source) directly.
    """
    pop_count   = old_nation.get("pop_count", 0)
    eff_cap     = old_nation.get("effective_pop_capacity", 0)
    excess_pops = max(0, pop_count - eff_cap)
    if excess_pops <= 0:
        return ""

    flee_chance = old_nation.get("pop_flee_chance", 0.0)
    if flee_chance <= 0 or random.random() > flee_chance:
        return ""

    region_id = str(old_nation.get("region", ""))
    if not region_id:
        return ""

    destination = _forced_flee_destination_for_region(region_id)
    if destination and destination["_id"] == old_nation["_id"]:
        destination = None  # a nation can't force its own overcrowded pops to flee to itself

    if not destination:
        try:
            candidates = list(mongo.db.nations.find(
                {
                    "region": region_id,
                    "_id": {"$ne": old_nation["_id"]},
                    "citizenship_stance": {"$ne": "Closed"},
                },
                {"_id": 1, "name": 1, "temperament": 1},
            ))
        except Exception:
            return ""

        if not candidates:
            return ""

        destination = random.choice(candidates)

    nation_id_str = str(old_nation["_id"])
    try:
        # "nation" must be projected even though it's also the query filter —
        # system_approve_change's check_no_other_changes compares before_data
        # against the pop's live document field-by-field, and a field entirely
        # absent from before_data can never match the pop's actual current
        # nation. That made this change fail its own approval every time,
        # silently stranding it as "Pending" forever with no error anywhere.
        pops = list(mongo.db.pops.find(
            {"nation": nation_id_str},
            {"_id": 1, "race": 1, "culture": 1, "religion": 1, "nation": 1},
        ))
    except Exception:
        return ""

    if not pops:
        return ""

    fleeing_pop  = random.choice(pops)
    old_pop_data = {k: v for k, v in fleeing_pop.items() if k != "_id"}
    new_pop_data = dict(old_pop_data)
    new_pop_data["nation"] = str(destination["_id"])

    _queue_change(
        pending,
        data_type="pops",
        item_id=fleeing_pop["_id"],
        change_type="Update",
        before_data=old_pop_data,
        after_data=new_pop_data,
        reason=(
            f"Pop fled from {old_nation.get('name', 'Unknown')} "
            f"to {destination.get('name', 'Unknown')} due to overcrowding"
        ),
    )
    if flee_events is not None:
        flee_events.append({
            "from_id": str(old_nation["_id"]),
            "to_id": str(destination["_id"]),
            "to_temperament": destination.get("temperament"),
        })
    return (
        f"A pop fled from {old_nation.get('name', 'Unknown')} "
        f"to {destination.get('name', 'Unknown')} due to overcrowding.\n"
    )

def temperament_tick(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    result = ""
    sessions_since_temperament_change = old_nation.get("sessions_since_temperament_change", 1)
    chance_of_temperament_change = sessions_since_temperament_change * 0.25
    temperament_change_roll = random.random()
    new_nation["temperament_change_roll"] = temperament_change_roll
    new_nation["temperament_change_chance_at_tick"] = chance_of_temperament_change
    if temperament_change_roll <= chance_of_temperament_change:
        try:
            culture = mongo.db.cultures.find_one({"_id": ObjectId(old_nation.get("primary_culture", ""))})
        except:
            culture = None
        trait_1_modifier = {}
        trait_2_modifier = {}
        trait_3_modifier = {}
        if culture:
            trait_1 = culture.get("trait_one", "None")
            trait_2 = culture.get("trait_two", "None")
            trait_3 = culture.get("trait_three", "None")

            trait_1_modifier = cultural_trait_temperament_modifiers.get(trait_1, {})
            trait_2_modifier = cultural_trait_temperament_modifiers.get(trait_2, {})
            trait_3_modifier = cultural_trait_temperament_modifiers.get(trait_3, {})
        
        temperament_odds = base_temperament_odds.copy()
        for temperament in temperament_enum:
            temperament_odds[temperament] += trait_1_modifier.get(temperament, 0)
            temperament_odds[temperament] += trait_2_modifier.get(temperament, 0)
            temperament_odds[temperament] += trait_3_modifier.get(temperament, 0)
        
        temperament_roll = random.random()
        new_nation["temperament_roll"] = temperament_roll
        new_nation["temperament_odds"] = temperament_odds
        cumulative_odds = 0
        for temperament in temperament_enum:
            cumulative_odds += temperament_odds[temperament]
            if temperament_roll <= cumulative_odds:
                new_nation["temperament"] = temperament
                result += f"{old_nation.get('name', 'Unknown')} has changed their temperament to {temperament}.  It had been {sessions_since_temperament_change} sessions since their last temperament change\n"
                break

        new_nation["sessions_since_temperament_change"] = 1
    else:
        new_nation["sessions_since_temperament_change"] = sessions_since_temperament_change + 1
    
    return result

def nation_tech_cost_reduction_tick(old_nation, new_nation, schema):
    result = ""
    json_tech_data = json_data["tech"]
    
    techs = new_nation.get("technologies") or {}
    for tech, value in (techs.items() if isinstance(techs, dict) else []):
        _tech_def = json_tech_data.get(tech, {})
        base_cost = _tech_def.get("cost", 0)
        _cat_mod = (old_nation.get("technology_category_cost_modifiers", {}) or {}).get((_tech_def.get("type") or "").lower(), 0)
        current_cost = value.get("cost", base_cost + old_nation.get("technology_cost_modifier", 0) + _cat_mod)
        invested = value.get("invested", 0)
        min_cost = (base_cost + 1) // 2

        # Reduce cost by 1 if it's higher than base cost and at least 2 higher than invested
        if current_cost > base_cost and current_cost >= invested + 2 and current_cost > min_cost:
            value["cost"] = current_cost - 1
            result += f"{old_nation.get('name', 'Unknown')} has reduced the cost of {tech} from {current_cost} to {current_cost - 1}.\n"
        
        new_nation["technologies"][tech] = value
    
    return result

def reset_rolling_karma_to_zero(old_nation, new_nation, schema):
    if old_nation.get("technologies", {}).get("cultural_prophecy", {}).get("researched", False):
        new_nation["rolling_karma"] = 2
    else:
        new_nation["rolling_karma"] = 0
    return ""

def reset_all_temperaments(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    result = ""
    try :
        culture = mongo.db.cultures.find_one({"_id": ObjectId(old_nation.get("primary_culture", ""))})
    except:
        culture = None
    trait_1_modifier = {}
    trait_2_modifier = {}
    trait_3_modifier = {}
    if culture:
        trait_1 = culture.get("trait_one", "None")
        trait_2 = culture.get("trait_two", "None")
        trait_3 = culture.get("trait_three", "None")

        trait_1_modifier = cultural_trait_temperament_modifiers.get(trait_1, {})
        trait_2_modifier = cultural_trait_temperament_modifiers.get(trait_2, {})
        trait_3_modifier = cultural_trait_temperament_modifiers.get(trait_3, {})

    temperament_odds = base_temperament_odds.copy()
    for temperament in temperament_enum:
        temperament_odds[temperament] += trait_1_modifier.get(temperament, 0)
        temperament_odds[temperament] += trait_2_modifier.get(temperament, 0)
        temperament_odds[temperament] += trait_3_modifier.get(temperament, 0)
    
    temperament_roll = random.random()
    new_nation["temperament_roll"] = temperament_roll
    new_nation["temperament_odds"] = temperament_odds
    cumulative_odds = 0
    for temperament in temperament_enum:
        cumulative_odds += temperament_odds[temperament]
        if temperament_roll <= cumulative_odds:
            new_nation["temperament"] = temperament
            result += f"{old_nation.get('name', 'Unknown')} has changed their temperament to {temperament}.\n"
            break

    new_nation["sessions_since_temperament_change"] = 1
    return result

def empire_prestige_decay_tick(old_nation, new_nation, schema):
    if not old_nation.get("empire"):
        return ""
    current = old_nation.get("empire_prestige_decay", 0)
    new_nation["empire_prestige_decay"] = current + 1
    return ""


def district_duration_tick(old_nation, new_nation, schema):
    """Increment session counters for districts that have the district_duration modifier.

    For each district with def_key that has a district_duration modifier in its
    definition, finds or creates a nation-level modifier tracking sessions:
      {"field": "district_sessions_{def_key}", "value": N, "duration": -1,
       "source": "District: {display_name}"}
    If the nation no longer has the district, the counter modifier is removed.
    """
    from calculations.field_calculations import _resolve_def

    districts = new_nation.get("districts", [])
    modifier_types_data = json_data.get("modifier_types", {})
    modifiers = list(new_nation.get("modifiers", []))
    result = ""

    active_def_keys = {}
    for d in districts:
        if not isinstance(d, dict):
            continue
        dk = d.get("def_key", "")
        if not dk:
            continue
        dd = _resolve_def(d)
        if not dd:
            continue
        has_duration = any(
            modifier_types_data.get(m.get("modifier_type", ""), {}).get("is_district_duration")
            for m in dd.get("modifiers", [])
            if isinstance(m, dict)
        )
        if has_duration:
            active_def_keys[dk] = dd.get("display_name", dk)

    for dk, display_name in active_def_keys.items():
        field_key = f"district_sessions_{dk}"
        source = f"District: {display_name}"
        found = False
        for m in modifiers:
            if m.get("field") == field_key:
                m["value"] = m.get("value", 0) + 1
                # Backfill modifier_type/district_key on legacy entries so the
                # edit page's modifier dropdown can match them (previously blank).
                m.setdefault("modifier_type", "district_session_count")
                m.setdefault("district_key", dk)
                found = True
                result += f"{old_nation.get('name', '?')}: {display_name} session count -> {m['value']}\n"
                break
            elif m.get("modifier_type") == "district_duration":
                m_src = (m.get("source") or "").lower()
                if dk.lower() in m_src or display_name.lower() in m_src:
                    m["value"] = m.get("value", 0) + 1
                    found = True
                    result += f"{old_nation.get('name', '?')}: {display_name} session count -> {m['value']} (legacy)\n"
                    break
        if not found:
            modifiers.append({
                "field": field_key, "value": 1, "duration": -1, "source": source,
                "modifier_type": "district_session_count", "district_key": dk,
            })
            result += f"{old_nation.get('name', '?')}: {display_name} session count -> 1 (new)\n"

    stale = []
    for i, m in enumerate(modifiers):
        # "field" is legitimately absent (or explicitly None) on structured
        # modifiers that target via modifier_type instead — .get(..., "")
        # only covers "missing", not "present but None".
        f = m.get("field") or ""
        if f.startswith("district_sessions_") and f[len("district_sessions_"):] not in active_def_keys:
            stale.append(i)
    for i in reversed(stale):
        removed = modifiers.pop(i)
        result += f"{old_nation.get('name', '?')}: removed stale counter for {removed.get('source', '?')}\n"

    new_nation["modifiers"] = modifiers
    return result

###########################################################
# Era / Age Tick Functions
###########################################################

_RELATION_STEPS = ["Hostile", "Unfriendly", "Neutral", "Friendly", "Allied"]
_COMPLIANCE_STEPS = ["Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]


def era_reset_stability_to_balanced_tick(old_nation, new_nation, schema):
    new_nation["stability"] = "Balanced"
    return ""


def era_compliance_decay_tick(old_nation, new_nation, schema):
    current = old_nation.get("compliance", "None")
    if current == "None" or current not in _COMPLIANCE_STEPS or current == "Neutral":
        return ""
    idx = _COMPLIANCE_STEPS.index(current)
    neutral_idx = _COMPLIANCE_STEPS.index("Neutral")
    new_nation["compliance"] = _COMPLIANCE_STEPS[idx + 1 if idx < neutral_idx else idx - 1]
    return ""


def era_resource_stockpile_decay_tick(old_nation, new_nation, schema):
    storage = old_nation.get("resource_storage") or {}
    if not storage:
        return ""
    base_kept = random.uniform(0.4, 0.6)
    stockpile_kept = old_nation.get("era_resource_stockpile_kept") or {}
    all_bonus = stockpile_kept.get("resource", 0)
    new_storage = {}
    for resource, amount in storage.items():
        if not isinstance(amount, (int, float)) or amount <= 0:
            new_storage[resource] = amount
            continue
        per_resource_bonus = stockpile_kept.get(resource, 0)
        kept_pct = min(base_kept + all_bonus + per_resource_bonus, 1.0)
        new_storage[resource] = round(amount * kept_pct)
    new_nation["resource_storage"] = new_storage
    kept_pct_display = round((base_kept + all_bonus) * 100)
    return f"{old_nation.get('name', 'Unknown')} kept ~{kept_pct_display}% of stockpile after era decay.\n"


def era_formal_storage_bonus_tick(old_nation, new_nation, schema):
    if not old_nation.get("technologies", {}).get("formal_storage", {}).get("researched", False):
        return ""

    general_resources = [r["key"] for r in json_data["general_resources"] if r["key"] not in ("research", "gunpowder")]
    unique_resources = [r["key"] for r in json_data["unique_resources"]]
    resource_pool = general_resources + unique_resources

    if not resource_pool:
        return ""

    gained = {}
    for _ in range(5):
        resource = random.choice(resource_pool)
        gained[resource] = gained.get(resource, 0) + 1

    storage = new_nation.get("resource_storage") or {}
    capacity = old_nation.get("nation_resource_capacity", {})
    for resource, amount in gained.items():
        current = storage.get(resource, 0)
        cap = capacity.get(resource, 0)
        storage[resource] = min(current + amount, cap) if cap else current + amount
    new_nation["resource_storage"] = storage

    gained_str = ", ".join(f"{v} {k}" for k, v in gained.items())
    return f"{old_nation.get('name', 'Unknown')} gained {gained_str} from Formal Storage.\n"


# ---------------------------------------------------------------------------
# Era AI resource grant
# ---------------------------------------------------------------------------

# Base grants calibrated to a ~15-pop nation (equivalent to a small player's district refunds).
# Scaled linearly by pop count at runtime: 5 pops ≈ 0.33×, 15 pops ≈ 1×, 30 pops ≈ 2×.
_ERA_AI_BASE_GRANTS = {
    "food":   9,
    "wood":   35,
    "stone":  33,
    "mounts": 1,
    "magic":  7,
    "iron":   3,
}
_ERA_AI_REFERENCE_POPS = 15  # pop count that yields the base amounts above


def _era_ai_terrain_weights(nation):
    """Return per-resource weight multipliers [0.5, 2.5] from terrain + node composition."""
    terrain_json = json_data.get("terrains", {})

    # Use effective territory types (what actually produces resources) rather than raw
    # territory_types, which can include disconnected tiles that generate nothing.
    # effective_territory_types is computed by calculate_all_fields and stored on the doc;
    # _calc_cache.effective_territory_types is the same value set during the tick run.
    cache = nation.get("_calc_cache", {}) or {}
    territory = (
        cache.get("effective_territory_types")
        or nation.get("effective_territory_types")
        or nation.get("territory_types")
        or {}
    )
    total_tiles = max(sum(territory.values()), 1)

    # Count tiles producing each resource using the terrain's own production rules
    tiles_by_resource = {}
    for terrain, count in territory.items():
        res = terrain_json.get(terrain, {}).get("resource", "none")
        if res != "none":
            tiles_by_resource[res] = tiles_by_resource.get(res, 0) + count

    # Multiplier: 0.5 (zero tiles) -> 2.0 when ≥20% of effective tiles produce this resource
    weights = {}
    for res in _ERA_AI_BASE_GRANTS:
        frac = tiles_by_resource.get(res, 0) / total_tiles
        weights[res] = 0.5 + 1.5 * min(1.0, frac * 5)

    # Node boosts — prefer _calc_cache counts (set by calculate_all_fields during tick),
    # fall back to the stored resource_nodes field on the document.
    nodes = nation.get("nodes", {}) or {}
    territory_nodes = (
        cache.get("territory_node_counts")
        or nation.get("resource_nodes")
        or {}
    )
    for res in _ERA_AI_BASE_GRANTS:
        total_nodes = nodes.get(res, 0) + territory_nodes.get(res, 0)
        if total_nodes > 0:
            weights[res] = min(2.5, weights[res] + 0.25 * total_nodes)

    return weights


def era_ai_resource_grant_tick(old_nation, new_nation, schema):
    """
    Grant AI nations era-transition resources equivalent to what player nations
    received as district refunds.  Amount scales linearly with pop count
    (calibrated so 15 pops ≈ a small player's refund total; 5-pop nations get ~0.33×,
    30-pop nations get ~2×).  Distribution is skewed by terrain composition and nodes.
    """
    if old_nation.get("temperament", "Player") == "Player":
        return ""

    pop_count = max(1, int(old_nation.get("pop_count", 0) or 0))
    scale = pop_count / _ERA_AI_REFERENCE_POPS

    terrain_weights = _era_ai_terrain_weights(old_nation)
    storage = {}  # reset stockpile to zero before granting era resources
    new_nation["resource_storage"] = storage
    capacity = old_nation.get("nation_resource_capacity", {})
    grants = {}

    for res, base_amt in _ERA_AI_BASE_GRANTS.items():
        mult = terrain_weights.get(res, 1.0)
        amount = max(0, round(base_amt * scale * mult * random.uniform(0.75, 1.25)))
        if amount == 0:
            continue
        current = storage.get(res, 0)
        cap = capacity.get(res, 0)
        new_val = min(current + amount, cap) if cap else current + amount
        actual = new_val - current
        if actual > 0:
            storage[res] = new_val
            grants[res] = actual

    new_nation["resource_storage"] = storage

    # Money: skewed toward stone/iron terrain (mines, mints)
    money_mult = (terrain_weights.get("stone", 1.0) + terrain_weights.get("iron", 1.0)) / 2.0
    money_grant = round(random.uniform(0, 1100) * scale * money_mult)
    if money_grant > 0:
        new_nation["money"] = new_nation.get("money", 0) + money_grant
        grants["money"] = money_grant

    if not grants:
        return ""
    summary = ", ".join(f"{k}+{v}" for k, v in sorted(grants.items()))
    return f"{old_nation.get('name', '?')}: era resource grant [{summary}]\n"


def era_relations_decay_tick(pending=None):
    neutral_idx = _RELATION_STEPS.index("Neutral")
    relations = list(mongo.db.diplo_relations.find())
    count = 0
    for relation in relations:
        current = relation.get("relation", "Neutral")
        if current == "Neutral" or current not in _RELATION_STEPS:
            continue
        idx = _RELATION_STEPS.index(current)
        new_val = _RELATION_STEPS[idx + 1 if idx < neutral_idx else idx - 1]
        _queue_change(
            pending,
            data_type="diplo_relations",
            item_id=relation["_id"],
            change_type="Update",
            before_data={"relation": current},
            after_data={"relation": new_val},
            reason="Era Tick: Relations Decay to Neutral",
        )
        count += 1
    return f"Decayed {count} relation(s) toward Neutral.\n"


def _era_pop_growth_tick_impl(skip_infertile=False):
    from helpers.admin_tool_helpers import grow_population
    from helpers.hex_map_helpers import get_nations_within_distance

    _, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))
    count = 0

    for nation in nations:
        if _in_stasis(nation):
            continue
        if skip_infertile:
            race_id = nation.get("primary_race")
            if race_id:
                try:
                    race = mongo.db.races.find_one(
                        {"_id": ObjectId(race_id)}, {"negative_trait": 1, "_id": 0}
                    )
                    if race and race.get("negative_trait") == "Infertile":
                        continue
                except Exception:
                    pass

        nearby_names = get_nations_within_distance(nation["name"], max_distance=10)
        foreign_nation = None
        if nearby_names:
            chosen_name = random.choice(nearby_names)
            foreign_nation = db.find_one({"name": chosen_name})

        grow_population(nation, foreign_nation)
        count += 1

    return count


def era_pop_growth_tick():
    count = _era_pop_growth_tick_impl(skip_infertile=False)
    return f"Era Pop Growth: grew {count} nation(s).\n"


def age_pop_growth_tick():
    count = _era_pop_growth_tick_impl(skip_infertile=True)
    return f"Age Pop Growth: grew {count} nation(s) (infertile races skipped).\n"


def era_artifact_loss_tick(pending=None):
    """Roll artifact loss chance 3 times per character; lose 1 artifact per successful roll."""
    character_schema, character_db = get_data_on_category("characters")
    _, artifact_db = get_data_on_category("artifacts")

    characters = list(character_db.find().sort("name", ASCENDING))
    losses_log = ""

    for character in characters:
        if character.get("health_status", "Healthy") == "Dead":
            continue
        if _in_stasis(character):
            continue

        character.update(calculate_all_fields(character, character_schema, "character"))
        artifact_loss_chance = character.get("artifact_loss_chance", 0)
        if artifact_loss_chance <= 0:
            continue

        losses = sum(1 for _ in range(3) if random.random() <= artifact_loss_chance)
        if losses <= 0:
            continue

        char_id_str = str(character["_id"])
        unequipped = list(artifact_db.find({"owner": char_id_str, "equipped": False}))
        equipped = list(artifact_db.find({"owner": char_id_str, "equipped": True}))
        available = unequipped + equipped  # prefer losing unequipped first

        for _ in range(losses):
            if not available:
                break
            pool = [a for a in available if not a.get("equipped", False)] or available
            old_artifact = random.choice(pool)
            new_artifact = deepcopy(old_artifact)
            new_artifact["owner"] = "Lost"
            available.remove(old_artifact)

            _queue_change(
                pending,
                data_type="artifacts",
                item_id=old_artifact["_id"],
                change_type="Update",
                before_data=old_artifact,
                after_data=new_artifact,
                reason=f"{old_artifact.get('name', 'Unknown')} lost by {character.get('name', 'Unknown')} during era artifact loss",
            )
            losses_log += f"  {character.get('name', 'Unknown')} lost {old_artifact.get('name', 'Unknown')}.\n"

    if losses_log:
        return f"Era Artifact Loss:\n{losses_log}"
    return "Era Artifact Loss: no artifacts lost.\n"


def era_character_aging_tick(pending=None):
    """Roll 5d4 once; age every living character by that many sessions.
    Any character whose age exceeds their elderly_age threshold by more than 2 dies."""
    character_schema, character_db = get_data_on_category("characters")

    age_increase = sum(random.randint(1, 4) for _ in range(5))
    result = f"Era Character Aging: all characters age by {age_increase} sessions.\n"

    characters = list(character_db.find().sort("name", ASCENDING))

    for character in characters:
        if character.get("health_status", "Healthy") == "Dead":
            continue
        if _in_stasis(character):
            continue

        character.update(calculate_all_fields(character, character_schema, "character"))
        new_character = deepcopy(character)

        new_age = character.get("age", 1) + age_increase
        new_character["age"] = new_age

        elderly_age = character.get("elderly_age", 3)
        if new_age > elderly_age + 2:
            new_character["health_status"] = "Dead"
            new_character["ruling_nation_org"] = None
            new_character["region"] = None
            new_character["player"] = None
            result += (
                f"{character.get('name', 'Unknown')} died of old age"
                f" (age {new_age}, elderly threshold {elderly_age}).\n"
            )

        _queue_change(
            pending,
            data_type="characters",
            item_id=character["_id"],
            change_type="Update",
            before_data=character,
            after_data=new_character,
            reason=f"Era Tick: aged {age_increase} session(s)",
            already_calculated=True,
        )

    return result


def region_renaissance_tick():
    """One-off regional flavor mechanic (see collect_flaming_ravager_modifiers in
    calculations/field_calculations.py): if the Frigid Caps region remains at
    Hopeful prosperity for 3 consecutive sessions, flag that Prosperity has
    ended there. No automatic mechanical lockout — the game master applies any
    narrative consequences (e.g. for Flaming Ravagers) manually when it fires.
    """
    from calculations.field_calculations import FLAMING_RAVAGER_REGION

    region = mongo.db.regions.find_one({"name": FLAMING_RAVAGER_REGION})
    if not region or region.get("renaissance_triggered"):
        return ""

    count = region.get("hopeful_session_count", 0) + 1 if region.get("prosperity") == "Hopeful" else 0
    updates = {"hopeful_session_count": count}
    result = ""
    if count >= 3:
        updates["renaissance_triggered"] = True
        result = (
            f"{FLAMING_RAVAGER_REGION} has remained at Hopeful prosperity for 3 consecutive "
            f"sessions — a Renaissance event has occurred! Prosperity has ended for this region. "
            f"(Apply any narrative consequences for Flaming Ravagers manually.)\n"
        )
    mongo.db.regions.update_one({"_id": region["_id"]}, {"$set": updates})
    return result


###########################################################
# Tick Function Constants
###########################################################

GENERAL_TICK_FUNCTIONS = {
    "Backup Database": backup_database,
    "Give Tick Summary": give_tick_summary,
    "Tick Session Number": tick_session_number,
    "Snapshot Hex Map": None,   # handled directly in tick() after session number is committed
    "Region Renaissance Check": region_renaissance_tick,
}

CHARACTER_TICK_FUNCTIONS = {
    "Character Heal and Death Tick": character_heal_then_death_tick,
    "Character Mana Tick": character_mana_tick,
    "Character Age Tick": character_age_tick,
    "Character Stat Gain Tick": character_stat_gain_tick,
    "Character Modifier Decay Tick": modifier_decay_tick,
    "Character Progress Quests Tick": progress_quests_tick,
    "Character Artifact Loss Tick": artifact_loss_tick,
}

ARTIFACT_TICK_FUNCTIONS = {
}

MERCHANT_TICK_FUNCTIONS = {
    "Merchant Income Tick": merchant_income_tick,
    "Merchant Progress Quests Tick": progress_quests_tick,
}

MERCENARY_TICK_FUNCTIONS = {
    "Mercenary Upkeep Tick": mercenary_upkeep_tick,
    "Mercenary Progress Quests Tick": progress_quests_tick,
}

FACTION_TICK_FUNCTIONS = {
    "Faction Income Tick": faction_income_tick,
    "Faction Progress Quests Tick": progress_quests_tick,
}

MARKET_TICK_FUNCTIONS = {
    "Market Income Tick": market_income_tick,
    "Bandit Camp Spawn Tick": bandit_camp_spawn_tick,
    "Complex Trade Bandit Loss Tick": complex_trade_bandit_loss_tick,
}

VASSAL_SPECIFIC_NATION_TICK_FUNCTIONS = [
    "Nation Concessions Tick",
    "Nation Rebellion Tick",
    "Nation Vassal Compliance Tick",
    "Nation Enclave Compliance Tick",
]

NATION_TICK_FUNCTIONS = {
    "AI Ensure Leader Tick": ai_ensure_leader_tick,
    "Nation Isolated Diplo Stance Tick": isolated_diplo_stance_tick,
    "Nation Income Tick": nation_income_tick,
    "Nation Tech Tick": nation_tech_tick,
    "Nation Update Rolling Karma Tick": update_rolling_karma,
    "Nation Infamy Decay Tick": nation_infamy_decay_tick,
    "Nation War Support Tick": nation_war_support_tick,
    "Nation Infamy Consequences Tick": nation_infamy_consequences_tick,
    "Nation Prestige Gain Tick": nation_prestige_gain_tick,
    "Nation Civil War Tick": nation_civil_war_tick,
    "Nation Stability Tick": nation_stability_tick,
    "Nation Concessions Tick": nation_concessions_tick,
    "Nation Rebellion Tick": nation_rebellion_tick,
    "Nation Vassal Compliance Tick": nation_vassal_compliance_tick,
    "Nation Enclave Compliance Tick": nation_enclave_compliance_tick,
    "Nation Passive Expansion Tick": nation_passive_expansion_tick,
    "Nation Modifier Decay Tick": modifier_decay_tick,
    "Nation Progress Quests Tick": progress_quests_tick,
    "Nation Job Cleanup Tick": nation_job_cleanup_tick,
    "AI Decision Tick": ai_decision_tick,
    "AI Mech RP Tick": ai_mech_rp_tick,
    "Nation Disease Spread Tick": nation_disease_spread_tick,
    "Nation Accepted Disease Spread Tick": nation_accepted_spread_tick,
    "Nation Disease Natural Cure Tick": nation_disease_natural_cure_tick,
    "Nation Disease Effects Tick": nation_disease_effects_tick,
    "Nation Pop Loss Tick": pop_loss_tick,
    "Nation Pop Flee Tick": pop_flee_tick,
    "Nation Temperament Tick": temperament_tick,
    "District Duration Tick": district_duration_tick,
    "Empire Prestige Decay Tick": empire_prestige_decay_tick,
}

def ongoing_trade_route_tick(_old_nations, _new_nations, _schema):
    """Lifecycle-only tick: ends routes that have passed their last delivery session."""
    current_session = _tr_current_session()
    log = run_trade_route_lifecycle(current_session)
    return (log + "\n") if log else ""


NATION_CROSS_TICK_FUNCTIONS = {
    "Ongoing Trade Route Tick": ongoing_trade_route_tick,
    "AI Market Matching Tick": ai_market_matching_tick,
    "Market Price Tick": market_price_tick,
    "Disease Cure Tick": disease_cure_cross_tick,
    "Disease Job Death Tick": disease_job_death_tick,
    "AI Vassal Concessions Payment Tick": ai_vassal_concessions_payment_tick,
}

def generate_all_ai_rulers_tick(pending=None):
    """Generate AI rulers for all nations and mercenary companies without a living ruler or direct players."""
    result = ""
    character_schema, _ = get_data_on_category("characters")

    living_ruler_org_ids = {
        str(c["ruling_nation_org"])
        for c in mongo.db.characters.find(
            {"ruling_nation_org": {"$ne": None}, "health_status": {"$ne": "Dead"}},
            {"ruling_nation_org": 1},
        )
        if c.get("ruling_nation_org")
    }

    for collection_name in ("nations", "mercenaries"):
        try:
            org_schema, org_db = get_data_on_category(collection_name)
        except Exception:
            continue
        for org in org_db.find():
            if _in_stasis(org):
                continue
            if str(org["_id"]) not in living_ruler_org_ids and not org.get("players"):
                result += generate_ai_character(org, org_schema, character_schema, pending=pending)

    return result


ERA_GENERAL_TICK_FUNCTIONS = {
    "Backup Database": None,   # handled directly in era_tick() before nation processing
    "Snapshot Hex Map": None,  # handled directly in era_tick() after nation processing
    "Era Give Tick Summary": None,  # handled directly in era_tick() after all processing
    "Era Relations Decay to Neutral": era_relations_decay_tick,
    "Era Pop Growth (All Nations)": era_pop_growth_tick,
    "Age Pop Growth (Skip Infertile Races)": age_pop_growth_tick,
    "Era Artifact Loss": era_artifact_loss_tick,
    "Era Character Aging": era_character_aging_tick,
    "Era Generate AI Rulers": generate_all_ai_rulers_tick,
}

ERA_NATION_TICK_FUNCTIONS = {
    "Era Nation Reset Rolling Karma to Zero": reset_rolling_karma_to_zero,
    "Era Nation Reset All Temperaments": reset_all_temperaments,
    "Era Reset Stability to Balanced": era_reset_stability_to_balanced_tick,
    "Era Compliance Decay to Neutral": era_compliance_decay_tick,
    "Era Resource Stockpile Decay": era_resource_stockpile_decay_tick,
    "Era Formal Storage Bonus": era_formal_storage_bonus_tick,
    "Era AI Resource Grant": era_ai_resource_grant_tick,
    "Age Nation Tech Cost Reduction Tick": nation_tech_cost_reduction_tick,
}


def era_character_magic_decay_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    magic_points = old_character.get("magic_points", 0)
    if not magic_points:
        return ""
    kept_pct = random.uniform(0.4, 0.6)
    new_character["magic_points"] = round(magic_points * kept_pct)
    kept_display = round(kept_pct * 100)
    return f"{old_character.get('name', 'Unknown')} kept ~{kept_display}% of magic stockpile ({magic_points} -> {new_character['magic_points']}).\n"


ERA_CHARACTER_TICK_FUNCTIONS = {
    "Era Character Magic Stockpile Decay": era_character_magic_decay_tick,
}


# Tick functions that queue a deferred change against a document OTHER than
# the one they're iterating (see _queue_change) — everything else is called
# by tick()/era_tick()'s dispatch loops exactly as before. Wrapped with
# functools.partial(fn, pending=pending) at dispatch time rather than adding
# `pending` to every tick function's signature, so this list is the only
# place that needs to be kept up to date when a new cross-cutting write is
# added.
_PENDING_AWARE_TICK_FUNCTIONS = {
    character_heal_then_death_tick,
    artifact_loss_tick,
    ai_ensure_leader_tick,
    pop_flee_tick,
    disease_cure_cross_tick,
    disease_job_death_tick,
    era_relations_decay_tick,
    era_artifact_loss_tick,
    era_character_aging_tick,
    generate_all_ai_rulers_tick,
}

# Separate registry, same reasoning, for functions that queue hex_map_tiles
# writes via _queue_tile_write (district/city claims) rather than
# system_request_change-tracked ones — see _queue_tile_write's docstring.
_TILE_PENDING_AWARE_TICK_FUNCTIONS = {
    ai_decision_tick,
    ai_market_matching_tick,
    nation_passive_expansion_tick,
    bandit_camp_spawn_tick,
    complex_trade_bandit_loss_tick,
}

# Separate registry, same reasoning, for functions that report a
# cross-nation event (currently just a pop fleeing FROM the nation being
# processed TO some other nation) into a shared `flee_events` list, so the
# caller can tell whether the DESTINATION nation (not just the one this
# dispatch call was made for) is player-relevant. See pop_flee_tick and
# tick()'s player_tick_summary attribution.
_FLEE_EVENT_AWARE_TICK_FUNCTIONS = {
    pop_flee_tick,
}


def _dispatch(tick_function, pending, *args, pending_tiles=None, flee_events=None):
    """Call a registered tick function, binding `pending`/`pending_tiles`/
    `flee_events` in only if that function is registered for them — every
    other tick function's call signature is completely unaffected."""
    kwargs = {}
    if tick_function in _PENDING_AWARE_TICK_FUNCTIONS:
        kwargs["pending"] = pending
    if tick_function in _TILE_PENDING_AWARE_TICK_FUNCTIONS:
        kwargs["pending_tiles"] = pending_tiles
    if tick_function in _FLEE_EVENT_AWARE_TICK_FUNCTIONS:
        kwargs["flee_events"] = flee_events
    if kwargs:
        return functools.partial(tick_function, **kwargs)(*args)
    return tick_function(*args)


def era_tick(form_data):
    full_tick_summary = ""

    if "run_Backup Database" in form_data:
        success, message = backup_database()
        if not success:
            return message

    # See tick()'s matching comment: nothing below touches the database
    # except plain reads until the single commit phase at the end.
    pending = []

    collect_nation_data = any(
        f"run_{label}" in form_data for label in ERA_NATION_TICK_FUNCTIONS
    )

    if collect_nation_data:
        nation_schema, nation_db = get_data_on_category("nations")
        old_nations = list(nation_db.find().sort("name", ASCENDING))
        new_nations = []
        for nation in old_nations:
            if nation:
                nation.update(calculate_all_fields(nation, nation_schema, "nation"))
                new_nations.append(deepcopy(nation))

        for label, fn in ERA_NATION_TICK_FUNCTIONS.items():
            if f"run_{label}" in form_data:
                print(label)
                for i in range(len(old_nations)):
                    if _in_stasis(old_nations[i]):
                        continue
                    result = _dispatch(fn, pending, old_nations[i], new_nations[i], nation_schema)
                    full_tick_summary += result

        for i in range(len(old_nations)):
            _queue_change(
                pending,
                data_type="nations",
                item_id=old_nations[i]["_id"],
                change_type="Update",
                before_data=old_nations[i],
                after_data=new_nations[i],
                reason="Era Tick Update for " + old_nations[i]["name"],
                already_calculated=True,
            )

    collect_character_data = any(
        f"run_{label}" in form_data for label in ERA_CHARACTER_TICK_FUNCTIONS
    )

    if collect_character_data:
        character_schema, character_db = get_data_on_category("characters")
        old_characters = list(character_db.find().sort("name", ASCENDING))
        new_characters = []
        for character in old_characters:
            if character:
                character.update(calculate_all_fields(character, character_schema, "character"))
                new_characters.append(deepcopy(character))

        for label, fn in ERA_CHARACTER_TICK_FUNCTIONS.items():
            if f"run_{label}" in form_data:
                print(label)
                for i in range(len(old_characters)):
                    if _in_stasis(old_characters[i]):
                        continue
                    result = _dispatch(fn, pending, old_characters[i], new_characters[i], character_schema)
                    full_tick_summary += result

        for i in range(len(old_characters)):
            _queue_change(
                pending,
                data_type="characters",
                item_id=old_characters[i]["_id"],
                change_type="Update",
                before_data=old_characters[i],
                after_data=new_characters[i],
                reason="Era Tick Update for " + old_characters[i].get("name", str(old_characters[i]["_id"])),
                already_calculated=True,
            )

    for label, fn in ERA_GENERAL_TICK_FUNCTIONS.items():
        if fn is None:
            continue  # handled as a special case above (e.g. Backup Database)
        if f"run_{label}" in form_data:
            print(label)
            full_tick_summary += _dispatch(fn, pending)

    # ── Commit phase — see tick()'s matching comment. ──────────────────────
    current_session = _commit_pending_changes(pending)

    if "run_Snapshot Hex Map" in form_data:
        from helpers.hex_map_helpers import snapshot_current_map
        snap_message = snapshot_current_map(current_session)
        full_tick_summary += f"\n\n{snap_message}"

    if "run_Era Give Tick Summary" in form_data:
        give_tick_summary(full_tick_summary, full_tick_summary)

    return full_tick_summary


def run_era_tick_async(form_data):
    from threading import Thread
    thread = Thread(target=_run_tick_guarded, args=(era_tick, form_data, "Era tick"))
    thread.daemon = True
    thread.start()
    return "Era tick started in background."


def adjust_stability(old_nation, new_nation, schema, amounts=[-1], reasons=[""]):
    result = ""
    stability_enum = schema["properties"]["stability"]["enum"]
    stability_index = stability_enum.index(old_nation.get("stability", "Balanced"))
    for i in range(min(len(amounts), len(reasons))):
        stability_index += amounts[i]
        gain_or_loss = "gained" if amounts[i] > 0 else "lost"
        result += f"{old_nation.get('name', 'Unknown')} has {gain_or_loss} {abs(amounts[i])} level(s) of stability due to {reasons[i]}.\n"
        if amounts[i] < 0:
            windfall_rolls = old_nation.get("resource_windfall_on_stability_loss", 0) * abs(amounts[i])
            result += _grant_resource_windfall(old_nation, new_nation, windfall_rolls, f"lost stability from {reasons[i]}")
    if stability_index < 0:
        civil_war_chance = 0.5
        civil_war_roll = random.random()
        worst_reason = reasons[amounts.index(min(amounts))]
        new_nation[worst_reason + "_civil_war_roll"] = civil_war_roll
        new_nation[worst_reason + "civil_war_chance_at_tick"] = civil_war_chance
        if civil_war_roll <= civil_war_chance:
            stability_index = 1
            result += f"{old_nation.get('name', 'Unknown')} has experienced a civil war due to negative stability from {worst_reason}.\n"
        else:
            stability_index = 0
    elif stability_index >= len(stability_enum):
        stability_index = len(stability_enum) - 1
    
    new_nation["stability"] = stability_enum[stability_index]
    return result


def adjust_compliance(old_nation, new_nation, schema, amounts=[-1], reasons=[""]):
    result = ""
    compliance_enum = schema["properties"]["compliance"]["enum"]
    floor_index = compliance_enum.index("Rebellious")
    ceiling_index = len(compliance_enum) - 1
    compliance_index = compliance_enum.index(old_nation.get("compliance", "None"))
    for i in range(min(len(amounts), len(reasons))):
        compliance_index += amounts[i]
        gain_or_loss = "gained" if amounts[i] > 0 else "lost"
        result += f"{old_nation.get('name', 'Unknown')} has {gain_or_loss} {abs(amounts[i])} level(s) of compliance due to {reasons[i]}.\n"
    compliance_index = max(floor_index, min(compliance_index, ceiling_index))

    new_nation["compliance"] = compliance_enum[compliance_index]
    return result
