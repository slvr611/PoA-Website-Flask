"""Tests for helpers/archive_helpers.py.

Context: the backup investigation (2026-09-15) found the `changes`
collection is by far the largest in the DB and directly drives how long
the nightly backup takes to dump. ARCHIVE_AFTER_N_SESSIONS controls its
steady-state size, so this pins the lowered threshold against silent
regressions and covers the archive/keep boundary.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.archive_helpers as ah


class TestArchiveAfterNSessionsThreshold:
    def test_threshold_is_five_not_the_old_twenty(self):
        """Guards against silently drifting back toward the old, much
        larger retention window that let `changes` balloon in size."""
        assert ah.ARCHIVE_AFTER_N_SESSIONS == 5


class TestArchiveOldChanges:
    def _make_change(self, session_number, status="Approved"):
        return {
            "_id": ObjectId(),
            "status": status,
            "session_number": session_number,
            "last_modified_time": "2026-01-01 00:00:00",
        }

    def test_archives_and_deletes_only_changes_older_than_the_cutoff(self, mock_mongo):
        current_session = 20
        cutoff = current_session - ah.ARCHIVE_AFTER_N_SESSIONS  # 15

        old_change = self._make_change(session_number=cutoff - 1)   # 14 -> archive
        boundary_change = self._make_change(session_number=cutoff)  # 15 -> keep (not strictly older)
        recent_change = self._make_change(session_number=current_session)  # 20 -> keep
        pending_change = self._make_change(session_number=cutoff - 1, status="Pending")  # never archived

        for doc in [old_change, boundary_change, recent_change, pending_change]:
            mock_mongo.db.changes.insert_one(doc)

        with patch("helpers.archive_helpers.mongo", mock_mongo), \
             patch("helpers.archive_helpers.upload_to_s3", return_value=(True, "ok")), \
             patch("builtins.open", MagicMock()), \
             patch("os.remove"):
            message = ah.archive_old_changes(current_session)

        remaining_ids = {d["_id"] for d in mock_mongo.db.changes.find({})}
        assert old_change["_id"] not in remaining_ids, "strictly-older change should have been deleted"
        assert boundary_change["_id"] in remaining_ids
        assert recent_change["_id"] in remaining_ids
        assert pending_change["_id"] in remaining_ids, "pending changes must never be auto-archived"
        assert "Archived 1 changes" in message

    def test_does_nothing_and_reports_when_nothing_is_old_enough(self, mock_mongo):
        current_session = 20
        mock_mongo.db.changes.insert_one(self._make_change(session_number=current_session))

        with patch("helpers.archive_helpers.mongo", mock_mongo), \
             patch("helpers.archive_helpers.upload_to_s3") as upload:
            message = ah.archive_old_changes(current_session)

        upload.assert_not_called()
        assert message == "No changes to archive."
        assert mock_mongo.db.changes.count_documents({}) == 1

    def test_does_not_delete_when_s3_upload_fails(self, mock_mongo):
        current_session = 20
        cutoff = current_session - ah.ARCHIVE_AFTER_N_SESSIONS
        old_change = self._make_change(session_number=cutoff - 1)
        mock_mongo.db.changes.insert_one(old_change)

        with patch("helpers.archive_helpers.mongo", mock_mongo), \
             patch("helpers.archive_helpers.upload_to_s3", return_value=(False, "S3 down")), \
             patch("builtins.open", MagicMock()), \
             patch("os.remove"):
            message = ah.archive_old_changes(current_session)

        assert "NOT deleted" in message
        assert mock_mongo.db.changes.count_documents({"_id": old_change["_id"]}) == 1

    def test_migration_step_stamps_missing_session_number_as_current_minus_one(self, mock_mongo):
        legacy_change = {
            "_id": ObjectId(), "status": "Approved", "last_modified_time": "2026-01-01 00:00:00",
        }
        mock_mongo.db.changes.insert_one(legacy_change)

        with patch("helpers.archive_helpers.mongo", mock_mongo), \
             patch("helpers.archive_helpers.upload_to_s3", return_value=(True, "ok")), \
             patch("builtins.open", MagicMock()), \
             patch("os.remove"):
            ah.archive_old_changes(current_session=20)

        stamped = mock_mongo.db.changes.find_one({"_id": legacy_change["_id"]})
        assert stamped is not None
        assert stamped["session_number"] == 19
