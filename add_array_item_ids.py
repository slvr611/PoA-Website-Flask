"""One-time migration: stamp a stable ``_id`` onto every embedded sub-document
list item that lacks one.

Run this script once against the production database after deploying the
ID-based array tracking changes.  It is safe to run multiple times — items
that already have an ``_id`` are left untouched.

Usage (from the project root):
    python -m migrations.add_array_item_ids
"""

import uuid
from app_core import mongo


# Maps each collection name to the list of top-level array fields whose dict
# items need a stable _id.
COLLECTION_ARRAY_FIELDS = {
    "nations":      ["modifiers", "progress_quests", "districts", "cities"],
    "characters":   ["modifiers", "progress_quests"],
    "factions":     ["progress_quests"],
    "merchants":    ["progress_quests"],
    "mercenaries":  ["progress_quests", "districts"],
    "regions":      ["external_modifiers"],
    "religions":    ["external_modifiers"],
    "wonders":      ["external_modifiers"],
    "artifacts":    ["external_modifiers"],
}


def _stamp_ids_in_list(lst):
    """Return True if any item was modified."""
    modified = False
    for item in lst:
        if isinstance(item, dict) and "_id" not in item:
            item["_id"] = uuid.uuid4().hex[:8]
            modified = True
    return modified


def migrate_add_array_item_ids():
    db = mongo.db
    total_docs = 0
    total_modified = 0

    for collection_name, array_fields in COLLECTION_ARRAY_FIELDS.items():
        collection = db[collection_name]
        docs_modified = 0

        for doc in collection.find():
            updates = {}
            for field in array_fields:
                lst = doc.get(field)
                if not isinstance(lst, list) or not lst:
                    continue
                # Work on a copy so we can detect changes
                lst_copy = [
                    dict(item) if isinstance(item, dict) else item
                    for item in lst
                ]
                if _stamp_ids_in_list(lst_copy):
                    updates[field] = lst_copy

            if updates:
                collection.update_one({"_id": doc["_id"]}, {"$set": updates})
                docs_modified += 1

        print(f"{collection_name}: {docs_modified} document(s) updated")
        total_docs += collection.count_documents({})
        total_modified += docs_modified

    print(f"\nMigration complete. {total_modified} document(s) modified across all collections.")


if __name__ == "__main__":
    migrate_add_array_item_ids()
