"""Manual script to archive changes older than DAYS_TO_KEEP days.

Usage:
    python scripts/archive_old_changes.py

Connects to MongoDB via the MONGO_URI env var (loaded from .env), exports
non-pending changes in streaming batches to S3, then deletes them.

The collection has a mixed date-field schema left over from a past migration:
time_implemented/last_modified_time are stored as real BSON dates on some
documents and as ISO strings (e.g. "2025-10-09 09:30:00") on others. MongoDB's
$lt only matches within the same BSON type, so the cutoff filter below checks
both a datetime cutoff and an equivalent ISO-string cutoff for each field.
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
DAYS_TO_KEEP = 30    # archive changes older than this
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

    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_TO_KEEP)
    cutoff_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')

    print(f"Archiving non-pending changes older than {cutoff_dt.date()} ({DAYS_TO_KEEP} days) ...")

    def _lt_cutoff(field):
        # MongoDB's comparison operators compare ACROSS BSON types using a
        # fixed type-order (strings sort below dates), so a bare
        # {"$lt": cutoff_dt} would match every string-typed value
        # unconditionally regardless of its actual date — not just old ones.
        # $type scopes each branch to its own representation.
        return {"$or": [
            {field: {"$type": "date", "$lt": cutoff_dt}},
            {field: {"$type": "string", "$lt": cutoff_str}},
        ]}

    # Build the filter — approved changes use time_implemented; others use last_modified_time
    query = {
        "status": {"$ne": "Pending"},
        "$or": [
            {"$and": [{"time_implemented": {"$exists": True}}, _lt_cutoff("time_implemented")]},
            {"$and": [{"time_implemented": {"$exists": False}}, _lt_cutoff("last_modified_time")]},
        ],
    }

    total = db.changes.count_documents(query)
    if total == 0:
        print(f"No changes older than {DAYS_TO_KEEP} days. Nothing to do.")
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
        print(f"  Batch {batch_num}: archived {len(batch_docs)} -> s3://{s3_bucket}/{key}, deleted {result.deleted_count}")
        batch_ids = []
        batch_docs = []

    # Atlas's free (M0) tier disallows no_cursor_timeout cursors, so a single
    # long-lived cursor spanning every S3 upload isn't an option. Instead,
    # fetch the (small) list of matching _ids up front, then re-query by
    # _id for each batch — each query is short-lived and independent, so
    # slow S3 uploads between batches can't time out a cursor.
    all_ids = [d["_id"] for d in db.changes.find(query, {"_id": 1})]
    for i in range(0, len(all_ids), BATCH_SIZE):
        id_batch = all_ids[i:i + BATCH_SIZE]
        batch_docs = list(db.changes.find({"_id": {"$in": id_batch}}))
        batch_ids = [d["_id"] for d in batch_docs]
        flush_batch()

    print(f"\nDone. Archived and deleted {deleted_total}/{total} changes.")
    print(f"Remaining in collection: {db.changes.count_documents({})}")


if __name__ == "__main__":
    main()
