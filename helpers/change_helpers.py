import uuid
from datetime import datetime, timezone
from app_core import mongo, category_data
from flask import g, flash
from calculations.field_calculations import calculate_all_fields
from copy import deepcopy
from bson import ObjectId

_NATURAL_KEY_FIELDS = ['name', 'quest_name', 'source', 'field', 'key']


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
        if not _all_have_ids(before_data):
            return

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


def _calculate_and_attach_fields(data_type, target):
    schema = category_data[data_type]["schema"]
    target_data_type = category_data[data_type]["singularName"].lower()

    if data_type == "nations":
        calculated_fields, breakdowns = calculate_all_fields(
            target,
            schema,
            target_data_type,
            return_breakdowns=True
        )
        target.update(calculated_fields)
        target["breakdowns"] = breakdowns
    else:
        calculated_fields = calculate_all_fields(target, schema, target_data_type)
        target.update(calculated_fields)

    target.pop("_calc_cache", None)
    return target

def request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"id": g.user.get("id", None)})["_id"]
    if requester is None:
        flash("You must be logged in to request changes.")
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None)

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
        "status": "Pending"
    }
    result = changes_collection.insert_one(change_doc)
    return result.inserted_id

def system_request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"name": "System"})
    if requester is None:
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None)

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
        "status": "Pending"
    }
    result = changes_collection.insert_one(change_doc)
    return result.inserted_id


def approve_change(change_id):
    approver = mongo.db.players.find_one({"id": g.user.get("id", None)})
    if approver is None or not approver.get("is_admin", False):
        flash("You must be an admin to approve changes.")
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]
        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)
        inserted_item_id = target_collection.insert_one(after_data).inserted_id
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "last_modified_time": now,
            "approver": approver["_id"],
            "before_implemented_data": {},
            "after_implemented_data": after_data
        }})

        propagate_updates(
            changed_data_type=change["target_collection"],
            changed_object_id=inserted_item_id,
            changed_object=after_data,
            reason=f"Dependency update from change #{change_id}"
        )

        return True
    else:
        target = target_collection.find_one({"_id": change["target"]})
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        if check_no_other_changes(before_data, after_data, target):
            if change["change_type"] == "Update":
                existing = target_collection.find_one({"_id": change["target"]})
                merged = deep_merge(existing, after_data)
                merged = _calculate_and_attach_fields(change["target_collection"], merged)
                target_collection.update_one({"_id": change["target"]}, {"$set": merged})
            else:
                target_collection.delete_one({"_id": change["target"]})

            changes_collection.update_one({"_id": change_id}, {"$set": {
                "status": "Approved",
                "time_implemented": now,
                "last_modified_time": now,
                "approver": approver["_id"],
                "before_implemented_data": before_data,
                "after_implemented_data": after_data
            }})
            if change["change_type"] == "Update":
                propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object=merged,
                    reason=f"Dependency update from change #{change_id}"
                )
            else:
                propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object={},
                    reason=f"Dependency update from change #{change_id}"
                )

            return True
        else:
            flash("Change approval failed because the target has changed since the request was made.")
    return False

def system_approve_change(change_id):
    approver = mongo.db.players.find_one({"name": "System"})

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]
        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)

        inserted_item_id = target_collection.insert_one(after_data).inserted_id
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "last_modified_time": now,
            "approver": approver["_id"],
            "before_implemented_data": {},
            "after_implemented_data": after_data
        }})

        propagate_updates(
            changed_data_type=change["target_collection"],
            changed_object_id=inserted_item_id,
            changed_object=after_data,
            reason=f"Dependency update from change #{change_id}"
        )
        return True
    else:
        target = target_collection.find_one({"_id": change["target"]})
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        if check_no_other_changes(before_data, after_data, target):
            if change["change_type"] == "Update":
                existing = target_collection.find_one({"_id": change["target"]})
                merged = deep_merge(existing, after_data)
                merged = _calculate_and_attach_fields(change["target_collection"], merged)
                target_collection.update_one({"_id": change["target"]}, {"$set": merged})
            else:
                target_collection.delete_one({"_id": change["target"]})

            changes_collection.update_one({"_id": change_id}, {"$set": {
                "status": "Approved",
                "time_implemented": now,
                "last_modified_time": now,
                "approver": approver["_id"],
                "before_implemented_data": target,
                "after_implemented_data": after_data
            }})
            
            if change["change_type"] == "Update":
                propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object=merged,
                    reason=f"Dependency update from change #{change_id}"
                )
            else:
                propagate_updates(
                    changed_data_type=change["target_collection"],
                    changed_object_id=change["target"],
                    changed_object={},
                    reason=f"Dependency update from change #{change_id}"
                )
            return True
    return False


def force_approve_change(change_id):
    """Approve a change without running check_no_other_changes.

    Used when a change was stranded by subsequent ticks modifying the target,
    but the intended update is still correct and should be applied.
    """
    approver = mongo.db.players.find_one({"id": g.user.get("id", None)})
    if approver is None or not approver.get("is_admin", False):
        flash("You must be an admin to approve changes.")
        return None

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]
    after_data = change["after_requested_data"]
    before_data = change["before_requested_data"]

    if change["change_type"] == "Update":
        existing = target_collection.find_one({"_id": change["target"]})
        merged = deep_merge(existing, after_data)
        merged = _calculate_and_attach_fields(change["target_collection"], merged)
        target_collection.update_one({"_id": change["target"]}, {"$set": merged})
    elif change["change_type"] == "Add":
        after_data = _calculate_and_attach_fields(change["target_collection"], after_data)
        change["target"] = target_collection.insert_one(after_data).inserted_id
    else:
        target_collection.delete_one({"_id": change["target"]})

    changes_collection.update_one({"_id": change_id}, {"$set": {
        "status": "Approved",
        "time_implemented": now,
        "last_modified_time": now,
        "approver": approver["_id"],
        "before_implemented_data": before_data,
        "after_implemented_data": after_data
    }})
    propagate_updates(
        changed_data_type=change["target_collection"],
        changed_object_id=change["target"],
        changed_object=merged if change["change_type"] == "Update" else after_data,
        reason=f"Force-approved change #{change_id}"
    )
    return True


def deny_change(change_id):
    denier = mongo.db.players.find_one({"id": g.user["id"]})
    if denier is None or not denier.get("is_admin", False):
        return False

    changes_collection = mongo.db.changes
    now = datetime.now(timezone.utc)

    changes_collection.update_one({"_id": change_id}, {"$set": {
        "status": "Rejected",
        "time_rejected": now,
        "last_modified_time": now,
        "denier": denier["_id"]
    }})
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

def get_dependent_objects(changed_data_type, changed_object_id, changed_object):
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
            if _depends_on_data_type(changed_data_type, schema["properties"][local_field]):
                # Find objects that reference the changed object
                dependent_ids = []
                if schema.get("properties")[local_field].get("queryTargetAttribute"):
                    db = mongo.db[category]
                    try:
                        new_id = _find_referencing_objects_single(db, ObjectId(changed_object.get(schema.get("properties")[local_field].get("queryTargetAttribute", ""), "")))
                        if new_id:
                            dependent_ids.append(new_id)
                    except:
                        pass
                else:
                    for collection in schema.get("properties")[local_field].get("collections"):
                        db = mongo.db[collection]
                        dependent_ids += _find_referencing_objects_array(db, local_field, changed_object_id)

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

def _find_referencing_objects_single(db, id):
    """Find objects in category that reference the given ID in the specified field"""
    return db.find_one({"_id": id})

def _find_referencing_objects_array(db, target_field, id):
    """Find objects in category that reference the given ID in the specified field"""
    return list(db.find({target_field: id}))

def recalculate_all_objects(data_type):
    """Recalculate all fields for all objects of a given type"""
    db = mongo.db[data_type]
    objects = list(db.find())
    for object in objects:
        object = _calculate_and_attach_fields(data_type, object)
        db.update_one({"_id": object["_id"]}, {"$set": object})

def recalculate_object(data_type, object_ref):
    """Recalculate all fields for an object"""
    db = mongo.db[data_type]
    object_id = None
    object = None
    try:
        object_id = ObjectId(object_ref)
        object = db.find_one({"_id": object_id})
    except:
        object_id = object_ref
        object = db.find_one({"name": object_id})
    if not object:
        return
    object = _calculate_and_attach_fields(data_type, object)
    search_dict = {}
    if object_id == object_ref:
        search_dict = {"name": object_id}
    else:
        search_dict = {"_id": object_id}
    db.update_one(search_dict, {"$set": object})

def propagate_updates(changed_data_type, changed_object_id, changed_object, reason="Dependency update"):
    """Propagate updates to all dependent objects"""
    dependent_objects = get_dependent_objects(changed_data_type, changed_object_id, changed_object)

    for dep in dependent_objects:
        try:
            # Get the object and its schema
            db = mongo.db[dep["data_type"]]
            schema = category_data[dep["data_type"]]["schema"]
            
            old_object = db.find_one({"_id": dep["object_id"]["_id"]})
            if not old_object:
                continue

            propagate_updates(dep["data_type"], dep["object_id"]["_id"], old_object, reason)

            recalculate_object(dep["data_type"], dep["object_id"]["_id"])
            
        except Exception as e:
            print(f"Error updating {dep['data_type']} {dep['object_id']}: {e}")
