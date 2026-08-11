"""
Regression test for a real bug: the "Backup Database" step at the start of
tick()/era_tick() used to call backup_mongodb_async(), which spawns the
actual backup in a SEPARATE background thread and returns almost
immediately ("Backup process started in background..."). Since tick()/
era_tick() themselves already run in their own background thread
(run_tick_async/run_era_tick_async), this meant the backup thread and the
tick's own data-mutating logic ran CONCURRENTLY, racing to read/write the
same database — the backup could still be mid-dump of a collection the tick
had already started changing, producing an inconsistent, not-actually-
pre-tick snapshot.

The fix makes backup_database() call the synchronous backup_mongodb()
directly and wait for it to fully finish before returning, which tick()/
era_tick() already treat as a hard gate (return early if it reports
failure) before touching any data.
"""
import time
from unittest.mock import patch, MagicMock

import helpers.tick_helpers as th


class TestBackupDatabaseBlocksUntilComplete:
    def test_backup_database_waits_for_the_real_backup_to_finish(self):
        """Proves backup_database() is synchronous: if it merely kicked off
        a background thread (the old bug), this would return almost
        instantly regardless of how long the underlying backup takes."""
        def _slow_backup():
            time.sleep(0.15)
            return True, "ok"

        with patch.object(th, "backup_mongodb", side_effect=_slow_backup):
            start = time.time()
            success, message = th.backup_database()
            elapsed = time.time() - start

        assert success is True
        assert elapsed >= 0.14, (
            f"backup_database() returned after only {elapsed:.3f}s despite the "
            "backup taking 0.15s — it isn't actually waiting for completion"
        )

    def test_backup_database_propagates_failure(self):
        with patch.object(th, "backup_mongodb", return_value=(False, "disk full")):
            success, message = th.backup_database()

        assert success is False
        assert "disk full" in message


class TestTickBlocksOnBackupBeforeAnyOtherWork:
    def test_backup_completes_before_tick_reads_global_modifiers(self):
        """Integration-level check on tick() itself: the backup must be
        fully done before the very next thing tick() does (reading
        global_modifiers) even starts."""
        call_order = []

        def _slow_backup():
            time.sleep(0.1)
            call_order.append("backup_finished")
            return True, "ok"

        fake_mongo = MagicMock()

        def _record_global_modifiers_read(*args, **kwargs):
            call_order.append("global_modifiers_read")
            return None  # tick() handles a falsy global_modifiers fine (guarded by `if global_modifiers:`)

        fake_mongo.db.__getitem__.return_value.find_one.side_effect = _record_global_modifiers_read

        with patch.object(th, "backup_mongodb", side_effect=_slow_backup), \
             patch.object(th, "mongo", fake_mongo):
            th.tick({"run_Backup Database": "on"})

        # global_modifiers gets read more than once over the course of a full
        # tick (session counter, archival, ...) — what matters is that the
        # backup is the very first thing to finish, before any of those
        # reads happen.
        assert "global_modifiers_read" in call_order, "tick() never read global_modifiers — test setup is stale"
        assert call_order[0] == "backup_finished", (
            f"tick() didn't wait for the backup before proceeding: {call_order}"
        )

    def test_era_tick_also_blocks_on_backup(self):
        call_order = []

        def _slow_backup():
            time.sleep(0.1)
            call_order.append("backup_finished")
            return True, "ok"

        with patch.object(th, "backup_mongodb", side_effect=_slow_backup):
            result = th.era_tick({"run_Backup Database": "on"})

        assert call_order == ["backup_finished"]
        # era_tick() has no other steps requested, so it should return
        # normally (empty summary) rather than the early-return failure path.
        assert result == "" or result is not None
