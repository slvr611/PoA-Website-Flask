import os
from datetime import datetime, timezone
from bson import json_util
from app_core import mongo, upload_to_s3

# Number of sessions (ticks) of non-pending changes to retain.
# Changes approved/denied more than this many sessions ago will be
# exported to S3 and deleted from MongoDB on each tick.
ARCHIVE_AFTER_N_SESSIONS = 20


def archive_old_changes(current_session):
    """Export old non-pending changes to S3 then delete them from MongoDB.

    Called automatically at the end of each tick.  Returns a short summary
    string suitable for appending to the tick summary.
    """
    # Migration step: stamp session_number = current_session - 1 on any
    # existing non-pending changes that predate this feature.  This means
    # they will survive ARCHIVE_AFTER_N_SESSIONS more ticks before being
    # archived rather than being swept up immediately.
    mongo.db.changes.update_many(
        {"status": {"$ne": "Pending"}, "session_number": {"$exists": False}},
        {"$set": {"session_number": current_session - 1}}
    )

    cutoff = current_session - ARCHIVE_AFTER_N_SESSIONS
    docs = list(mongo.db.changes.find(
        {"status": {"$ne": "Pending"}, "session_number": {"$lt": cutoff}}
    ))
    if not docs:
        return "No changes to archive."

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f"changes_archive_{timestamp}_session-{current_session}.json"
    tmp_path = os.path.join(os.getcwd(), filename)

    with open(tmp_path, 'w') as f:
        f.write(json_util.dumps(docs, indent=2))

    s3_key = f"backups/{filename}"
    success, message = upload_to_s3(tmp_path, s3_key)
    os.remove(tmp_path)

    if not success:
        return f"Archive upload failed ({message}). Changes NOT deleted."

    ids = [d["_id"] for d in docs]
    mongo.db.changes.delete_many({"_id": {"$in": ids}})

    return f"Archived {len(docs)} changes to S3: {s3_key}"
