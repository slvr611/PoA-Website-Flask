"""Manual script to archive changes older than MONTHS_TO_KEEP months.

Usage:
    python scripts/archive_old_changes.py

Connects to MongoDB via the MONGO_URI env var (loaded from .env), exports
all non-pending changes whose last_modified_time is older than MONTHS_TO_KEEP
months to the S3 backups/ folder, then deletes them from MongoDB.
"""

import os
import sys
import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(override=True)

from pymongo import MongoClient
from bson import json_util
import boto3

# ── Configurable ──────────────────────────────────────────────────────────────
MONTHS_TO_KEEP = 6
# ─────────────────────────────────────────────────────────────────────────────


def upload_to_s3(file_path, s3_key):
    s3_bucket = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    if not s3_bucket or not aws_access_key or not aws_secret_key:
        return False, "S3 configuration missing"

    s3_client = boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key
    )
    s3_client.upload_file(file_path, s3_bucket, s3_key)
    return True, f"s3://{s3_bucket}/{s3_key}"


def main():
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("ERROR: MONGO_URI not set.")
        sys.exit(1)

    parsed = urlparse(mongo_uri)
    db_name = parsed.path.lstrip('/')
    if '?' in db_name:
        db_name = db_name.split('?')[0]

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=MONTHS_TO_KEEP * 30)

    def parse_dt(value):
        """Return a timezone-aware datetime from either a datetime object or a string."""
        if isinstance(value, datetime.datetime):
            return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
        if isinstance(value, str):
            # Handle trailing Z (Python < 3.11 fromisoformat doesn't accept it)
            normalized = value.rstrip('Z')
            try:
                dt = datetime.datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        return None

    def is_older_than_cutoff(doc):
        """Check the most relevant date field for the doc's status."""
        status = doc.get("status")
        field = "time_implemented" if status == "Approved" else "time_denied" if status == "Denied" else "last_modified_time"
        dt = parse_dt(doc.get(field)) or parse_dt(doc.get("last_modified_time"))
        return dt is not None and dt < cutoff

    all_non_pending = list(db.changes.find({"status": {"$ne": "Pending"}}))
    docs = [d for d in all_non_pending if is_older_than_cutoff(d)]

    if not docs:
        print(f"No changes older than {MONTHS_TO_KEEP} months found. Nothing to do.")
        return

    print(f"Found {len(docs)} changes to archive (older than {MONTHS_TO_KEEP} months).")

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f"changes_archive_manual_{timestamp}.json"
    tmp_path = os.path.join(os.getcwd(), filename)

    with open(tmp_path, 'w') as f:
        f.write(json_util.dumps(docs, indent=2))

    print(f"Exported to {tmp_path}. Uploading to S3...")

    success, message = upload_to_s3(tmp_path, f"backups/{filename}")
    os.remove(tmp_path)

    if not success:
        print(f"ERROR: S3 upload failed ({message}). Changes NOT deleted.")
        sys.exit(1)

    print(f"Uploaded: {message}")

    ids = [d["_id"] for d in docs]
    result = db.changes.delete_many({"_id": {"$in": ids}})

    if result.deleted_count == len(docs):
        print(f"Deleted {result.deleted_count} changes from MongoDB.")
    else:
        # Batch delete under-counted — fall back to individual deletes.
        # This handles the case where old _id values are stored as strings
        # while some are ObjectIds, causing $in type mismatches.
        print(f"Batch delete only removed {result.deleted_count}/{len(docs)} — retrying remaining individually.")
        already_deleted = set()
        # Re-fetch what's still in the collection from our set
        remaining = list(db.changes.find({"_id": {"$in": ids}}))
        deleted_individually = 0
        for doc in remaining:
            _id = doc["_id"]
            if _id in already_deleted:
                continue
            r = db.changes.delete_one({"_id": _id})
            if r.deleted_count:
                deleted_individually += 1
                already_deleted.add(_id)
            else:
                print(f"  WARNING: could not delete _id={_id!r} (type {type(_id).__name__})")
        total = result.deleted_count + deleted_individually
        print(f"Deleted {total}/{len(docs)} changes from MongoDB.")


if __name__ == "__main__":
    main()
