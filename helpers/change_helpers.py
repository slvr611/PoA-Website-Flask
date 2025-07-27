from datetime import datetime
from app_core import mongo, category_data
from flask import g, flash
from calculations.field_calculations import calculate_all_fields
from copy import deepcopy
from bson import ObjectId

def request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"id": g.user.get("id", None)})["_id"]
    if requester is None:
        flash("You must be logged in to request changes.")
        return None

    changes_collection = mongo.db.changes
    now = datetime.now()

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None)

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
    now = datetime.now()

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None)

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
    now = datetime.now()
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]
        calculated_fields = calculate_all_fields(after_data, category_data[change["target_collection"]]["schema"], category_data[change["target_collection"]]["singularName"].lower())
        after_data.update(calculated_fields)
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
            changed_object=merged,
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
                calculated_fields = calculate_all_fields(merged, category_data[change["target_collection"]]["schema"], category_data[change["target_collection"]]["singularName"].lower())
                merged.update(calculated_fields)
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

            propagate_updates(
                changed_data_type=change["target_collection"],
                changed_object_id=change["target"],
                changed_object=merged,
                reason=f"Dependency update from change #{change_id}"
            )

            return True
        else:
            flash("Change approval failed because the target has changed since the request was made.")
    return False

def system_approve_change(change_id):
    approver = mongo.db.players.find_one({"name": "System"})

    changes_collection = mongo.db.changes
    now = datetime.now()
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]
        calculated_fields = calculate_all_fields(after_data, category_data[change["target_collection"]]["schema"], category_data[change["target_collection"]]["singularName"].lower())
        after_data.update(calculated_fields)

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
            changed_object=merged,
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
                calculated_fields = calculate_all_fields(merged, category_data[change["target_collection"]]["schema"], category_data[change["target_collection"]]["singularName"].lower())
                merged.update(calculated_fields)
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

            propagate_updates(
                changed_data_type=change["target_collection"],
                changed_object_id=change["target"],
                changed_object=merged,
                reason=f"Dependency update from change #{change_id}"
            )
            return True
    return False


def deny_change(change_id):
    denier = mongo.db.players.find_one({"id": g.user["id"]})
    if denier is None or not denier.get("is_admin", False):
        return False

    changes_collection = mongo.db.changes
    now = datetime.now()

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
    if len(after_data) == 0 and not deep_compare(before_data, after_data):  # If after_data is empty and before_data is not, then we want to include it in the changes to clear the data 
        return before_data, after_data

    for key in after_data.keys():
        current_before = before_data.get(key)
        current_after = after_data.get(key)
        if isinstance(current_before, dict) and isinstance(current_after, dict):
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
            # Special case for integers (allow changes)
            if any(isinstance(v, int) for v in [b_val, a_val, c_val]):
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

            new_object = deepcopy(old_object)

            # Create change request
            change_id = system_request_change(
                data_type=dep["data_type"],
                item_id=old_object["_id"],
                change_type="Update",
                before_data=old_object,
                after_data=new_object,
                reason=f"{reason} - {changed_data_type} {changed_object_id} changed"
            )
            system_approve_change(change_id)
            
        except Exception as e:
            print(f"Error updating {dep['data_type']} {dep['object_id']}: {e}")