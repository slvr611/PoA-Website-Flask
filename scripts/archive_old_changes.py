"""Manual script to archive changes older than MONTHS_TO_KEEP months.

Usage:
    python scripts/archive_old_changes.py

Connects to MongoDB via the MONGO_URI env var (loaded from .env), exports
non-pending changes in streaming batches to S3, then deletes them.

Dates in this collection are stored as ISO strings (e.g. "2025-10-09 09:30:00"),
so MongoDB-side filtering uses string comparison (ISO format sorts correctly).
"""

import os
import sys
import datetime
import json
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(override=True)

from pymongo import MongoClient
from bson import json_util
import boto3

# ── Configurable ──────────────────────────────────────────────────────────────
MONTHS_TO_KEEP = 3   # archive changes older than this
BATCH_SIZE = 500     # docs per S3 file
# ─────────────────────────────────────────────────────────────────────────────


def get_s3_client():
    s3_bucket = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    if not s3_bucket or not aws_access_key or not aws_secret_key:
        return None, None, "S3 configuration missing"
    client = boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
    )
    return client, s3_bucket, None


def upload_bytes_to_s3(client, bucket, key, data: bytes):
    from io import BytesIO
    client.upload_fileobj(BytesIO(data), bucket, key)


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

    s3_client, s3_bucket, s3_err = get_s3_client()
    if s3_err:
        print(f"ERROR: {s3_err}")
        sys.exit(1)

    # Cutoff as an ISO string (dates in the collection are stored as strings)
    cutoff_str = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=MONTHS_TO_KEEP * 30)
    ).strftime('%Y-%m-%d')

    print(f"Archiving non-pending changes with time_implemented < {cutoff_str} ...")

    # Build the filter — approved changes use time_implemented; others use last_modified_time
    query = {
        "status": {"$ne": "Pending"},
        "$or": [
            {"time_implemented": {"$lt": cutoff_str}},
            {
                "time_implemented": {"$exists": False},
                "last_modified_time": {"$lt": cutoff_str},
            },
        ],
    }

    total = db.changes.count_documents(query)
    if total == 0:
        print(f"No changes older than {MONTHS_TO_KEEP} months. Nothing to do.")
        return

    print(f"Found {total} changes to archive.")

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
    batch_num = 0
    deleted_total = 0
    batch_ids = []
    batch_docs = []

    def flush_batch():
        nonlocal batch_num, deleted_total, batch_ids, batch_docs
        if not batch_docs:
            return
        batch_num += 1
        key = f"backups/changes_archive_{timestamp}_batch{batch_num:04d}.json"
        data = json_util.dumps(batch_docs, indent=2).encode()
        upload_bytes_to_s3(s3_client, s3_bucket, key, data)
        result = db.changes.delete_many({"_id": {"$in": batch_ids}})
        deleted_total += result.deleted_count
        print(f"  Batch {batch_num}: archived {len(batch_docs)} → s3://{s3_bucket}/{key}, deleted {result.deleted_count}")
        batch_ids = []
        batch_docs = []

    cursor = db.changes.find(query, no_cursor_timeout=True).batch_size(BATCH_SIZE)
    try:
        for doc in cursor:
            batch_docs.append(doc)
            batch_ids.append(doc["_id"])
            if len(batch_docs) >= BATCH_SIZE:
                flush_batch()
        flush_batch()  # remaining
    finally:
        cursor.close()

    print(f"\nDone. Archived and deleted {deleted_total}/{total} changes.")
    print(f"Remaining in collection: {db.changes.count_documents({})}")


if __name__ == "__main__":
    main()
