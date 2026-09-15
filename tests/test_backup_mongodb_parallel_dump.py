"""Tests for backup_mongodb()'s concurrent per-collection dump.

Context: the 2026-09-15 backup-slowness investigation found the dominant
cost was server round-trip time to fill the *first* batch of each
collection's query — a per-query stall on the order of tens of seconds,
largely independent of collection size. Dumping collections one at a time
means those stalls sum; dumping them concurrently (bounded thread pool, see
_DUMP_MAX_WORKERS in app_core.backup_mongodb) lets them overlap instead.
These tests prove: everything still gets dumped correctly, the pool
actually overlaps stalls, the pool is bounded, and a single failing
collection still aborts the whole backup (unchanged behavior from before
parallelization).
"""
import os
import io
import time
import glob
import json
import zipfile
import tempfile
import shutil
from unittest.mock import patch
from bson import ObjectId

import app_core


class FakeCursor:
    def __init__(self, docs, delay=0.0, track=None, name=None):
        self._docs = list(docs)
        self._delay = delay
        self._first = True
        self._track = track
        self._name = name

    def batch_size(self, n):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        if self._first:
            if self._track is not None:
                self._track.append((self._name, "start", time.time()))
            if self._delay:
                time.sleep(self._delay)
            self._first = False
        if not self._docs:
            raise StopIteration
        return self._docs.pop(0)


class FakeCollection:
    def __init__(self, docs, delay=0.0, track=None, name=None, raise_on_find=None):
        self._docs = docs
        self._delay = delay
        self._track = track
        self._name = name
        self._raise_on_find = raise_on_find

    def find(self, query):
        if self._raise_on_find:
            raise self._raise_on_find
        return FakeCursor(self._docs, delay=self._delay, track=self._track, name=self._name)


class FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def list_collection_names(self):
        return list(self._collections.keys())

    def __getitem__(self, name):
        return self._collections[name]


class FakeClient:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        return self._db


def _run_backup_in_tmp_dir(fake_db):
    """Runs backup_mongodb() with MongoClient faked out and the working
    directory redirected to a scratch temp dir, so the test never touches
    the real project's backups/ folder. Returns (success, message, zip_bytes_or_None) —
    the zip's raw bytes, read before the temp dir is cleaned up.
    """
    tmp_dir = tempfile.mkdtemp()
    try:
        with patch("pymongo.MongoClient", return_value=FakeClient(fake_db)), \
             patch("os.getcwd", return_value=tmp_dir), \
             patch.object(app_core, "send_backup_email", return_value=(True, "skipped")), \
             patch.object(app_core, "upload_to_s3", return_value=(False, "skipped, keep local zip for inspection")):
            success, message = app_core.backup_mongodb()

        zips = glob.glob(os.path.join(tmp_dir, "backups", "mongodb_backup_*.zip"))
        zip_bytes = None
        if zips:
            with open(zips[0], "rb") as f:
                zip_bytes = f.read()
        return success, message, zip_bytes
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class TestBackupMongodbDumpsEveryCollectionCorrectly:
    def test_all_collections_end_up_in_the_zip_with_correct_contents(self):
        doc_id = ObjectId()
        fake_db = FakeDB({
            "nations": FakeCollection([{"_id": doc_id, "name": "Testland", "owner": ObjectId()}]),
            "empty_collection": FakeCollection([]),
            "characters": FakeCollection([{"_id": ObjectId(), "name": "Alice"}, {"_id": ObjectId(), "name": "Bob"}]),
        })

        success, message, zip_bytes = _run_backup_in_tmp_dir(fake_db)

        assert success is True, message
        assert zip_bytes is not None, "expected the zip to remain on disk since S3 upload was mocked to fail"

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            nations_entry = next(n for n in names if n.endswith("nations.json"))
            characters_entry = next(n for n in names if n.endswith("characters.json"))
            empty_entry = next(n for n in names if n.endswith("empty_collection.json"))

            nations_data = json.loads(zf.read(nations_entry))
            assert len(nations_data) == 1
            assert nations_data[0]["name"] == "Testland"
            assert nations_data[0]["_id"] == str(doc_id)  # ObjectId converted to string

            characters_data = json.loads(zf.read(characters_entry))
            assert {d["name"] for d in characters_data} == {"Alice", "Bob"}

            assert json.loads(zf.read(empty_entry)) == []


class TestBackupMongodbDumpConcurrency:
    def test_collection_dumps_overlap_instead_of_running_strictly_serially(self):
        """Six collections, each with a simulated 0.15s "time to first
        batch" stall. Run one at a time that's 0.9s minimum; run with the
        actual bounded thread pool (_DUMP_MAX_WORKERS=6) they should mostly
        overlap. Generous threshold to avoid CI flakiness while still
        clearly distinguishing parallel from serial."""
        track = []
        delay = 0.15
        collections = {
            f"coll_{i}": FakeCollection([{"_id": ObjectId(), "n": i}], delay=delay, track=track, name=f"coll_{i}")
            for i in range(6)
        }
        fake_db = FakeDB(collections)

        start = time.time()
        success, message, zip_path = _run_backup_in_tmp_dir(fake_db)
        elapsed = time.time() - start

        assert success is True, message
        serial_time = delay * len(collections)
        assert elapsed < serial_time * 0.75, (
            f"dump took {elapsed:.2f}s, not meaningfully faster than the "
            f"{serial_time:.2f}s a fully serial dump would take — collections "
            f"don't appear to be running concurrently"
        )

    def test_pool_is_bounded_not_one_thread_per_collection(self):
        """More collections than _DUMP_MAX_WORKERS: proves the pool caps
        concurrency (checked via a shared counter of simultaneously-active
        dumps) rather than spawning unboundedly many threads."""
        import threading
        active = {"count": 0, "max_seen": 0}
        lock = threading.Lock()

        class TrackingCursor(FakeCursor):
            def __next__(self):
                if self._first:
                    with lock:
                        active["count"] += 1
                        active["max_seen"] = max(active["max_seen"], active["count"])
                    time.sleep(0.1)
                    with lock:
                        active["count"] -= 1
                return super().__next__()

        class TrackingCollection(FakeCollection):
            def find(self, query):
                return TrackingCursor(self._docs)

        fake_db = FakeDB({f"coll_{i}": TrackingCollection([{"_id": ObjectId()}]) for i in range(15)})

        success, message, zip_path = _run_backup_in_tmp_dir(fake_db)

        assert success is True, message
        assert active["max_seen"] <= 6, f"expected at most 6 concurrent dumps, saw {active['max_seen']}"
        assert active["max_seen"] > 1, "expected some real concurrency, not accidental full serialization"


class TestBackupMongodbAbortsOnCollectionFailure:
    def test_one_failing_collection_fails_the_whole_backup(self):
        fake_db = FakeDB({
            "nations": FakeCollection([{"_id": ObjectId(), "name": "Testland"}]),
            "broken": FakeCollection([], raise_on_find=RuntimeError("simulated connection drop")),
        })

        success, message, zip_path = _run_backup_in_tmp_dir(fake_db)

        assert success is False
        assert "Backup failed" in message
        assert "simulated connection drop" in message
