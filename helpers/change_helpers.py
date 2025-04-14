from datetime import datetime
from app_core import mongo, category_data
from flask import g

def request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"id": g.user.get("id", None)})["_id"]
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
        return None

    changes_collection = mongo.db.changes
    now = datetime.now()
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        after_data = change["after_requested_data"]
        inserted_item_id = target_collection.insert_one(after_data).inserted_id
        changes_collection.update_one({"_id": change_id}, {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "approver": approver["_id"],
            "before_implemented_data": {},
            "after_implemented_data": after_data
        }})
        return True
    else:
        target = target_collection.find_one({"_id": change["target"]})
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        if check_no_other_changes(before_data, after_data, target):
            if change["change_type"] == "Update":
                target_collection.update_one({"_id": change["target"]}, {"$set": after_data})
            else:
                target_collection.delete_one({"_id": change["target"]})

            changes_collection.update_one({"_id": change_id}, {"$set": {
                "status": "Approved",
                "time_implemented": now,
                "approver": approver["_id"],
                "before_implemented_data": before_data,
                "after_implemented_data": after_data
            }})
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
        "denier": denier["_id"]
    }})
    return True


def keep_only_differences(before_data, after_data, change_type):
    new_before = {}
    new_after = {}
    if change_type == "Remove":
        for key in before_data:
            new_before[key] = before_data.get(key)
            new_after[key] = after_data.get(key)
    else:
        for key in after_data:
            if before_data.get(key) != after_data.get(key):
                new_before[key] = before_data.get(key)
                new_after[key] = after_data.get(key)
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
    for key in before_data:
        b_val = before_data.get(key)
        a_val = after_data.get(key)
        c_val = current_data.get(key)
        if b_val != c_val and a_val != c_val and not any(isinstance(v, int) for v in [b_val, a_val, c_val]):
            return False
    return True
