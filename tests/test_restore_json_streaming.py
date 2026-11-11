"""
Regression test for app_core.iter_json_array_from_binary -- the streaming
JSON-array reader used by restore_mongodb() to read each collection's backup
file out of the zip without loading the whole thing into memory at once.

Bug: on retry after a JSONDecodeError (i.e. the current document isn't fully
buffered yet), the old code called ensure(chunk_size), which only reads more
data while the buffer holds LESS than chunk_size past pos. Once one full
chunk had already been buffered, that check was immediately satisfied, so no
further bytes were ever read and the same failing decode was retried forever
-- a true infinite loop with no progress, for any single document bigger
than one chunk. In production this is what stalled "changes" collection
restores (which store full before/after nation-document snapshots) partway
through, with memory usage pinned and climbing from whatever was in flight
when it got stuck.
"""
import io
import json
import time

from app_core import iter_json_array_from_binary


def test_document_larger_than_one_chunk_is_still_read_in_full():
    big_value = "x" * 500
    docs = [{"a": 1}, {"big": big_value}, {"c": 3}]
    payload = json.dumps(docs).encode("utf-8")

    start = time.monotonic()
    result = list(iter_json_array_from_binary(io.BytesIO(payload), chunk_size=16))
    elapsed = time.monotonic() - start

    assert result == docs
    assert elapsed < 5, "parser should not hang retrying the same failed decode"


def test_many_chunk_boundaries_within_a_single_document():
    docs = [{"blob": "y" * 10_000}]
    payload = json.dumps(docs).encode("utf-8")

    result = list(iter_json_array_from_binary(io.BytesIO(payload), chunk_size=64))

    assert result == docs


def test_multiple_documents_each_spanning_several_chunks():
    docs = [{"n": i, "pad": "z" * 300} for i in range(5)]
    payload = json.dumps(docs).encode("utf-8")

    result = list(iter_json_array_from_binary(io.BytesIO(payload), chunk_size=32))

    assert result == docs


def test_empty_array_yields_nothing():
    payload = json.dumps([]).encode("utf-8")
    assert list(iter_json_array_from_binary(io.BytesIO(payload), chunk_size=16)) == []


def test_truncated_document_raises_instead_of_hanging():
    payload = b'[{"a": 1}, {"b": '  # truncated mid-object, no closing

    start = time.monotonic()
    try:
        list(iter_json_array_from_binary(io.BytesIO(payload), chunk_size=8))
        raised = False
    except json.JSONDecodeError:
        raised = True
    elapsed = time.monotonic() - start

    assert raised
    assert elapsed < 5
