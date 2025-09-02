from app_core import mongo

def migrate_reknown_to_renown():
    """One-time migration to fix reknown spelling"""
    db = mongo.db.mercenaries
    
    # Find all documents with the old field
    docs_to_update = db.find({"reknown": {"$exists": True}})
    
    for doc in docs_to_update:
        # Copy old field to new field if new field doesn't exist
        if "renown" not in doc:
            db.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {"renown": doc["reknown"]},
                    "$unset": {"reknown": ""}
                }
            )
    
    print(f"Migration completed for mercenaries collection")

if __name__ == "__main__":
    migrate_reknown_to_renown()