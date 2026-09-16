"""Regression test for give_tick_summary writing non-ASCII text.

Context: a local test tick (run against a real, non-mocked replica set via
scripts/run_local_tick.py, 2026-09-16) completed a full 57-step run and
reached the very last step — writing the tick summary to disk — only to
crash there with:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u012b'
    in position 331: character maps to <undefined>

open(path, 'w') without an explicit encoding defaults to the OS's
preferred locale encoding, which on Windows is a legacy codepage (cp1252
here) that can't represent every character a nation/character/culture
name might contain — turning an otherwise fully-successful tick into a
reported failure at the last possible moment. The same unguarded open()
pattern existed in app_core.backup_mongodb's per-collection dump and
archive_helpers.archive_old_changes's archive write; all three got the
same encoding='utf-8' fix.
"""
import os
from unittest.mock import patch

import helpers.tick_helpers as th


class TestGiveTickSummaryEncoding:
    def test_writes_non_ascii_summary_text_without_raising(self, mock_mongo, tmp_path):
        non_ascii_summary = "Nation event for Alīcia: gained a boon"  # ī, the exact
        # character class (Latin Extended-A) that broke cp1252 in the incident

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch("helpers.tick_helpers.os.getcwd", return_value=str(tmp_path)), \
             patch("helpers.tick_helpers.upload_to_s3") as upload:
            full_summary_path = th.give_tick_summary(non_ascii_summary, non_ascii_summary)

        # os.getenv("S3_BUCKET_NAME") isn't mocked away, so this only asserts
        # upload wasn't required to succeed for the write itself to work.
        assert os.path.exists(full_summary_path)
        with open(full_summary_path, "rb") as f:
            raw = f.read()
        assert raw.decode("utf-8") == non_ascii_summary
