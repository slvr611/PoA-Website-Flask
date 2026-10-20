import threading
import traceback
import uuid
from datetime import datetime, timezone
from time import perf_counter, sleep
from pymongo.errors import PyMongoError
from pymongo import ReturnDocument
from app_core import mongo, category_data, json_data, extract_discord_links
from flask import g, flash, current_app, has_request_context
from calculations.field_calculations import calculate_all_fields, purge_invalid_district_jobs
from copy import deepcopy
from bson import ObjectId

_NATURAL_KEY_FIELDS = ['name', 'quest_name', 'source', 'modifier_type', 'scope', 'attribute', 'resource_from', 'resource_to', 'field', 'key', 'unit_category', 'unit_stat', 'tier', 'tech_category', 'def_key', 'disease', 'stage_name']


def _handle_nation_rename(nation_id, old_name, new_name, session=None):
    """When a nation is renamed: update all hex_map_tiles owner and route.owner
    references, all trade_routes nation_a/nation_b references, and record the
    old name in previous_names so old links can redirect.

    `session` (optional): a pymongo ClientSession — when the caller is
    committing inside a transaction (e.g. a tick's deferred commit phase),
    pass it through so these writes participate in the same transaction and
    roll back together with everything else on failure."""
    if not old_name or not new_name or old_name == new_name:
        return
    mongo.db.hex_map_tiles.update_many(
        {"owner": old_name},
        {"$set": {"owner": new_name}},
        session=session,
    )
    mongo.db.hex_map_tiles.update_many(
        {"route.owner": old_name},
        {"$set": {"route.owner": new_name}},
        session=session,
    )
    mongo.db.trade_routes.update_many(
        {"nation_a": old_name},
        {"$set": {"nation_a": new_name}},
        session=session,
    )
    mongo.db.trade_routes.update_many(
        {"nation_b": old_name},
        {"$set": {"nation_b": new_name}},
        session=session,
    )
    # proposer records who requested the route; keep it consistent too.
    mongo.db.trade_routes.update_many(
        {"proposer": old_name},
        {"$set": {"proposer": new_name}},
        session=session,
    )
    mongo.db.nations.update_one(
        {"_id": nation_id},
        {"$addToSet": {"previous_names": old_name}},
        session=session,
    )
    from helpers.hex_map_helpers import bump_tile_version
    bump_tile_version()


def _handle_city_changes(old_cities, new_cities, session=None):
    """When a nation's cities are edited: sync any live hex_map_tiles.city
    snapshot (matched by city _id) with the new name/type, and clear tiles
    whose city was deleted from the nation.

    Only touches the live hex_map_tiles collection — hex_map_history snapshots
    from past sessions are separate, already-copied documents and are left
    untouched, so they keep whatever city data existed on that session
    (same guarantee _handle_nation_rename relies on for tile owners).

    `session`: see _handle_nation_rename."""
    old_by_id = {c.get("_id"): c for c in (old_cities or []) if isinstance(c, dict) and c.get("_id")}
    new_by_id = {c.get("_id"): c for c in (new_cities or []) if isinstance(c, dict) and c.get("_id")}
    if not old_by_id and not new_by_id:
        return

    changed = False
    for city_id, new_city in new_by_id.items():
        old_city = old_by_id.get(city_id)
        if old_city is None:
            continue  # newly created city — no tile references it yet
        new_name = new_city.get("name", "")
        new_type = new_city.get("type", "")
        if old_city.get("name", "") == new_name and old_city.get("type", "") == new_type:
            continue
        result = mongo.db.hex_map_tiles.update_many(
            {"city.id": city_id},
            {"$set": {"city": {"id": city_id, "name": new_name, "type": new_type}}},
            session=session,
        )
        if result.modified_count:
            changed = True

    for city_id in old_by_id:
        if city_id in new_by_id:
            continue
        result = mongo.db.hex_map_tiles.update_many(
            {"city.id": city_id},
            {"$set": {"city": None}},
            session=session,
        )
        if result.modified_count:
            changed = True

    if changed:
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()


def _get_natural_key(item):
    """Return the first non-empty value from a priority list of identifying fields."""
    for field in _NATURAL_KEY_FIELDS:
        val = item.get(field)
        if val:
            return str(val)
    return None


def _reconcile_item_ids(before_data, after_data):
    """Propagate stable _id values from before_data to matching after_data items.

    When after_data items lack a valid _id (e.g. because a form did not include
    the hidden _id input), this matches them to before_data items by natural key
    (quest_name, name, etc.) and copies the original _id.  This preserves
    identity across request/display so that the diff correctly shows only the
    fields that actually changed rather than treating every item as removed and
    re-added.

    Call this BEFORE _ensure_item_ids so that truly new items (no match in
    before) still get a fresh id from _ensure_item_ids.
    """
    if isinstance(before_data, dict) and isinstance(after_data, dict):
        for key in after_data:
            if key in before_data:
                _reconcile_item_ids(before_data[key], after_data[key])
    elif isinstance(before_data, list) and isinstance(after_data, list):
        # Note: deliberately not gated on _all_have_ids(before_data) — a single
        # id-less item (e.g. an unused district/city slot padding out the list)
        # would otherwise disable natural-key matching for every other item in
        # the list. Items without a usable _id or natural key are simply
        # skipped below rather than blocking the whole list.
        before_ids = {item['_id'] for item in before_data if isinstance(item, dict) and '_id' in item}

        # Index before items by natural key for O(1) lookup
        before_by_key = {}
        for item in before_data:
            if isinstance(item, dict):
                nk = _get_natural_key(item)
                if nk and nk not in before_by_key:
                    before_by_key[nk] = item

        # Track which before IDs have already been claimed by an after item
        claimed = set()
        for after_item in after_data:
            if isinstance(after_item, dict) and after_item.get('_id') in before_ids:
                claimed.add(after_item['_id'])

        for after_item in after_data:
            if not isinstance(after_item, dict):
                continue
            # Already has a valid before ID — nothing to do
            if after_item.get('_id') in before_ids:
                continue
            nk = _get_natural_key(after_item)
            if nk and nk in before_by_key:
                bid = before_by_key[nk].get('_id')
                if bid and bid not in claimed:
                    after_item['_id'] = bid
                    claimed.add(bid)


def _normalize_item_ids(data):
    """Recursively rename ``item_id`` → ``_id`` in submitted form data.

    WTForms cannot register field names that start with ``_``, so the stable
    sub-document identity field is exposed to forms as ``item_id``.  This
    function renames it back to ``_id`` before the data enters the change
    pipeline so that _reconcile_item_ids can match submitted items against
    the stored before_data by their original IDs.

    Only call this on data that came from a form submission (i.e. in
    ``request_change``), not on data generated internally (``system_request_change``).
    """
    if isinstance(data, dict):
        for value in data.values():
            _normalize_item_ids(value)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if 'item_id' in item:
                    raw_id = item.pop('item_id')
                    if raw_id:  # only promote non-empty values
                        item['_id'] = raw_id
                _normalize_item_ids(item)


def _ensure_item_ids(data):
    """Recursively assign a _id to any dict item inside a list that lacks one.

    Call this on ``after_data`` before computing diffs so that newly added
    sub-document items receive a stable identity at request time.
    Also replaces empty-string or None _id values (e.g. from unrendered form
    hidden fields) with fresh ids.
    """
    if isinstance(data, dict):
        for value in data.values():
            _ensure_item_ids(value)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if not item.get('_id'):
                    item['_id'] = uuid.uuid4().hex[:8]
                _ensure_item_ids(item)


def _all_have_ids(lst):
    """Return True if *every* item in ``lst`` is a dict with a non-empty ``_id``.

    Used to decide whether ID-based or positional list matching applies.
    Returns False for empty lists, lists with non-dict items, or lists where
    any dict is missing ``_id`` or has a falsy ``_id`` (None, '').
    """
    return bool(lst) and all(isinstance(item, dict) and item.get('_id') for item in lst)


def _ensure_starting_techs(nation):
    """Ensure the nation has all Starting-type techs researched."""
    tech_json = json_data.get("tech", {})
    technologies = nation.get("technologies")
    if not isinstance(technologies, dict):
        technologies = {}
        nation["technologies"] = technologies
    for tech_id, tech_def in tech_json.items():
        if tech_def.get("type") == "Starting":
            if tech_id not in technologies or not isinstance(technologies[tech_id], dict):
                technologies[tech_id] = {"researched": True, "investing": 0, "cost": 0}
            elif not technologies[tech_id].get("researched"):
                technologies[tech_id]["researched"] = True


def _update_tech_costs(nation):
    """Re-evaluate tech costs based on the nation's current cost modifier.

    Skips techs where cost_manually_set is True so player edits are preserved.
    Minimum cost is ceil(base_cost / 2), matching the floor used everywhere
    else this is checked (_validate_tech_costs in nation_routes.py,
    nation_tech_cost_reduction_tick in tick_helpers.py, forms.py).
    """
    tech_json = json_data.get("tech", {})
    cost_modifier = nation.get("technology_cost_modifier", 0)
    category_modifiers = nation.get("technology_category_cost_modifiers", {}) or {}
    technologies = nation.get("technologies")
    if not isinstance(technologies, dict):
        return
    for tech_id, tech_data in technologies.items():
        if not isinstance(tech_data, dict):
            continue
        if tech_data.get("cost_manually_set", False):
            continue
        tech_def = tech_json.get(tech_id, {})
        base_cost = tech_def.get("cost", 0)
        category = (tech_def.get("type") or "").lower()
        cat_modifier = category_modifiers.get(category, 0)
        min_cost = (base_cost + 1) // 2
        tech_data["cost"] = max(base_cost + cost_modifier + cat_modifier, min_cost)


def _calculate_and_attach_fields(data_type, target):
    schema = category_data[data_type]["schema"]
    target_data_type = category_data[data_type]["singularName"].lower()

    if data_type == "nations":
        _ensure_starting_techs(target)
        purge_invalid_district_jobs(target, json_data.get("jobs", {}))
        calculated_fields, breakdowns = calculate_all_fields(
            target,
            schema,
            target_data_type,
            return_breakdowns=True,
        )
        target.update(calculated_fields)
        target["breakdowns"] = breakdowns
        _update_tech_costs(target)
        from calculations.visibility import collect_visibility_modifiers
        target["visibility_modifiers"] = collect_visibility_modifiers(target)
    elif data_type in ("merchants", "mercenaries"):
        calculated_fields = calculate_all_fields(target, schema, target_data_type)
        target.update(calculated_fields)
        # Merchant/mercenary companies can carry their own offensive/
        # defensive visibility modifiers (via their `modifiers` array) —
        # same generic mechanism nations use — so compute_merchant_visibility/
        # compute_mercenary_visibility have something to read (see
        # calculations/visibility.py).
        from calculations.visibility import collect_visibility_modifiers
        target["visibility_modifiers"] = collect_visibility_modifiers(target)
    else:
        calculated_fields = calculate_all_fields(target, schema, target_data_type)
        target.update(calculated_fields)

    target.pop("_calc_cache", None)
    return target


def _strip_recalculable_fields(data_type, data):
    """Remove schema "calculated" fields plus the always-recomputed extras
    (breakdowns, visibility_modifiers, _pending_preview_cache) from a
    change-record snapshot before it's persisted.

    These are pure derived output of _calculate_and_attach_fields /
    calculate_all_fields — every insert, update, and revert recomputes them
    from scratch regardless of what a change record stored, so a snapshot
    that includes them is storing data that will never actually be read
    back. For nations in particular this is the dominant contributor to
    change-record size: a single "Tick Update" can otherwise carry a
    ~35KB breakdowns dict plus modifiers/technologies/etc. that changed
    only because they're recalculated every tick, not because they're
    part of the actual edit."""
    if not isinstance(data, dict):
        return data
    schema = category_data.get(data_type, {}).get("schema", {})
    schema_properties = schema.get("properties", {})
    cleaned = {
        k: v for k, v in data.items()
        if not (isinstance(schema_properties.get(k), dict) and schema_properties[k].get("calculated"))
    }
    for extra in ("breakdowns", "visibility_modifiers", "_pending_preview_cache"):
        cleaned.pop(extra, None)
    return cleaned


def request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"id": g.user.get("id", None)})["_id"]
    if requester is None:
        flash("You must be logged in to request changes.", "error")
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)

    # Defensive copy — before_data/after_data are frequently the caller's own
    # live in-memory objects (e.g. a tick's per-nation dict), not throwaway
    # snapshots. The .pop() calls below (and _reconcile_item_ids/
    # _ensure_item_ids further down) mutate whatever dict they're given, so
    # without this, popping "_id" here would silently strip it from the
    # caller's original object too. See generate_ai_character in
    # tick_helpers.py, which was bitten by exactly this before it started
    # deepcopy-ing before_data itself.
    before_data = deepcopy(before_data)
    after_data = deepcopy(after_data)

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None)

    # Snapshot automation-readiness fields from the FULL before/after data,
    # before keep_only_differences reduces them to only the changed keys —
    # a "Tick Update" that doesn't touch "name" would otherwise leave a
    # future reader (human or AI) with no display name to anchor the change
    # to without a live join. See the design doc's automation-readiness
    # section: these are read-only snapshots, not yet consumed by anything.
    target_display_name = after_data.get("name") or before_data.get("name")
    referenced_discord_links = extract_discord_links(reason)

    # Calculated fields (breakdowns, modifiers, technologies, etc.) get
    # recomputed from scratch on every approve/revert regardless of what's
    # stored here — see _strip_recalculable_fields's docstring. Stripping
    # them before the diff is computed keeps them out of both the stored
    # snapshot AND differential_data.
    before_data = _strip_recalculable_fields(data_type, before_data)
    after_data = _strip_recalculable_fields(data_type, after_data)

    # Rename item_id → _id in form-submitted data (WTForms cannot register
    # field names starting with '_', so the hidden field is named item_id).
    _normalize_item_ids(after_data)
    # Propagate stable IDs from before to after where items match by natural key,
    # then stamp fresh IDs on any remaining items that still lack one.
    _reconcile_item_ids(before_data, after_data)
    _ensure_item_ids(after_data)

    before_data, after_data = keep_only_differences(before_data, after_data, change_type)
    differential = calculate_int_changes(before_data, after_data)

    change_doc = {
        "target_collection": data_type,
        "target": item_id,
        "time_requested": now,
        "last_modified_time": now,
        "requester": requester,
        "change_type": change_type,
        "before_requested_data": before_data,
        "after_requested_data": after_data,
        "differential_data": differential,
        "request_reason": reason,
        "target_display_name": target_display_name,
        "referenced_discord_links": referenced_discord_links,
        "status": "Pending"
    }
    result = changes_collection.insert_one(change_doc)
    return result.inserted_id

def system_request_change(data_type, item_id, change_type, before_data, after_data, reason, session=None):
    """`session`: optional pymongo ClientSession — pass through when committing
    inside a transaction (see system_approve_change).

    `item_id` for an "Add" change: normally None (Mongo auto-generates the new
    document's _id at insert time). A caller can instead pass a pre-generated
    ObjectId here to know the new document's _id *before* it's actually
    inserted — needed when a deferred-commit batch queues a later action
    (e.g. reassigning an artifact) that references the new document's id
    before the insert itself has run. See generate_ai_character in
    tick_helpers.py for the motivating case."""
    requester = mongo.db.players.find_one({"name": "System"}, session=session)
    if requester is None:
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)

    # Defensive copy — see the matching comment in request_change(). Tick
    # functions in particular routinely pass their own live nation/character
    # dicts straight through as before_data.
    before_data = deepcopy(before_data)
    after_data = deepcopy(after_data)

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    if change_type == "Add" and item_id is not None:
        after_data["_id"] = item_id
    else:
        after_data.pop("_id", None)

    # See the matching comment in request_change().
    target_display_name = after_data.get("name") or before_data.get("name")
    referenced_discord_links = extract_discord_links(reason)

    # See the matching comment in request_change() / _strip_recalculable_fields.
    before_data = _strip_recalculable_fields(data_type, before_data)
    after_data = _strip_recalculable_fields(data_type, after_data)

    _reconcile_item_ids(before_data, after_data)
    _ensure_item_ids(after_data)

    before_data, after_data = keep_only_differences(before_data, after_data, change_type)
    differential = calculate_int_changes(before_data, after_data)

    change_doc = {
        "target_collection": data_type,
        "target": item_id,
        "time_requested": now,
        "last_modified_time": now,
        "requester": requester,
        "change_type": change_type,
        "before_requested_data": before_data,
        "after_requested_data": after_data,
        "differential_data": differential,
        "request_reason": reason,
        "target_display_name": target_display_name,
        "referenced_discord_links": referenced_discord_links,
        "status": "Pending"
    }
    result = changes_collection.insert_one(change_doc, session=session)
    return result.inserted_id


def _approve_hex_map_change(change, change_id, changes_collection, approver, now, session_number):
    """Write all buffered tile changes from a hex_map change request to the database."""
    after_tiles = change.get("after_requested_data", {}).get("tiles", {})
    affected_nations = set()
    affected_route_owners = set()
    for coord_key, tile_data in after_tiles.items():
        try:
            q, r = map(int, coord_key.split(","))
        except (ValueError, TypeError):
            continue
        existing = mongo.db.hex_map_tiles.find_one(
            {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}
        ) or {}
        update = {k: v for k, v in tile_data.items() if k != "_id"}
        update["q"] = q
        update["r"] = r
        if existing.get("_id"):
            mongo.db.hex_map_tiles.update_one({"_id": existing["_id"]}, {"$set": update})
            mongo.db.hex_map_tiles.delete_many({
                "q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]},
                "_id": {"$ne": existing["_id"]},
            })
        else:
            mongo.db.hex_map_tiles.insert_one(update)
        for n in [existing.get("owner"), tile_data.get("owner")]:
            if n:
                affected_nations.add(n)

        if "route" in update:
            for n in [(existing.get("route") or {}).get("owner"), (tile_data.get("route") or {}).get("owner")]:
                if n:
                    affected_route_owners.add(n)

    for nation_name in affected_nations:
        pipeline = [
            {"$match": {"owner": nation_name, "terrain": {"$exists": True, "$ne": None}}},
            {"$group": {"_id": "$terrain", "count": {"$sum": 1}}},
        ]
        counts = {doc["_id"]: doc["count"] for doc in mongo.db.hex_map_tiles.aggregate(pipeline)}
        mongo.db.nations.update_one({"name": nation_name}, {"$set": {"territory_types": counts}})

    for nation_name in affected_route_owners:
        from routes.hex_map_routes import _resync_nation_routes
        _resync_nation_routes(nation_name)

    if affected_nations or after_tiles:
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()

    changes_collection.update_one({"_id": change_id}, {"$set": {
        "status": "Approved",
        "time_implemented": now,
        "last_modified_time": now,
        "approver": approver["_id"],
        "session_number": session_number,
        "before_implemented_data": change.get("before_requested_data", {}),
        "after_implemented_data": change.get("after_requested_data", {}),
    }})
    return True


def _find_name_collision(target_collection, data_type, name, era=None, exclude_id=None, session=None):
    """Return the colliding document for a prospective `name` in this
    collection, or None if the name is free.

    Units are scoped by (name, era) — the same name is allowed to repeat
    across different eras, matching the check already applied at request
    time in routes/data_item_routes.py. `exclude_id`, when given, excludes
    the document being updated from the collision search (a rename that
    keeps the same name isn't a collision with itself).
    """
    if not name:
        return None
    query = {"name": name}
    if data_type == "units":
        query["era"] = era
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return target_collection.find_one(query, session=session)


def is_tick_locked():
    """True while a session/era tick is currently running.

    Reuses the existing tick_status heartbeat (helpers/tick_helpers.py's
    _tick_heartbeat_start/_tick_heartbeat_end, added for surfacing a stuck
    tick to admins) rather than a second, separate flag: _run_tick_guarded
    already sets tick_status.running True the moment the tick's background
    thread starts, and False the moment it exits — success or failure —
    also turning on the revert-warning banner at that same point (see
    _enable_revert_warning_after_tick).

    Requesting a change is never blocked by this; only approving one is,
    so a tick's own in-flight change queue never conflicts with itself.
    Always a fresh, uncached read: this gates a real action, unlike
    routes/base_routes.py's cached revert-warning/tick-lock banner checks,
    where a few seconds of staleness only affects display, not
    correctness."""
    doc = mongo.db.tick_status.find_one({"_id": "current"}, {"running": 1})
    return bool(doc and doc.get("running"))


# ---------------------------------------------------------------------------
# Partial approval/revert — unit addressing and split-on-apply
#
# A "unit" is addressed by the exact path keep_only_differences already
# produces: a top-level key ("money"), a dotted path into a nested dict
# field ("stockpiles.stone" — recursing exactly as far as
# keep_only_differences_dict already recurses when computing the diff in
# the first place), or (for a field whose value is an ID-keyed list on
# either side) an individual item within it ("districts[id=a1b2c3d4]"),
# including inside a nested dict. The review page exposes both whole-key
# and nested-dict-sub-key checkboxes (see templates/macros/pagination.html's
# render_changes_preview/render_field_change) but not yet a checkbox per
# list item — _list_change_unit_paths/_unit_selected already understand
# that finer per-item addressing too, so a future UI pass can expose it
# without touching any of this.
# ---------------------------------------------------------------------------

def _list_change_unit_paths(before_data, after_data, _prefix=""):
    """Return the ordered list of selectable unit path strings for a
    change's before/after data — see the module note above."""
    paths = []
    for key in after_data:
        after_val = after_data.get(key)
        before_val = before_data.get(key)
        path = f"{_prefix}{key}"

        if isinstance(after_val, dict):
            sub_before = before_val if isinstance(before_val, dict) else {}
            paths.extend(_list_change_unit_paths(sub_before, after_val, _prefix=f"{path}."))
            continue

        ids, seen = [], set()
        if isinstance(after_val, list) and _all_have_ids(after_val):
            for item in after_val:
                if isinstance(item, dict) and item.get("_id") not in seen:
                    ids.append(item["_id"])
                    seen.add(item["_id"])
        if isinstance(before_val, list) and _all_have_ids(before_val):
            for item in before_val:
                if isinstance(item, dict) and item.get("_id") not in seen:
                    ids.append(item["_id"])
                    seen.add(item["_id"])
        if ids:
            paths.extend(f"{path}[id={item_id}]" for item_id in ids)
        else:
            paths.append(path)
    return paths


def flatten_change_units(before_data, after_data, _prefix=""):
    """Like _list_change_unit_paths, but also carries each unit's before/
    after leaf value — [{"path", "before", "after"}, ...], recursing the
    same way (nested dict sub-keys, individual ID-keyed list items). Public
    (no leading underscore) since, unlike the other unit-addressing
    helpers, it's meant to be used outside this module — see
    helpers/ai_review_helpers.py, which needs each unit's actual value to
    judge it, not just its address.
    """
    units = []
    for key in after_data:
        after_val = after_data.get(key)
        before_val = before_data.get(key)
        path = f"{_prefix}{key}"

        if isinstance(after_val, dict):
            sub_before = before_val if isinstance(before_val, dict) else {}
            units.extend(flatten_change_units(sub_before, after_val, _prefix=f"{path}."))
            continue

        if isinstance(after_val, list) and _all_have_ids(after_val):
            before_items = before_val if isinstance(before_val, list) else []
            before_by_id = {i["_id"]: i for i in before_items if isinstance(i, dict) and "_id" in i}
            after_by_id = {i["_id"]: i for i in after_val if isinstance(i, dict) and "_id" in i}
            for item_id, item in after_by_id.items():
                units.append({
                    "path": f"{path}[id={item_id}]",
                    "before": before_by_id.get(item_id),
                    "after": item,
                })
            for item_id in sorted(set(before_by_id) - set(after_by_id), key=str):
                units.append({
                    "path": f"{path}[id={item_id}]",
                    "before": before_by_id[item_id],
                    "after": None,
                })
            continue

        units.append({"path": path, "before": before_val, "after": after_val})
    return units


def _unit_selected(path, selected_paths):
    """True if `path` (a whole-key path like "money", a dotted nested-dict
    path like "stockpiles.stone", or a list-item path like
    "districts[id=xxx]") is selected. Any SELECTED ANCESTOR of `path` also
    selects it — selecting "stockpiles" selects "stockpiles.stone" and
    "stockpiles.wood"; selecting "districts" selects every
    "districts[id=...]" — so one checkbox on a parent unit can stand in
    for every unit nested under it."""
    if path in selected_paths:
        return True
    base = path.split("[", 1)[0]   # strip a trailing "[id=...]" list-item suffix
    if base in selected_paths:
        return True
    segments = base.split(".")
    for i in range(1, len(segments)):
        if ".".join(segments[:i]) in selected_paths:
            return True
    return False


def _partition_units(before_data, after_data, selected_paths, _prefix=""):
    """Partition a change's before/after diff data into (selected_before,
    selected_after, remaining_before, remaining_after) at the granularity
    _list_change_unit_paths exposes. Pure data partition — does not look at
    any live document — so it's safe for deny_change (which never merges
    into a target at all) as well as being the first step
    _apply_selected_units builds on for approve_change/revert_change.

    `selected_paths` empty/None selects everything: returns
    (before_data, after_data, {}, {}), i.e. today's whole-change behavior,
    unchanged for every caller that doesn't pass a selection.
    """
    if not selected_paths:
        return dict(before_data), dict(after_data), {}, {}

    selected_before, selected_after = {}, {}
    remaining_before, remaining_after = {}, {}

    for key in after_data:
        after_val = after_data[key]
        before_val = before_data.get(key)
        path = f"{_prefix}{key}"

        if isinstance(after_val, dict):
            # Nested dict — recurse; each differing sub-key is its own unit
            # (see keep_only_differences_dict, which already only stores
            # the differing sub-keys here in the first place).
            sub_before = before_val if isinstance(before_val, dict) else {}
            s_before, s_after, r_before, r_after = _partition_units(
                sub_before, after_val, selected_paths, _prefix=f"{path}."
            )
            if s_before or s_after:
                selected_before[key] = s_before
                selected_after[key] = s_after
            if r_before or r_after:
                remaining_before[key] = r_before
                remaining_after[key] = r_after
        elif isinstance(after_val, list) and _all_have_ids(after_val):
            before_items = before_val if isinstance(before_val, list) else []
            before_by_id = {i["_id"]: i for i in before_items if isinstance(i, dict) and "_id" in i}
            after_by_id = {i["_id"]: i for i in after_val if isinstance(i, dict) and "_id" in i}
            removed_ids = set(before_by_id) - set(after_by_id)

            sel_after_items, sel_before_items = [], []
            rem_after_items, rem_before_items = [], []

            # Items present in the after list (added or updated).
            for item_id, item in after_by_id.items():
                item_path = f"{path}[id={item_id}]"
                after_bucket, before_bucket = (
                    (sel_after_items, sel_before_items) if _unit_selected(item_path, selected_paths)
                    else (rem_after_items, rem_before_items)
                )
                after_bucket.append(item)
                if item_id in before_by_id:
                    before_bucket.append(before_by_id[item_id])

            # Items removed by this change (in before, absent from after).
            for item_id in removed_ids:
                item_path = f"{path}[id={item_id}]"
                if _unit_selected(item_path, selected_paths):
                    sel_before_items.append(before_by_id[item_id])
                else:
                    rem_before_items.append(before_by_id[item_id])

            if sel_after_items or sel_before_items:
                selected_after[key] = sel_after_items
                selected_before[key] = sel_before_items
            if rem_after_items or rem_before_items:
                remaining_after[key] = rem_after_items
                remaining_before[key] = rem_before_items
        else:
            if _unit_selected(path, selected_paths):
                selected_after[key] = after_val
                if key in before_data:
                    selected_before[key] = before_data[key]
            else:
                remaining_after[key] = after_val
                if key in before_data:
                    remaining_before[key] = before_data[key]

    return selected_before, selected_after, remaining_before, remaining_after


def _undo_remaining_units(final, existing, remaining_before, remaining_after):
    """Recursively reset, inside `final`, every unit present in
    remaining_before/remaining_after back to its live (`existing`) value —
    the "undo" half of _apply_selected_units, mirroring _partition_units'
    own recursive structure (whole keys, nested dict sub-keys, and
    ID-keyed list items). Mutates `final` in place.

    Dict sub-keys have no "removal" case to undo, unlike list items: an
    Update diff's keep_only_differences_dict only ever adds/changes a
    nested dict's sub-keys, never represents one being deleted (a form
    always submits the dict's current full set of sub-fields) — see
    _list_change_unit_paths' module note.
    """
    def reset_list_items(key, items):
        existing_list = existing.get(key) if isinstance(existing.get(key), list) else []
        existing_by_id = {i["_id"]: i for i in existing_list if isinstance(i, dict) and "_id" in i}
        final_list = final.get(key) if isinstance(final.get(key), list) else []
        order = [i.get("_id") for i in final_list if isinstance(i, dict)]
        final_by_id = {i["_id"]: i for i in final_list if isinstance(i, dict) and "_id" in i}
        changed = False
        for item in items:
            item_id = item.get("_id") if isinstance(item, dict) else None
            if item_id in existing_by_id:
                final_by_id[item_id] = existing_by_id[item_id]
                if item_id not in order:
                    order.append(item_id)
                changed = True
            elif item_id in final_by_id:
                # Deferring a brand-new addition that never existed live —
                # drop it from the final result entirely.
                final_by_id.pop(item_id, None)
                order.remove(item_id)
                changed = True
        if changed:
            final[key] = [final_by_id[i] for i in order if i in final_by_id]

    for key, after_val in remaining_after.items():
        if isinstance(after_val, dict):
            existing_sub = existing.get(key) if isinstance(existing.get(key), dict) else {}
            final_sub = final.get(key)
            if not isinstance(final_sub, dict):
                final_sub = {}
            _undo_remaining_units(final_sub, existing_sub, remaining_before.get(key, {}), after_val)
            final[key] = final_sub
        elif isinstance(after_val, list):
            reset_list_items(key, after_val)
        else:
            if key in existing:
                final[key] = deepcopy(existing[key])
            else:
                final.pop(key, None)

    # Deferred pure list-item REMOVALS (an id in remaining_before with no
    # counterpart in remaining_after — i.e. the change deleted it, and
    # that deletion was deselected) aren't covered by the loop above,
    # since they never appear in remaining_after. Restore them into
    # `final` from `existing`.
    for key, before_items in remaining_before.items():
        if not isinstance(before_items, list):
            continue
        after_val = remaining_after.get(key)
        after_ids_for_key = {
            i.get("_id") for i in after_val if isinstance(i, dict)
        } if isinstance(after_val, list) else set()
        pure_removals = [
            i for i in before_items
            if isinstance(i, dict) and i.get("_id") is not None and i.get("_id") not in after_ids_for_key
        ]
        if pure_removals:
            reset_list_items(key, pure_removals)


def _apply_selected_units(existing, before_data, after_data, selected_paths):
    """Build the live target's new state with only the selected units
    applied, against `existing` (the live target, read fresh inside the
    caller's transaction).

    Strategy: first compute the result as if the WHOLE change were applied
    — deep_merge(existing, after_data), the exact call approve_change/
    revert_change always made — since that's the only way to correctly
    reconcile an ID-keyed list's full after-state against the live
    document (deep_merge replaces such a list wholesale, which is only
    correct when `after_data` carries the COMPLETE intended list — see
    deep_merge's own docstring about exactly that). Then "undo" each
    deselected (remaining) unit by resetting it back to its live value in
    a copy of that result (see _undo_remaining_units). This never calls
    deep_merge with a partial list, which would wholesale-replace and wipe
    every untouched item — the exact failure mode deep_merge's own
    comments already warn about for a different case.

    Returns (final_target, selected_before, selected_after,
    remaining_before, remaining_after). `selected_paths` empty/None
    selects everything — `final_target` comes back identical to plain
    deep_merge(existing, after_data), matching every caller's existing
    whole-change behavior.
    """
    selected_before, selected_after, remaining_before, remaining_after = _partition_units(
        before_data, after_data, selected_paths
    )
    final = deep_merge(existing, after_data)
    if not selected_paths:
        return final, selected_before, selected_after, remaining_before, remaining_after

    _undo_remaining_units(final, existing, remaining_before, remaining_after)

    return final, selected_before, selected_after, remaining_before, remaining_after


def _spin_off_remaining_change(change, remaining_before, remaining_after, session=None):
    """Insert a new Pending change covering exactly the units a partial
    approval deferred, inheriting the original's requester/time_requested/
    request_reason/automation-readiness snapshots so the deferred portion
    keeps its full context instead of looking like a fresh, unexplained
    request. Returns the new change's _id."""
    differential = calculate_int_changes(remaining_before, remaining_after)
    new_doc = {
        "target_collection": change["target_collection"],
        "target": change["target"],
        "time_requested": change.get("time_requested"),
        "last_modified_time": datetime.now(timezone.utc),
        "requester": change.get("requester"),
        "change_type": change["change_type"],
        "before_requested_data": remaining_before,
        "after_requested_data": remaining_after,
        "differential_data": differential,
        "request_reason": change.get("request_reason", ""),
        "target_display_name": change.get("target_display_name"),
        "referenced_discord_links": change.get("referenced_discord_links", []),
        "status": "Pending",
        "split_from": change["_id"],
    }
    result = mongo.db.changes.insert_one(new_doc, session=session)
    return result.inserted_id


class _ChangeApprovalRejected(Exception):
    """Raised inside an approve_change/revert_change transaction callback to
    abort cleanly with a specific user-facing reason (name collision, drift
    conflict, infamy gate, missing data, etc.). Deliberately NOT a
    PyMongoError, so session.with_transaction() aborts the transaction
    immediately without retrying it — the same exemption RuntimeError gets
    from tick_helpers.py's _commit_chunk_with_retry for a queued change
    that fails its own validation (retrying a non-transient rejection would
    just fail the same way every time). Caught once in approve_change/
    revert_change themselves, outside the transaction, to flash() the
    message exactly once regardless of how the transaction got there."""
    def __init__(self, message):
        self.message = message
        super().__init__(message)


# How many times to retry a whole approve/revert transaction attempt after
# session.with_transaction itself already gave up (it has its own ~120s
# internal retry budget for TransientTransactionError before raising) — and
# how long to wait between attempts. Mirrors tick_helpers.py's
# _CHUNK_COMMIT_MAX_ATTEMPTS/_CHUNK_COMMIT_RETRY_DELAY_SECONDS (the same
# resilience strategy proven out there for a real 2026-09-17 production
# transient-transaction incident), applied to the interactive admin
# approve/revert path, which had no transaction at all before this.
_TRANSACTION_MAX_ATTEMPTS = 3
_TRANSACTION_RETRY_DELAY_SECONDS = 10


def run_in_transaction_with_retry(fn, max_attempts=_TRANSACTION_MAX_ATTEMPTS,
                                   retry_delay_seconds=_TRANSACTION_RETRY_DELAY_SECONDS):
    """Run fn(session) inside a fresh session.with_transaction(), retrying
    the whole attempt (a brand new session + transaction each time) up to
    `max_attempts` times if it raises a PyMongoError. `_ChangeApprovalRejected`
    (or any other non-PyMongoError) always propagates immediately on the
    first attempt — see its docstring. Returns fn's return value on success.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            with mongo.cx.start_session() as session:
                return session.with_transaction(lambda s: fn(s))
        except PyMongoError as e:
            last_error = e
            if attempt >= max_attempts:
                raise
            print(
                f"Transaction attempt {attempt}/{max_attempts} failed with a "
                f"Mongo error, retrying in {retry_delay_seconds}s: {e}"
            )
            sleep(retry_delay_seconds)
    raise last_error


def approve_change(change_id, selected_paths=None):
    """`selected_paths`: optional iterable of unit paths (see
    _list_change_unit_paths) to approve now, leaving the rest Pending in a
    newly spun-off change — see _apply_selected_units. Empty/None approves
    the whole change at once, exactly as before this existed; only an
    Update change can be partially approved (Add/Remove are atomic by
    nature — there's no live document to partially reconcile against)."""
    approver = mongo.db.players.find_one({"id": g.user.get("id", None)})
    if approver is None or not approver.get("is_admin", False):
        flash("You must be an admin to approve changes.", "error")
        return None

    if is_tick_locked():
        flash("A tick is currently running — changes can be requested but not approved until it finishes.", "error")
        return None

    # The hex_map special case has its own entirely different shape (a
    # buffer of tile writes, not a before/after document merge) and was
    # never covered by check_no_other_changes to begin with — out of scope
    # for the transactional rewrite below; unchanged from before.
    change = mongo.db.changes.find_one({"_id": change_id})
    if change is not None and change.get("target_collection") == "hex_map":
        changes_collection = mongo.db.changes
        now = datetime.now(timezone.utc)
        global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
        session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0
        return _approve_hex_map_change(change, change_id, changes_collection, approver, now, session_number)

    selected_paths = set(selected_paths) if selected_paths else None
    try:
        return run_in_transaction_with_retry(
            lambda session: _approve_change_in_session(change_id, approver, session, selected_paths)
        )
    except _ChangeApprovalRejected as e:
        flash(e.message, "error")
        return False


def _approve_change_in_session(change_id, approver, session, selected_paths=None):
    """The actual body of approve_change, run inside a real transaction via
    run_in_transaction_with_retry — every read and write here participates
    in that transaction, so a failure anywhere (a collision found partway
    through, a Mongo error) rolls all of it back instead of leaving the
    target written but the change record still Pending (or vice versa),
    which is what could happen before this existed. Re-reads `change` and
    the target fresh here (rather than trusting whatever approve_change's
    caller already had) since this may run more than once if an earlier
    attempt hit a transient Mongo error.
    """
    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"}, session=session)
    session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0
    change = changes_collection.find_one({"_id": change_id}, session=session)
    if change is None:
        raise _ChangeApprovalRejected("Change not found.")
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]

        if change["target_collection"] == "market_links":
            member_id = after_data.get("member", "")
            if member_id:
                try:
                    member_nation = mongo.db.nations.find_one(
                        {"_id": ObjectId(member_id)}, {"infamy": 1}, session=session,
                    )
                except Exception:
                    member_nation = None
                if member_nation and member_nation.get("infamy", 0) >= 50:
                    raise _ChangeApprovalRejected("Cannot add nation to market: that nation has 50 or more infamy.")

        if change["target_collection"] == "trade_routes" and after_data.get("status") == "pending":
            # Trade routes proposed to/from an AI nation go through moderator
            # approval instead of the normal accept/reject flow, since the AI
            # side has no player to click "accept". Moderator approval IS the
            # AI's acceptance, so the route must go live immediately rather
            # than being inserted as "pending" (which no automated process
            # ever advances — it would silently never become active).
            after_data["status"] = "active"
            after_data["accepted_session"] = session_number

        collision = _find_name_collision(
            target_collection, change["target_collection"],
            after_data.get("name"), era=after_data.get("era"), session=session,
        )
        if collision:
            raise _ChangeApprovalRejected(f"Cannot approve: the name '{after_data.get('name')}' is already in use.")

        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)
        inserted_item_id = target_collection.insert_one(after_data, session=session).inserted_id
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "last_modified_time": now,
            "approver": approver["_id"],
            "session_number": session_number,
            "before_implemented_data": {},
            "after_implemented_data": _strip_recalculable_fields(change["target_collection"], after_data)
        }}, session=session)

        _enqueue_propagation(
            session,
            changed_data_type=change["target_collection"],
            changed_object_id=inserted_item_id,
            changed_object=after_data,
            reason=f"Dependency update from change #{change_id}",
        )

        return True
    else:
        target = target_collection.find_one({"_id": change["target"]}, session=session)
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        if not check_no_other_changes(before_data, after_data, target):
            raise _ChangeApprovalRejected("Change approval failed because the target has changed since the request was made.")

        merged = None
        split_into = None
        implemented_before, implemented_after = before_data, after_data
        if change["change_type"] == "Update":
            if change["target_collection"] == "mercenaries" and after_data.get("patron"):
                try:
                    patron_nation = mongo.db.nations.find_one(
                        {"_id": ObjectId(after_data["patron"])}, {"infamy": 1}, session=session,
                    )
                except Exception:
                    patron_nation = None
                if patron_nation and patron_nation.get("infamy", 0) >= 50:
                    raise _ChangeApprovalRejected("Cannot hire this mercenary company: the prospective patron nation has 50 or more infamy.")

            existing = target_collection.find_one({"_id": change["target"]}, session=session)
            merged, selected_before, selected_after, remaining_before, remaining_after = _apply_selected_units(
                existing, before_data, after_data, selected_paths
            )
            implemented_before, implemented_after = selected_before, selected_after

            new_name = merged.get("name")
            if new_name and new_name != existing.get("name"):
                collision = _find_name_collision(
                    target_collection, change["target_collection"], new_name,
                    era=merged.get("era"), exclude_id=change["target"], session=session,
                )
                if collision:
                    raise _ChangeApprovalRejected(f"Cannot approve: the name '{new_name}' is already in use.")

            merged = _calculate_and_attach_fields(change["target_collection"], merged)
            target_collection.update_one({"_id": change["target"]}, {"$set": merged}, session=session)

            if change["target_collection"] == "nations":
                _handle_nation_rename(change["target"], existing.get("name", ""), merged.get("name", ""), session=session)
                _handle_city_changes(existing.get("cities", []), merged.get("cities", []), session=session)

            if remaining_before or remaining_after:
                split_into = _spin_off_remaining_change(change, remaining_before, remaining_after, session=session)
        else:
            target_collection.delete_one({"_id": change["target"]}, session=session)

        update_fields = {
            "status": "Approved",
            "time_implemented": now,
            "last_modified_time": now,
            "approver": approver["_id"],
            "session_number": session_number,
            "before_implemented_data": implemented_before,
            "after_implemented_data": implemented_after,
        }
        if split_into is not None:
            update_fields["partial"] = True
            update_fields["split_into"] = split_into
        changes_collection.update_one({"_id": change_id}, {"$set": update_fields}, session=session)
        _enqueue_propagation(
            session,
            changed_data_type=change["target_collection"],
            changed_object_id=change["target"],
            changed_object=merged if change["change_type"] == "Update" else {},
            reason=f"Dependency update from change #{change_id}",
        )
        return True


def approve_change_as(change_id, approver, selected_paths=None, session=None):
    """Approve a change on behalf of a specific `approver` player document,
    with no g.user/is_admin dependency — for a caller that already knows
    WHO is approving without a Flask request (e.g.
    helpers/ai_review_helpers.py's ai_approve_change). Runs through the
    exact same transactional, partial-approval-aware path approve_change
    itself uses (_approve_change_in_session); `selected_paths` has the
    exact same meaning there — empty/None approves the whole change.

    `session`: optional existing transaction to participate in. When
    provided, the CALLER owns the transaction (including its own
    is_tick_locked() check, made once before opening it — this skips its
    own for exactly the same reason approve_change checks it only once,
    outside its transaction, not from within) and this call simply runs
    inside it; when omitted, this opens and retries its own transaction,
    exactly like approve_change.

    Returns (approved: bool, error: str|None) either way — never raises.
    """
    if session is None and is_tick_locked():
        return False, "tick_locked"
    try:
        if session is not None:
            return _approve_change_in_session(change_id, approver, session, selected_paths), None
        return run_in_transaction_with_retry(
            lambda s: _approve_change_in_session(change_id, approver, s, selected_paths)
        ), None
    except _ChangeApprovalRejected as e:
        return False, e.message


def system_approve_change(change_id, session=None, skip_recalculation=False, skip_propagate_ids=None):
    """`session`: optional pymongo ClientSession — pass through when
    committing inside a transaction (e.g. a tick's deferred commit phase).
    When set, every read/write this function (and everything it calls —
    propagate_updates, _handle_nation_rename, etc.) performs participates in
    the caller's transaction, so a failure anywhere rolls all of it back.

    `skip_propagate_ids`: forwarded to propagate_updates — see its docstring.

    `skip_recalculation`: skip the _calculate_and_attach_fields call (see
    below) entirely. Only safe when the caller already knows after_data (for
    Add) / the merged result (for Update) is fully calculated — e.g. a tick's
    queued change, where the data already went through calculate_all_fields
    once during the tick's compute phase, and skipping a second recompute
    here is a large, measured performance win (recalculating a nation from
    scratch was independently measured at ~8s; a character ~0.4s — the
    dominant cost in the commit phase by two orders of magnitude). Defaults
    to False (today's original behavior: always recalculate) for every
    caller that isn't explicitly opting in.

    Known gap: _calculate_and_attach_fields → calculate_all_fields does its
    own non-session-aware reads of other live documents (e.g. vassal tribute
    reads a vassal's pop_count/modifiers/resource_production). Inside a
    multi-entity transaction, those reads see the pre-transaction snapshot of
    other same-batch entities, not their already-queued-but-not-yet-committed
    values — a cross-referencing calculated field may be one tick stale
    rather than perfectly fresh within the same batch. This doesn't affect
    the all-or-nothing guarantee itself; deliberately accepted rather than
    threading `session` through the entire calculation engine."""
    approver = mongo.db.players.find_one({"name": "System"}, session=session)

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"}, session=session)
    session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0
    change = changes_collection.find_one({"_id": change_id}, session=session)
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]

        collision = _find_name_collision(
            target_collection, change["target_collection"],
            after_data.get("name"), era=after_data.get("era"),
            session=session,
        )
        if collision:
            print(f"system_approve_change blocked: name '{after_data.get('name')}' "
                  f"already exists in {change['target_collection']}.")
            return False

        if not skip_recalculation:
            after_data = _calculate_and_attach_fields(change["target_collection"], after_data)

        # If after_data already carries an _id (system_request_change stamped
        # one on from a caller-supplied item_id), insert_one respects it
        # instead of generating a new one — see system_request_change's
        # docstring for why a caller would want that.
        inserted_item_id = target_collection.insert_one(after_data, session=session).inserted_id
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "last_modified_time": now,
            "approver": approver["_id"],
            "session_number": session_number,
            "before_implemented_data": {},
            # after_data was just recalculated above — strip the derived
            # fields back out of the *stored* copy (the actual insert above
            # already happened with them intact; only the audit record
            # shouldn't carry data that recalculates on its own).
            "after_implemented_data": _strip_recalculable_fields(change["target_collection"], after_data)
        }}, session=session)

        _dispatch_propagate_updates(
            changed_data_type=change["target_collection"],
            changed_object_id=inserted_item_id,
            changed_object=after_data,
            reason=f"Dependency update from change #{change_id}",
            session=session,
            skip_ids=skip_propagate_ids,
        )
        return True
    else:
        target = target_collection.find_one({"_id": change["target"]}, session=session)
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        # skip_recalculation only ever comes from a tick's own deferred
        # commit phase (see its docstring above). Two of a tick's own queued
        # changes can legitimately target the same entity — e.g. a character
        # death queues a cross-cutting nation update (adds a stability-loss
        # modifier entry) AND the main per-nation loop separately queues
        # "Tick Update for X" for that same nation, each snapshotted
        # independently. Once the first commits, the second's before_data
        # (captured before either committed) no longer matches the live
        # document — check_no_other_changes correctly flags that divergence,
        # but here it's expected same-tick sequencing, not an outside edit.
        # The merge below already re-reads `existing` fresh and deep_merges
        # onto it, so it's safe to skip the gate rather than abort the batch.
        if skip_recalculation or check_no_other_changes(before_data, after_data, target):
            if change["change_type"] == "Update":
                existing = target_collection.find_one({"_id": change["target"]}, session=session)
                merged = deep_merge(existing, after_data)

                new_name = merged.get("name")
                if new_name and new_name != existing.get("name"):
                    collision = _find_name_collision(
                        target_collection, change["target_collection"], new_name,
                        era=merged.get("era"), exclude_id=change["target"],
                        session=session,
                    )
                    if collision:
                        print(f"system_approve_change blocked: name '{new_name}' "
                              f"already exists in {change['target_collection']}.")
                        return False

                if not skip_recalculation:
                    merged = _calculate_and_attach_fields(change["target_collection"], merged)
                target_collection.update_one({"_id": change["target"]}, {"$set": merged}, session=session)
                if change["target_collection"] == "nations":
                    _handle_nation_rename(change["target"], existing.get("name", ""), merged.get("name", ""), session=session)
                    _handle_city_changes(existing.get("cities", []), merged.get("cities", []), session=session)

                # A diff, not the whole document: only the keys this change
                # actually touches (after_data's keys, already stripped of
                # calculated fields at request time), sourced from `existing`
                # (the fresh pre-merge document) so revert_change's
                # deep_merge(existing, before_implemented_data) restores
                # exactly what was there right before this approval. Storing
                # the full `target` document here (breakdowns, modifiers,
                # technologies, everything) was the dominant contributor to
                # the changes collection's size — see archive_old_changes.py.
                before_snapshot = _strip_recalculable_fields(
                    change["target_collection"],
                    {k: existing.get(k) for k in after_data.keys()},
                )
            else:
                target_collection.delete_one({"_id": change["target"]}, session=session)
                # A delete has no "after" to diff against — the full
                # document is the only way to restore it on revert.
                before_snapshot = target

            changes_collection.update_one({"_id": change_id}, {"$set": {
                "status": "Approved",
                "time_implemented": now,
                "last_modified_time": now,
                "approver": approver["_id"],
                "session_number": session_number,
                "before_implemented_data": before_snapshot,
                "after_implemented_data": after_data
            }}, session=session)

            if change["change_type"] == "Update":
                _dispatch_propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object=merged,
                    reason=f"Dependency update from change #{change_id}",
                    session=session,
                    skip_ids=skip_propagate_ids,
                )
            else:
                _dispatch_propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object={},
                    reason=f"Dependency update from change #{change_id}",
                    session=session,
                    skip_ids=skip_propagate_ids,
                )
            return True
        print(f"system_approve_change blocked: change #{change_id} on "
              f"{change['target_collection']} #{change['target']} failed "
              f"check_no_other_changes (target was modified since the change "
              f"was requested, or before_requested_data doesn't reflect the "
              f"target's actual state).")
        print(_format_check_no_other_changes_failure_text(
            describe_check_no_other_changes_conflicts(before_data, after_data, target)
        ))
    return False


def force_approve_change(change_id, selected_paths=None):
    """Approve a change without running check_no_other_changes.

    Used when a change was stranded by subsequent ticks modifying the target,
    but the intended update is still correct and should be applied.

    `selected_paths`: see approve_change — same partial-approval support,
    Update changes only.
    """
    approver = mongo.db.players.find_one({"id": g.user.get("id", None)})
    if approver is None or not approver.get("is_admin", False):
        flash("You must be an admin to approve changes.", "error")
        return None

    if is_tick_locked():
        flash("A tick is currently running — changes can be requested but not approved until it finishes.", "error")
        return None

    selected_paths = set(selected_paths) if selected_paths else None
    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]
    after_data = change["after_requested_data"]
    before_data = change["before_requested_data"]
    implemented_before, implemented_after = before_data, after_data
    split_into = None

    if change["change_type"] == "Update":
        existing = target_collection.find_one({"_id": change["target"]})
        merged, selected_before, selected_after, remaining_before, remaining_after = _apply_selected_units(
            existing, before_data, after_data, selected_paths
        )
        implemented_before, implemented_after = selected_before, selected_after

        new_name = merged.get("name")
        if new_name and new_name != existing.get("name"):
            collision = _find_name_collision(
                target_collection, change["target_collection"], new_name,
                era=merged.get("era"), exclude_id=change["target"],
            )
            if collision:
                flash(f"Cannot approve: the name '{new_name}' is already in use.", "error")
                return False

        merged = _calculate_and_attach_fields(change["target_collection"], merged)
        target_collection.update_one({"_id": change["target"]}, {"$set": merged})
        if change["target_collection"] == "nations":
            _handle_nation_rename(change["target"], existing.get("name", ""), merged.get("name", ""))
            _handle_city_changes(existing.get("cities", []), merged.get("cities", []))

        if remaining_before or remaining_after:
            split_into = _spin_off_remaining_change(change, remaining_before, remaining_after)
    elif change["change_type"] == "Add":
        collision = _find_name_collision(
            target_collection, change["target_collection"],
            after_data.get("name"), era=after_data.get("era"),
        )
        if collision:
            flash(f"Cannot approve: the name '{after_data.get('name')}' is already in use.", "error")
            return False

        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)
        change["target"] = target_collection.insert_one(after_data).inserted_id
        implemented_after = after_data
    else:
        target_collection.delete_one({"_id": change["target"]})

    update_fields = {
        "status": "Approved",
        "time_implemented": now,
        "last_modified_time": now,
        "approver": approver["_id"],
        "session_number": session_number,
        "before_implemented_data": implemented_before,
        "after_implemented_data": _strip_recalculable_fields(change["target_collection"], implemented_after),
    }
    if split_into is not None:
        update_fields["partial"] = True
        update_fields["split_into"] = split_into
    changes_collection.update_one({"_id": change_id}, {"$set": update_fields})
    _dispatch_propagate_updates(
        changed_data_type=change["target_collection"],
        changed_object_id=change["target"],
        changed_object=merged if change["change_type"] == "Update" else after_data,
        reason=f"Force-approved change #{change_id}"
    )
    return True


def system_force_approve_change(change_id):
    """Force-approve a change using the System player as approver,
    without checking g.user or running check_no_other_changes."""
    approver = mongo.db.players.find_one({"name": "System"})
    if approver is None:
        return False

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0
    change = changes_collection.find_one({"_id": change_id})
    if not change or change.get("status") != "Pending":
        return False

    target_collection = category_data[change["target_collection"]]["database"]
    after_data = change["after_requested_data"]
    before_data = change["before_requested_data"]

    if change["change_type"] == "Update":
        existing = target_collection.find_one({"_id": change["target"]})
        merged = deep_merge(existing, after_data)

        new_name = merged.get("name")
        if new_name and new_name != existing.get("name"):
            collision = _find_name_collision(
                target_collection, change["target_collection"], new_name,
                era=merged.get("era"), exclude_id=change["target"],
            )
            if collision:
                print(f"system_force_approve_change blocked: name '{new_name}' "
                      f"already exists in {change['target_collection']}.")
                return False

        merged = _calculate_and_attach_fields(change["target_collection"], merged)
        target_collection.update_one({"_id": change["target"]}, {"$set": merged})
        if change["target_collection"] == "nations":
            _handle_nation_rename(change["target"], existing.get("name", ""), merged.get("name", ""))
            _handle_city_changes(existing.get("cities", []), merged.get("cities", []))
    elif change["change_type"] == "Add":
        collision = _find_name_collision(
            target_collection, change["target_collection"],
            after_data.get("name"), era=after_data.get("era"),
        )
        if collision:
            print(f"system_force_approve_change blocked: name '{after_data.get('name')}' "
                  f"already exists in {change['target_collection']}.")
            return False

        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)
        change["target"] = target_collection.insert_one(after_data).inserted_id
    else:
        target_collection.delete_one({"_id": change["target"]})

    changes_collection.update_one({"_id": change_id}, {"$set": {
        "status": "Approved",
        "time_implemented": now,
        "last_modified_time": now,
        "approver": approver["_id"],
        "session_number": session_number,
        "before_implemented_data": before_data,
        "after_implemented_data": _strip_recalculable_fields(change["target_collection"], after_data),
    }})
    _dispatch_propagate_updates(
        changed_data_type=change["target_collection"],
        changed_object_id=change["target"],
        changed_object=merged if change["change_type"] == "Update" else after_data,
        reason=f"System force-approved change #{change_id}"
    )
    return True


def deny_change(change_id, selected_paths=None):
    """`selected_paths`: optional unit paths to deny now, leaving the rest
    Pending in a newly spun-off change — the same split-on-apply pattern
    approve_change uses, minus any target write (deny never touches the
    target collection at all, only the change document's own status), so
    it only applies to Update changes (Add/Remove have no "units" of their
    own to split, matching approve_change's scoping). Empty/None denies
    the whole change at once, exactly as before this existed."""
    denier = mongo.db.players.find_one({"id": g.user["id"]})
    if denier is None or not denier.get("is_admin", False):
        return False

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    session_number = global_modifiers.get("session_counter", 0) if global_modifiers else 0

    selected_paths = set(selected_paths) if selected_paths else None
    update_fields = {
        "status": "Rejected",
        "time_rejected": now,
        "last_modified_time": now,
        "denier": denier["_id"],
        "session_number": session_number,
    }

    if selected_paths:
        change = changes_collection.find_one({"_id": change_id})
        if change is not None and change.get("change_type") == "Update":
            selected_before, selected_after, remaining_before, remaining_after = _partition_units(
                change.get("before_requested_data", {}), change.get("after_requested_data", {}), selected_paths,
            )
            if remaining_before or remaining_after:
                split_into = _spin_off_remaining_change(change, remaining_before, remaining_after)
                update_fields["partial"] = True
                update_fields["split_into"] = split_into
                update_fields["before_requested_data"] = selected_before
                update_fields["after_requested_data"] = selected_after

    changes_collection.update_one({"_id": change_id}, {"$set": update_fields})
    return True

def rescind_change(change_id):
    """Allow the original requester (or an admin) to withdraw a pending change."""
    if not g.user:
        flash("You must be logged in to rescind a change.", "error")
        return False
    player = mongo.db.players.find_one({"id": g.user.get("id")})
    if player is None:
        flash("Could not find your player record.", "error")
        return False

    changes_collection = mongo.db.changes
    change = changes_collection.find_one({"_id": change_id, "status": "Pending"})
    if change is None:
        flash("Change not found or is no longer pending.", "error")
        return False

    is_admin = player.get("is_admin", False)
    is_requester = change.get("requester") == player["_id"]
    if not is_admin and not is_requester:
        flash("You can only rescind your own changes.", "error")
        return False

    now = datetime.now(timezone.utc)
    changes_collection.update_one({"_id": change_id}, {"$set": {
        "status": "Rescinded",
        "time_rescinded": now,
        "last_modified_time": now,
        "rescinder": player["_id"],
    }})
    return True


def revert_change(change_id, selected_paths=None):
    """Restore a target object to its state before an approved change was
    applied.

    `selected_paths`: optional unit paths to revert now, leaving the rest
    at their currently-approved value — only an Update revert can be
    partial (Add/Remove revert an entire document and have no "units" of
    their own to split). Unlike partial approval/deny, there is no
    follow-up Pending change to spin off here: the deselected units simply
    stay applied, and this change's status becomes "Partially Reverted"
    rather than "Reverted" so it reads correctly and is not eligible for a
    second revert attempt (see _revert_change_in_session). Empty/None
    reverts the whole change at once, exactly as before this existed."""
    reverter = mongo.db.players.find_one({"id": g.user.get("id", None)})
    if reverter is None or not reverter.get("is_admin", False):
        flash("You must be an admin to revert changes.", "error")
        return False

    selected_paths = set(selected_paths) if selected_paths else None
    try:
        return run_in_transaction_with_retry(
            lambda session: _revert_change_in_session(change_id, reverter, session, selected_paths)
        )
    except _ChangeApprovalRejected as e:
        flash(e.message, "error")
        return False


def _revert_change_in_session(change_id, reverter, session, selected_paths=None):
    """The actual body of revert_change, run inside a real transaction via
    run_in_transaction_with_retry — see _approve_change_in_session's
    docstring for why. Adds the drift check approve_change already had but
    revert_change never did: for an Update revert, the live target is
    expected to still match what approval wrote (after_implemented_data)
    unless it already matches what we're about to restore (before_data,
    e.g. a repeated revert) — anything else means something unrelated
    touched this target since approval, and blindly deep_merge-ing
    before_data onto it would silently clobber that. Same
    check_no_other_changes call get_change_drift_conflicts already surfaces
    as a page-load warning banner before this ever runs — see the design
    doc."""
    changes_collection = mongo.db.changes
    change = changes_collection.find_one({"_id": change_id}, session=session)
    if change is None:
        raise _ChangeApprovalRejected("Change not found.")
    if change["status"] != "Approved":
        raise _ChangeApprovalRejected("Only approved changes can be reverted.")

    now = datetime.now(timezone.utc)
    target_collection = category_data[change["target_collection"]]["database"]
    before_data = change.get("before_implemented_data", {})

    if change["change_type"] == "Add":
        current = target_collection.find_one({"_id": change["target"]}, session=session) or {}
        target_collection.delete_one({"_id": change["target"]}, session=session)
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "status": "Reverted",
            "time_reverted": now,
            "last_modified_time": now,
            "reverter": reverter["_id"],
            "before_revert_data": current,
            "after_revert_data": {},
        }}, session=session)
        _enqueue_propagation(
            session,
            changed_data_type=change["target_collection"],
            changed_object_id=change["target"],
            changed_object={},
            reason=f"Revert of change #{change_id}",
        )
    elif change["change_type"] == "Remove":
        if target_collection.find_one({"_id": change["target"]}, session=session) is not None:
            raise _ChangeApprovalRejected("Cannot revert: the target object already exists in the collection.")
        if not before_data:
            raise _ChangeApprovalRejected("Cannot revert: no before-data was recorded for this change.")
        restored = dict(before_data)
        restored["_id"] = change["target"]
        restored = _calculate_and_attach_fields(change["target_collection"], restored)
        target_collection.insert_one(restored, session=session)
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "status": "Reverted",
            "time_reverted": now,
            "last_modified_time": now,
            "reverter": reverter["_id"],
            "before_revert_data": {},
            "after_revert_data": restored,
        }}, session=session)
        _enqueue_propagation(
            session,
            changed_data_type=change["target_collection"],
            changed_object_id=change["target"],
            changed_object=restored,
            reason=f"Revert of change #{change_id}",
        )
    else:
        existing = target_collection.find_one({"_id": change["target"]}, session=session)
        if existing is None:
            raise _ChangeApprovalRejected("Target object no longer exists.")

        after_implemented_data = change.get("after_implemented_data", {})
        if not check_no_other_changes(after_implemented_data, before_data, existing):
            raise _ChangeApprovalRejected("Cannot revert: the target has changed since this change was approved.")

        # _apply_selected_units is generic about which side is "before" and
        # which is "after" — for a revert, the live document is expected to
        # currently reflect after_implemented_data (today's applied state)
        # and the selected units should move to before_implemented_data
        # (the pre-change state), so those play the "before"/"after" roles
        # respectively here, swapped from approve_change's own call.
        restored, _, _, remaining_before, remaining_after = _apply_selected_units(
            existing, after_implemented_data, before_data, selected_paths
        )
        restored = _calculate_and_attach_fields(change["target_collection"], restored)
        target_collection.update_one({"_id": change["target"]}, {"$set": restored}, session=session)
        if change["target_collection"] == "nations":
            _handle_nation_rename(change["target"], existing.get("name", ""), restored.get("name", ""), session=session)
            _handle_city_changes(existing.get("cities", []), restored.get("cities", []), session=session)

        is_partial = bool(remaining_before or remaining_after)
        update_fields = {
            "status": "Partially Reverted" if is_partial else "Reverted",
            "time_reverted": now,
            "last_modified_time": now,
            "reverter": reverter["_id"],
            "before_revert_data": existing,
            "after_revert_data": restored,
        }
        if is_partial:
            update_fields["reverted_units"] = sorted(selected_paths)
        changes_collection.update_one({"_id": change_id}, {"$set": update_fields}, session=session)
        _enqueue_propagation(
            session,
            changed_data_type=change["target_collection"],
            changed_object_id=change["target"],
            changed_object=restored,
            reason=f"Revert of change #{change_id}",
        )

    return True


def deep_merge(original, updates):
    merged = deepcopy(original)
    for key, value in updates.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            if len(value) == 0:
                merged[key] = value
            else:
                merged[key] = deep_merge(merged[key], value)
        elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
            if _all_have_ids(merged[key]) and _all_have_ids(value):
                # ID-based: both sides carry stable _id values, so after_data
                # carries the full intended list and we replace entirely.
                # Requiring BOTH sides to have IDs prevents a false positive
                # when _ensure_item_ids has stamped fresh IDs onto after_data
                # items that never had them in the DB — in that case the
                # existing list must be merged positionally so its data is
                # not wiped by the minimal {"_id": "hex"} diffs stored in
                # the change document.
                merged[key] = deepcopy(value)
            else:
                # Positional merge (legacy / non-ID data)
                new_list = []
                for i, item in enumerate(value):
                    if i < len(merged[key]) and isinstance(merged[key][i], dict) and isinstance(item, dict):
                        new_list.append(deep_merge(merged[key][i], item))
                    elif i < len(merged[key]):
                        new_list.append(item)
                    else:
                        new_list.append(item)
                merged[key] = new_list
        else:
            merged[key] = value
    return merged

def deep_compare(original, updates):
    if isinstance(original, dict) and isinstance(updates, dict):
        keys = set(original.keys()) | set(updates.keys())
        for key in keys:
            if key not in original or key not in updates or not deep_compare(original[key], updates[key]):
                return False
    elif isinstance(original, list) and isinstance(updates, list):
        if len(original) != len(updates):
            return False
        for i in range(len(original)):
            if not deep_compare(original[i], updates[i]):
                return False
    elif original != updates:
        return False
    return True


def keep_only_differences(before_data, after_data, change_type):
    new_before = {}
    new_after = {}
    if change_type == "Remove":
        for key in before_data:
            new_before[key] = before_data.get(key)
            new_after[key] = None
    else:
        new_before, new_after = keep_only_differences_dict(before_data, after_data)
    
    return new_before, new_after

def keep_only_differences_dict(before_data, after_data):
    new_before = {}
    new_after = {}
    # Note: an empty after_data ({}) is treated as "nothing was submitted
    # for this sub-document" rather than "the user intentionally cleared it".
    # This prevents FormField sub-forms that have no registered fields
    # (e.g. NavalUnitAssignmentDict when naval_unit_details is empty) from
    # generating spurious "all values → None" diffs.  The normal key loop
    # below handles all real changes correctly: if after_data has keys, they
    # are compared against before_data; if after_data is empty, no keys are
    # iterated and the function returns ({}, {}) — meaning no change.

    for key in after_data.keys():
        current_before = before_data.get(key)
        current_after = after_data.get(key)
        if isinstance(current_before, dict) and isinstance(current_after, dict):
            if not current_after and current_before:
                # after is explicitly empty but before had data (e.g. vassal
                # concessions reset to {}).  Record as a direct replacement so
                # the change is not silently dropped by the empty-loop guard.
                new_before[key] = current_before
                new_after[key] = {}
            else:
                temp_before, temp_after = keep_only_differences_dict(current_before, current_after)
                if len(temp_before) > 0 or len(temp_after) > 0:
                    new_before[key], new_after[key] = temp_before, temp_after
        elif isinstance(current_before, list) and isinstance(current_after, list):
            temp_before, temp_after = keep_only_differences_list(current_before, current_after)
            if not deep_compare(temp_before, temp_after):  #If the lists are the same, don't include them in the new data. This is because the lists are already in the database and don't need to be updated. If they are different, include them in the new data. This is because the lists have been updated and need to be updated in the database.
                new_before[key], new_after[key] = temp_before, temp_after
        elif current_before != current_after:
            new_before[key] = current_before
            new_after[key] = current_after
    
    return new_before, new_after

def keep_only_differences_list(before_data, after_data):
    # When all dict items in both lists carry a stable _id, preserve order
    # information and use ID-based matching at approval / display time.
    # Before returning, we restrict each before-item's keys to the set of
    # keys present in its matching after-item.  This prevents calculated
    # fields (e.g. total_progress_per_tick) that exist in the DB's before
    # snapshot but are never submitted by forms from appearing as spurious
    # "X → None" modifications in the change display.  Items that were
    # removed (only in before) keep all their keys so the display can show
    # what was there.
    if _all_have_ids(before_data) and _all_have_ids(after_data):
        after_by_id = {
            item['_id']: item
            for item in after_data
            if isinstance(item, dict) and '_id' in item
        }
        filtered_before = []
        for item in before_data:
            if not isinstance(item, dict) or '_id' not in item:
                filtered_before.append(item)
            elif item['_id'] in after_by_id:
                # Item exists in both snapshots: only track keys the form
                # submitted so that server-computed fields are not shown as
                # going from a real value to None.
                after_item = after_by_id[item['_id']]
                filtered_before.append(
                    {k: v for k, v in item.items() if k in after_item}
                )
            else:
                # Item was removed — keep all keys so the display can show
                # everything that was there before removal.
                filtered_before.append(dict(item))
        return filtered_before, list(after_data)

    # Positional fallback for legacy data without IDs.
    new_before = []
    new_after = []
    for i in range(max(len(before_data), len(after_data))):
        if i < len(before_data) and i < len(after_data):
            if isinstance(before_data[i], dict) and isinstance(after_data[i], dict):
                current_before, current_after = keep_only_differences_dict(before_data[i], after_data[i])
                new_before.append(current_before)  #Need to include this regardless of size because otherwise it will wipe all the items of the list
                new_after.append(current_after)
            elif isinstance(before_data[i], list) and isinstance(after_data[i], list):
                current_before, current_after = keep_only_differences_list(before_data[i], after_data[i])
                new_before.append(current_before)
                new_after.append(current_after)
            else:
                new_before.append(before_data[i])
                new_after.append(after_data[i])
        elif i < len(before_data):
            new_before.append(before_data[i])
        else:
            new_after.append(after_data[i])
    return new_before, new_after

def calculate_int_changes(before_data, after_data):
    diff = {}
    for key in set(before_data) | set(after_data):
        b_val = before_data.get(key)
        a_val = after_data.get(key)
        if isinstance(b_val, int) and isinstance(a_val, int):
            diff[key] = a_val - b_val
    return diff

def describe_check_no_other_changes_conflicts(before_data, after_data, current_data, max_fields=8, _prefix=""):
    """Structured, LEAF-granularity breakdown of why check_no_other_changes
    rejected (before_data, after_data, current_data) — one entry per unit
    at the same granularity partial approval addresses (see
    _list_change_unit_paths): a whole scalar field ("money"), a nested
    dict's sub-key ("stockpiles.stone"), or an item in an ID-keyed list
    ("ongoing_modifiers[id=a1b2c3d4]"). For each unit where the live
    document doesn't match before_data, reports whether it matches
    after_data instead (an already-applied, expected update) or neither (a
    genuine external conflict the admin needs to see).

    Recursing to leaf granularity means a conflict in one sub-field (e.g.
    a nation's "stockpiles.wood") no longer drags its untouched sibling
    ("stockpiles.stone") into the report, and a conflicting item inside a
    large list reports only THAT item instead of the field's entire
    (potentially huge) list value — mirrors check_no_other_changes' own
    recursive comparison branch-for-branch (including its numeric-drift
    tolerance, so a value that check_no_other_changes would silently allow
    is never reported here either) rather than independently re-deciding
    what counts as a mismatch.

    Originally a print-only, top-level-only debug helper added for the
    "system_approve_change blocked" spam seen during local tick testing
    (2026-09-16); promoted to structured data so the same breakdown can
    back both the debug log (via _format_check_no_other_changes_failure_text
    below) and the change review page's inline per-field drift warning —
    see get_change_drift_conflicts.
    """
    entries = []
    all_keys = sorted(set(before_data.keys()) | set(after_data.keys()) | set(current_data.keys()))
    for key in all_keys:
        if len(entries) >= max_fields:
            break
        if key not in after_data:
            continue
        b_val, a_val, c_val = before_data.get(key), after_data.get(key), current_data.get(key)
        path = f"{_prefix}{key}"

        if isinstance(b_val, list) and isinstance(a_val, list) and isinstance(c_val, list) \
                and _all_have_ids(b_val) and _all_have_ids(a_val):
            b_map = {i["_id"]: i for i in b_val if isinstance(i, dict) and "_id" in i}
            a_map = {i["_id"]: i for i in a_val if isinstance(i, dict) and "_id" in i}
            c_map = {i["_id"]: i for i in c_val if isinstance(i, dict) and "_id" in i}

            for item_id, c_item in c_map.items():
                item_path = f"{path}[id={item_id}]"
                if item_id not in b_map and item_id not in a_map:
                    entries.append({"path": item_path, "before": None, "after": None, "live": c_item, "verdict": "conflict"})
                    continue
                b_item = b_map.get(item_id, {})
                a_item = a_map.get(item_id, {})
                c_proj_b = {k: c_item.get(k) for k in b_item}
                c_proj_a = {k: c_item.get(k) for k in a_item}
                if deep_compare(c_proj_b, b_item):
                    continue
                verdict = "matches_after" if deep_compare(c_proj_a, a_item) else "conflict"
                entries.append({"path": item_path, "before": b_item, "after": a_item, "live": c_item, "verdict": verdict})

            for item_id in b_map:
                if item_id in a_map and item_id not in c_map:
                    entries.append({
                        "path": f"{path}[id={item_id}]", "before": b_map[item_id],
                        "after": a_map[item_id], "live": None, "verdict": "conflict",
                    })
            continue

        if isinstance(b_val, dict) and isinstance(a_val, dict) and isinstance(c_val, dict):
            entries.extend(describe_check_no_other_changes_conflicts(
                b_val, a_val, c_val, max_fields=max_fields, _prefix=f"{path}."
            ))
            continue

        # Primitive leaf — including a list/dict that lacks stable identity
        # on at least one side (can't address individual positions
        # meaningfully, since e.g. an insertion shifts every later index —
        # report the whole field as one unit, mirroring
        # check_no_other_changes' own fallthrough for this exact case) or
        # that mismatches type against the live document entirely.
        if c_val == b_val:
            continue
        if c_val == a_val:
            entries.append({"path": path, "before": b_val, "after": a_val, "live": c_val, "verdict": "matches_after"})
            continue
        # Tolerate numeric drift exactly like check_no_other_changes does,
        # so a value it would silently accept is never reported here either.
        if any(isinstance(v, (int, float)) for v in [b_val, a_val, c_val]):
            continue
        entries.append({"path": path, "before": b_val, "after": a_val, "live": c_val, "verdict": "conflict"})

    return entries


def _format_check_no_other_changes_failure_text(entries, max_len=200):
    """Render describe_check_no_other_changes_conflicts' structured output
    as the plain-text block the debug log previously printed inline."""
    if not entries:
        return "  (no top-level field differs from before_data — mismatch is nested; see recursive check)"
    lines = []
    for entry in entries:
        verdict = "matches after_data (expected update)" if entry["verdict"] == "matches_after" else "matches NEITHER before nor after"
        b_repr, a_repr, c_repr = (repr(entry[k])[:max_len] for k in ("before", "after", "live"))
        lines.append(f"  field '{entry['path']}': {verdict}\n    before={b_repr}\n    after ={a_repr}\n    live  ={c_repr}")
    return "\n".join(lines)


def get_change_drift_conflicts(change):
    """Structured drift report comparing a change's recorded target state
    against the live document — the same check approve_change (Pending) or
    a future revert (Approved) gates on, surfaced at page-load time instead
    of only after the admin clicks a button.

    For a Pending change this answers "has the target drifted since this
    was requested". For an Approved change it answers "has the target
    drifted since this was approved" — the same before/after-swapped use of
    check_no_other_changes that closes revert_change's current lack of any
    drift check (see the design doc).

    Returns [] for Add changes (no live target exists to compare against
    yet), hex_map changes (a different, tile-buffer-based shape entirely),
    when the target no longer exists, or when nothing has drifted.
    """
    try:
        change_type = change.get("change_type")
        status = change.get("status")
        if change_type not in ("Update", "Remove") or change.get("target_collection") == "hex_map":
            return []
        if status == "Pending":
            before_data = change.get("before_requested_data", {})
            after_data = change.get("after_requested_data", {})
        elif status == "Approved":
            before_data = change.get("after_implemented_data", {})
            after_data = change.get("before_implemented_data", {})
        else:
            return []

        target_collection = category_data.get(change.get("target_collection", ""), {}).get("database")
        if target_collection is None or not change.get("target"):
            return []
        current = target_collection.find_one({"_id": change["target"]})
        if current is None:
            return []

        if check_no_other_changes(before_data, after_data, current):
            return []
        return describe_check_no_other_changes_conflicts(before_data, after_data, current)
    except Exception:
        traceback.print_exc()
        return []


def check_no_other_changes(before_data, after_data, current_data):
    """
    Check if current_data matches either before_data or after_data for each field.
    Returns False if current_data has changed in ways not reflected in the change request.
    """

    # Check all keys from all three dictionaries
    all_keys = set(before_data.keys()) | set(after_data.keys()) | set(current_data.keys())
    
    for key in all_keys:
        b_val = before_data.get(key)
        a_val = after_data.get(key)
        c_val = current_data.get(key)
        
        # Skip if the key doesn't exist in after_data
        if key not in after_data:
            continue
            
        # Handle lists
        if isinstance(b_val, list) and isinstance(a_val, list) and isinstance(c_val, list):
            if _all_have_ids(b_val) and _all_have_ids(a_val):
                # ID-based comparison: reordering alone does not cause a conflict.
                b_map = {item['_id']: item for item in b_val if isinstance(item, dict) and '_id' in item}
                a_map = {item['_id']: item for item in a_val if isinstance(item, dict) and '_id' in item}
                c_map = {item['_id']: item for item in c_val if isinstance(item, dict) and '_id' in item}

                # Every item currently present must match its before or after version.
                for item_id, c_item in c_map.items():
                    if item_id not in b_map and item_id not in a_map:
                        return False  # Externally added item
                    b_item = b_map.get(item_id, {})
                    a_item = a_map.get(item_id, {})
                    # Project c_item onto only the keys tracked by b_item / a_item.
                    # The live DB document may contain server-calculated fields (e.g.
                    # total_progress_per_tick) that were stripped from the stored
                    # before/after items by keep_only_differences_list.  Those extra
                    # fields must not cause a false "target has changed" failure.
                    c_proj_b = {k: c_item.get(k) for k in b_item}
                    c_proj_a = {k: c_item.get(k) for k in a_item}
                    if not (deep_compare(c_proj_b, b_item) or deep_compare(c_proj_a, a_item)):
                        return False  # Content changed externally

                # Items that were in before AND after (i.e. being modified, not removed)
                # must still be present in current.
                for item_id in b_map:
                    if item_id in a_map and item_id not in c_map:
                        return False  # Expected item removed externally

            else:
                # Positional fallback for legacy / non-ID lists.
                # If lengths differ, check if current matches either before or after
                if len(c_val) != len(b_val) and len(c_val) != len(a_val):
                    return False

                # Check each item in the list
                for i in range(len(c_val)):
                    # Skip if index is out of range for before or after
                    if i >= len(b_val) and i >= len(a_val):
                        return False

                    c_item = c_val[i]
                    b_item = b_val[i] if i < len(b_val) else None
                    a_item = a_val[i] if i < len(a_val) else None

                    # Recursively check dict items
                    if isinstance(c_item, dict) and isinstance(b_item, dict) and isinstance(a_item, dict):
                        if not check_no_other_changes(b_item, a_item, c_item):
                            return False
                    # For non-dict items, check if current matches either before or after
                    elif c_item != b_item and c_item != a_item:
                        return False
                    
        # Handle dictionaries
        elif isinstance(b_val, dict) and isinstance(a_val, dict) and isinstance(c_val, dict):
            if not check_no_other_changes(b_val, a_val, c_val):
                return False
                
        # Handle primitive values
        elif c_val != b_val and c_val != a_val:
            # Allow numeric fields to have changed (e.g. tick-generated rolls,
            # stability values, calculated percentages).
            if any(isinstance(v, (int, float)) for v in [b_val, a_val, c_val]):
                continue
            return False
    
    return True

def get_dependent_objects(changed_data_type, changed_object_id, changed_object, session=None):
    """Find all objects that depend on the changed object"""
    dependent_objects = []

    # Check all schemas for external_calculation_requirements
    for category, category_info in category_data.items():
        schema = category_info.get("schema", {})
        external_reqs = schema.get("external_calculation_requirements", {})

        if not external_reqs:
            continue

        # Check if this category depends on the changed data type
        for local_field, foreign_fields in external_reqs.items():
            field_schema = schema["properties"].get(local_field, {})

            # Indirect link through a join/link table (e.g. nations.markets and
            # markets.members both resolve through market_links). The field's
            # own `collections` list names the FINAL linked entity (nations or
            # markets), never the link table itself, so _depends_on_data_type
            # can't see this case — a change to the link document (e.g. a
            # market_safety_stance edit) would otherwise never propagate to
            # either side, silently going stale (collect_external_requirements
            # reads the link document's own fields directly, per
            # collect_external_modifiers_from_object's linkCollection branch).
            if field_schema.get("linkCollection") == changed_data_type:
                target_field = field_schema.get("linkQueryTarget")
                target_id = changed_object.get(target_field) if target_field else None
                if target_id:
                    try:
                        dependent_objects.append({
                            "data_type": category,
                            "object_id": {"_id": ObjectId(target_id)},
                        })
                    except Exception:
                        pass
                continue

            if _depends_on_data_type(changed_data_type, field_schema):
                # Find objects that reference the changed object
                dependent_ids = []
                if schema.get("properties")[local_field].get("queryTargetAttribute"):
                    db = mongo.db[category]
                    try:
                        new_id = _find_referencing_objects_single(db, ObjectId(changed_object.get(schema.get("properties")[local_field].get("queryTargetAttribute", ""), "")), session=session)
                        if new_id:
                            dependent_ids.append(new_id)
                    except:
                        pass
                else:
                    for collection in schema.get("properties")[local_field].get("collections"):
                        db = mongo.db[collection]
                        dependent_ids += _find_referencing_objects_array(db, local_field, changed_object_id, session=session)

                for dep_id in dependent_ids:
                    dependent_objects.append({
                        "data_type": category,
                        "object_id": dep_id
                    })

    return dependent_objects

def _depends_on_data_type(data_type, field_schema):
    """Check if requirements list depends on a specific data type"""
    if (field_schema.get("bsonType") == "linked_object" or field_schema.get("bsonType") == "array") and field_schema.get("collections"):
        if data_type in field_schema.get("collections"):
            return True
    return False

def _find_referencing_objects_single(db, id, session=None):
    """Find objects in category that reference the given ID in the specified field"""
    return db.find_one({"_id": id}, session=session)

def _find_referencing_objects_array(db, target_field, id, session=None):
    """Find objects in category that reference the given ID in the specified field"""
    return list(db.find({target_field: id}, session=session))

def recalculate_all_objects(data_type):
    """Recalculate all fields for all objects of a given type"""
    db = mongo.db[data_type]
    objects = list(db.find())
    for obj in objects:
        obj = _calculate_and_attach_fields(data_type, obj)
        obj_id = obj.pop("_id")
        db.update_one({"_id": obj_id}, {"$set": obj})
        obj["_id"] = obj_id

def recalculate_object(data_type, object_ref, session=None):
    """Recalculate all fields for an object"""
    db = mongo.db[data_type]
    object_id = None
    object = None
    try:
        object_id = ObjectId(object_ref)
        object = db.find_one({"_id": object_id}, session=session)
    except:
        object_id = object_ref
        object = db.find_one({"name": object_id}, session=session)
    if not object:
        return
    object = _calculate_and_attach_fields(data_type, object)
    search_dict = {}
    if object_id == object_ref:
        search_dict = {"name": object_id}
    else:
        search_dict = {"_id": object_id}
    save_obj = {k: v for k, v in object.items() if k != "_id"}
    db.update_one(search_dict, {"$set": save_obj}, session=session)

_MAX_PROPAGATION_DEPTH = 3

def propagate_updates(changed_data_type, changed_object_id, changed_object, reason="Dependency update", _depth=0, session=None, skip_ids=None):
    """Propagate updates to all dependent objects.

    Depth-limited to _MAX_PROPAGATION_DEPTH to prevent runaway recursion
    on long vassal chains or cyclic dependency graphs from consuming all memory.

    `session`: optional pymongo ClientSession, threaded through every read/
    write here and in the recursive calls — see system_approve_change.

    `skip_ids`: optional set of (data_type, str(object_id)) tuples to skip
    recalculating entirely. recalculate_object's own cost is dominated by a
    full calculate_all_fields recompute (measured at ~8s for a nation), so
    this cascade is expensive for any changed object with dependents — and
    largely redundant when the dependent is *also* about to be committed in
    the same batch (a tick's per-category commit phase, see
    _commit_one_batch in tick_helpers.py), since it'll get its own fresh
    recalculation from that commit regardless of what this cascade would
    have done to it."""
    if _depth >= _MAX_PROPAGATION_DEPTH:
        return
    dependent_objects = get_dependent_objects(changed_data_type, changed_object_id, changed_object, session=session)

    for dep in dependent_objects:
        try:
            dep_id = dep["object_id"]["_id"]
            if skip_ids and (dep["data_type"], str(dep_id)) in skip_ids:
                continue
            db = mongo.db[dep["data_type"]]
            old_object = db.find_one({"_id": dep_id}, session=session)
            if not old_object:
                continue

            propagate_updates(dep["data_type"], dep_id, old_object, reason, _depth=_depth + 1, session=session, skip_ids=skip_ids)
            recalculate_object(dep["data_type"], dep_id, session=session)
        except Exception as e:
            print(f"Error updating {dep['data_type']} {dep['object_id']}: {e}")


# In-flight guard so two overlapping saves of the same object never start
# two concurrent background cascades for it — see _dispatch_propagate_updates.
_propagating_ids_lock = threading.Lock()
_propagating_ids = set()


def _propagate_updates_guarded(changed_data_type, changed_object_id, changed_object, reason, skip_ids, key):
    """Runs propagate_updates in a background thread. Prints a full
    traceback on failure — this codebase's only error-visibility mechanism
    for background work (see tick_helpers.py's _run_tick_guarded, the one
    other place a thread's failure needs to be more visible than a bare
    thread crash) — and always releases the in-flight guard for `key`,
    success or failure."""
    try:
        propagate_updates(changed_data_type, changed_object_id, changed_object, reason, skip_ids=skip_ids)
    except Exception:
        print(f"Background propagate_updates failed for {key}:")
        traceback.print_exc()
    finally:
        with _propagating_ids_lock:
            _propagating_ids.discard(key)


def _dispatch_propagate_updates(changed_data_type, changed_object_id, changed_object, reason="Dependency update", session=None, skip_ids=None):
    """Entry point every change-approval flow uses instead of calling
    propagate_updates directly — decides whether the dependent-object
    cascade can safely run in the background.

    Stays SYNCHRONOUS (identical to calling propagate_updates directly)
    whenever either is true:
      - `session` is set: a pymongo ClientSession can't safely be handed to
        another thread, and the transaction it belongs to (a tick's
        deferred commit phase — see system_approve_change) may already be
        committing/rolling back by the time a background thread got around
        to using it.
      - there's no live Flask request context: a standalone script (e.g.
        scripts/seed_hags_suffering_tick.py) or a background thread (the
        tick itself, an admin_tool_helpers.py async action) has no HTTP
        response to speed up, and — for a script specifically — its whole
        process could exit before a background thread finished, silently
        dropping the cascade.

    Otherwise (an interactive admin HTTP request, no open transaction) runs
    the cascade in a daemon background thread so the admin's response
    returns immediately instead of waiting on a full multi-nation
    recalculation. Skips starting a second background cascade if one for
    this exact object is already in flight — the object's OWN edit was
    already saved synchronously before this is ever called, so skipping a
    duplicate CASCADE just means its dependents catch up on the next
    save/tick instead of this one, rather than two cascades racing to
    recalculate the same dependents.
    """
    if session is not None or not has_request_context():
        propagate_updates(changed_data_type, changed_object_id, changed_object, reason, session=session, skip_ids=skip_ids)
        return

    key = (changed_data_type, str(changed_object_id))
    with _propagating_ids_lock:
        if key in _propagating_ids:
            print(f"Skipping background propagate_updates for {key} — a cascade for it is already in flight.")
            return
        _propagating_ids.add(key)

    thread = threading.Thread(
        target=_propagate_updates_guarded,
        args=(changed_data_type, changed_object_id, changed_object, reason, skip_ids, key),
    )
    thread.daemon = True
    thread.start()


# ---------------------------------------------------------------------------
# Propagation outbox — durable alternative to _dispatch_propagate_updates'
# fire-and-forget background thread, used only by the now-transactional
# interactive approve_change/revert_change path (see _enqueue_propagation's
# call sites). A daemon background thread (the cascade itself) has no
# durability: if the process crashes or the thread raises between the
# target being saved and the cascade finishing, the cascade is just gone —
# nothing retries it, and nothing records that it was ever owed. Writing a
# propagation_outbox entry INSIDE the same transaction as the target write
# and the change-status update (see _enqueue_propagation) makes "this
# object has a pending dependency cascade" durable and atomic with the edit
# that caused it; a separate long-running worker thread (started once per
# process via start_propagation_outbox_worker, same shape as the existing
# tick-launch threads in tick_helpers.py) claims and runs entries later, on
# its own schedule, independent of the admin's request/response cycle.
#
# Every OTHER caller of propagate_updates (ticks, force_approve_change,
# system_force_approve_change, standalone scripts) is unchanged and keeps
# using _dispatch_propagate_updates/propagate_updates directly — ticks
# already run inside their own transaction and pay this cost synchronously
# per commit chunk, and a script's whole process exiting before a
# background thread finishes is exactly the kind of loss this outbox exists
# to prevent, which argues for routing scripts through it too — but that's
# a larger change than this revamp's stated scope (change
# viewing/approval), so left for a future pass.
# ---------------------------------------------------------------------------

def _enqueue_propagation(session, changed_data_type, changed_object_id, changed_object, reason, skip_ids=None):
    """Insert a propagation_outbox entry inside the caller's transaction
    `session` — the enqueue commits (or rolls back) atomically together
    with whatever write it accompanies. `skip_ids` (a set of
    (data_type, str(object_id)) tuples — see propagate_updates) is stored
    as a list of lists since BSON has no tuple/set type."""
    mongo.db.propagation_outbox.insert_one({
        "status": "pending",
        "changed_data_type": changed_data_type,
        "changed_object_id": changed_object_id,
        "changed_object": changed_object,
        "reason": reason,
        "skip_ids": [list(pair) for pair in skip_ids] if skip_ids else None,
        "created_at": datetime.now(timezone.utc),
        "claimed_at": None,
        "completed_at": None,
        "error": None,
        "attempts": 0,
    }, session=session)


# How often the worker polls for new outbox entries when idle, and how many
# times it retries one entry (requeuing it as "pending" again) before
# giving up and marking it "failed" for an admin to notice via
# count_failed_propagations rather than retrying it forever.
_OUTBOX_POLL_INTERVAL_SECONDS = 5
_OUTBOX_MAX_ATTEMPTS = 3


def _claim_next_outbox_entry():
    """Atomically claim the oldest pending outbox entry, or None if there
    isn't one. find_one_and_update is itself atomic, so multiple worker
    threads/processes (e.g. this app's 3 gunicorn workers, each running its
    own copy of this loop) never double-claim the same entry. Returns the
    document AFTER the update (status "processing", attempts incremented)
    so _process_outbox_entry's max-attempts check sees the current count."""
    return mongo.db.propagation_outbox.find_one_and_update(
        {"status": "pending"},
        {"$set": {"status": "processing", "claimed_at": datetime.now(timezone.utc)},
         "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _process_outbox_entry(entry):
    """Run one claimed outbox entry's propagate_updates cascade, marking it
    "done" on success. On failure: requeue as "pending" to retry later if
    under _OUTBOX_MAX_ATTEMPTS, otherwise mark "failed" permanently (counted
    by count_failed_propagations for an admin banner) — never silently
    drops it either way."""
    try:
        skip_ids = {tuple(pair) for pair in entry["skip_ids"]} if entry.get("skip_ids") else None
        propagate_updates(
            entry["changed_data_type"], entry["changed_object_id"], entry["changed_object"],
            entry.get("reason", "Dependency update"), skip_ids=skip_ids,
        )
        mongo.db.propagation_outbox.update_one(
            {"_id": entry["_id"]},
            {"$set": {"status": "done", "completed_at": datetime.now(timezone.utc)}},
        )
    except Exception as e:
        traceback.print_exc()
        gave_up = entry.get("attempts", 1) >= _OUTBOX_MAX_ATTEMPTS
        mongo.db.propagation_outbox.update_one(
            {"_id": entry["_id"]},
            {"$set": {
                "status": "failed" if gave_up else "pending",
                "error": f"{type(e).__name__}: {e}",
            }},
        )


def count_failed_propagations():
    """How many outbox entries permanently gave up — surfaced as an admin
    banner (see routes/base_routes.py) instead of silently vanishing."""
    return mongo.db.propagation_outbox.count_documents({"status": "failed"})


_outbox_worker_lock = threading.Lock()
_outbox_worker_started = False


def start_propagation_outbox_worker():
    """Start the background thread that claims and runs propagation_outbox
    entries. Call exactly once per process, from app.py at startup — NOT
    from this module's own import time, so anything that imports
    change_helpers without going through the real app (tests, one-off
    scripts, migrations) never spawns a persistent polling thread. Safe to
    call more than once in the same process: every call after the first is
    a no-op."""
    global _outbox_worker_started
    with _outbox_worker_lock:
        if _outbox_worker_started:
            return
        _outbox_worker_started = True

    def _loop():
        while True:
            try:
                entry = _claim_next_outbox_entry()
                if entry is None:
                    sleep(_OUTBOX_POLL_INTERVAL_SECONDS)
                    continue
                _process_outbox_entry(entry)
            except Exception:
                traceback.print_exc()
                sleep(_OUTBOX_POLL_INTERVAL_SECONDS)

    thread = threading.Thread(target=_loop, name="propagation-outbox-worker")
    thread.daemon = True
    thread.start()
