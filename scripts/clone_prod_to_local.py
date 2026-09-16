"""Clone the production MongoDB database into a local MongoDB instance.

This is step 1 of reproducing/debugging problems (a stuck or slow tick, an
unexpected calculation, etc.) without any risk to production: it makes a
disposable local copy of the real data that you can then run a test tick
against with run_local_tick.py. See that script for step 2.

Usage:
    python scripts/clone_prod_to_local.py
    python scripts/clone_prod_to_local.py --only nations,characters,pops
    python scripts/clone_prod_to_local.py --exclude changes,hex_map_history,admin_visibility_logs
    python scripts/clone_prod_to_local.py --local-uri mongodb://localhost:27017/poa_local_debug

Requires a local MongoDB server already running and reachable (e.g.
`mongod --dbpath <some_folder>` in another terminal, or a local MongoDB
service). Reads MONGO_URI from .env as the READ-ONLY source — this script
never writes to that connection, only to --local-uri.

Indexes are not copied: app_core.ensure_mongo_indexes() creates everything
needed automatically the first time run_local_tick.py imports app_core
against the local database.
"""
import os
import sys
import argparse
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(override=True)

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

DEFAULT_LOCAL_URI = "mongodb://localhost:27017/poa_local_debug"
BATCH_SIZE = 1000


def _db_identity(uri, db_name):
    parsed = urlparse(uri)
    return (parsed.hostname, parsed.port, db_name)


def _looks_local(uri):
    host = (urlparse(uri).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-uri", default=os.getenv("LOCAL_MONGO_URI", DEFAULT_LOCAL_URI),
                         help=f"Target local MongoDB URI (default: {DEFAULT_LOCAL_URI})")
    parser.add_argument("--only", default=None,
                         help="Comma-separated collection names to copy (default: all)")
    parser.add_argument("--exclude", default="",
                         help="Comma-separated collection names to skip (e.g. large, tick-irrelevant ones)")
    parser.add_argument("--yes", action="store_true",
                         help="Don't prompt for confirmation before wiping the target database")
    args = parser.parse_args()

    source_uri = os.getenv("MONGO_URI")
    if not source_uri:
        print("ERROR: MONGO_URI not set (needed as the read-only source).")
        sys.exit(1)

    local_uri = args.local_uri
    if not _looks_local(local_uri):
        print(f"ERROR: --local-uri ({local_uri}) doesn't look like a local address "
              f"(expected localhost/127.0.0.1). Refusing to clone production data INTO "
              f"what might be another real database. Pass a genuinely local MongoDB URI.")
        sys.exit(1)

    source_parsed = urlparse(source_uri)
    source_db_name = source_parsed.path.lstrip('/').split('?')[0]
    local_parsed = urlparse(local_uri)
    local_db_name = local_parsed.path.lstrip('/').split('?')[0] or "poa_local_debug"

    if _db_identity(source_uri, source_db_name) == _db_identity(local_uri, local_db_name):
        print("ERROR: source and target resolve to the same host+database. Refusing to proceed.")
        sys.exit(1)

    print(f"Source (READ-ONLY): {source_parsed.hostname}/{source_db_name}")
    print(f"Target (WILL BE OVERWRITTEN): {local_parsed.hostname}:{local_parsed.port or 27017}/{local_db_name}")

    try:
        local_client = MongoClient(local_uri, serverSelectionTimeoutMS=3000)
        local_client.admin.command("ping")
    except ServerSelectionTimeoutError:
        print(f"\nERROR: can't reach a local MongoDB server at {local_uri}.")
        print("Start one first, e.g.: mongod --dbpath <some_local_folder>")
        sys.exit(1)

    if not args.yes:
        answer = input(f"\nThis will DROP all collections in the local '{local_db_name}' "
                        f"database and replace them with a copy of production data. Continue? [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)

    source_client = MongoClient(source_uri, serverSelectionTimeoutMS=30000)
    source_db = source_client[source_db_name]
    local_db = local_client[local_db_name]

    all_collections = source_db.list_collection_names()
    only = set(args.only.split(",")) if args.only else None
    exclude = {c for c in args.exclude.split(",") if c}
    collections = [c for c in all_collections if (only is None or c in only) and c not in exclude]

    if not collections:
        print("No collections matched --only/--exclude. Nothing to do.")
        return

    print(f"\nCopying {len(collections)} collection(s)...")
    start = time.time()
    for name in collections:
        t0 = time.time()
        local_db[name].drop()
        source_coll = source_db[name]
        count = 0
        batch = []
        for doc in source_coll.find({}).batch_size(BATCH_SIZE):
            batch.append(doc)
            if len(batch) >= BATCH_SIZE:
                local_db[name].insert_many(batch, ordered=False)
                count += len(batch)
                batch = []
        if batch:
            local_db[name].insert_many(batch, ordered=False)
            count += len(batch)
        print(f"  {name:30} {count:>7} docs in {time.time() - t0:6.1f}s")

    print(f"\nDone in {time.time() - start:.1f}s. Local database ready at {local_uri}")
    print("Run a test tick against it with: python scripts/run_local_tick.py --help")


if __name__ == "__main__":
    main()
