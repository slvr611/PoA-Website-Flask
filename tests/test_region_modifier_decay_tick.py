"""
Regression test for a reported gap: region modifiers never expired. Nations
and characters both run modifier_decay_tick (helpers/tick_helpers.py) every
session via "Nation Modifier Decay Tick"/"Character Modifier Decay Tick",
but regions — which have their own real `modifiers` array (see
json-data/schemas/regions.json, and _forced_flee_destination_for_region,
which already reads it for effect) — had no equivalent tick at all. A region
modifier given a finite duration stayed active forever.

Fixed by adding REGION_TICK_FUNCTIONS = {"Region Modifier Decay Tick":
modifier_decay_tick} and a per-region loop in tick(), mirroring the existing
faction/merchant/mercenary/market loops exactly (regions have no
"calculated" schema fields, so unlike those, there's no calculate_all_fields
recalculation step needed before running the tick).

The end-to-end tests exercise the real tick() -> _queue_change ->
_commit_pending_changes -> system_approve_change commit path, following the
exact pattern test_tick_atomicity.py already established for this: mongomock
doesn't implement real sessions (mongo.cx.start_session() raises
NotImplementedError), so mongo.cx is patched to a fake whose
with_transaction(callback) just calls callback(None) directly, letting the
underlying find/insert/update calls run against mongomock unchanged.
"""
from unittest.mock import MagicMock, patch

import helpers.tick_helpers as th
from app_core import category_data


class _FakeSession:
    def with_transaction(self, callback):
        return callback(None)


def _patched(mock_mongo):
    return (
        patch("helpers.tick_helpers.mongo", mock_mongo),
        patch("helpers.change_helpers.mongo", mock_mongo),
        patch("helpers.archive_helpers.mongo", mock_mongo),
        patch("helpers.change_helpers.propagate_updates"),
        patch.object(mock_mongo, "cx", MagicMock(start_session=MagicMock(
            return_value=MagicMock(__enter__=lambda s: _FakeSession(), __exit__=lambda *a: False)
        ))),
    )


def _seed(test_db, modifiers):
    test_db["players"].insert_one({"name": "System"})
    test_db["global_modifiers"].insert_one({"name": "global_modifiers", "session_counter": 1})
    test_db["regions"].insert_one({"name": "Testlands", "modifiers": modifiers})


class TestRegionModifierDecayTickIsRegistered:
    def test_registered_in_region_tick_functions(self):
        assert th.REGION_TICK_FUNCTIONS.get("Region Modifier Decay Tick") is th.modifier_decay_tick

    def test_exposed_to_the_admin_tick_helper_checkbox_list(self):
        import importlib
        tr = importlib.import_module("routes.tick_routes")
        assert "Region Modifier Decay Tick" in tr.REGION_TICK_FUNCTIONS


class TestRegionModifierDecayTickEndToEnd:
    def test_finite_duration_modifier_counts_down(self, test_db, monkeypatch):
        monkeypatch.setitem(category_data["regions"], "database", test_db["regions"])
        _seed(test_db, [{"_id": "m1", "modifier_type": "stability", "value": 1, "duration": 3}])

        mock_mongo = MagicMock(db=test_db)
        patches = _patched(mock_mongo)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            th.tick({"run_Region Modifier Decay Tick": "on"})

        region = test_db["regions"].find_one({"name": "Testlands"})
        assert region["modifiers"][0]["duration"] == 2

    def test_modifier_expires_and_is_removed_at_zero_duration(self, test_db, monkeypatch):
        monkeypatch.setitem(category_data["regions"], "database", test_db["regions"])
        _seed(test_db, [
            {"_id": "m1", "modifier_type": "stability", "value": 1, "duration": 1},
            {"_id": "m2", "modifier_type": "stability", "value": 1, "duration": -1},
        ])

        mock_mongo = MagicMock(db=test_db)
        patches = _patched(mock_mongo)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            th.tick({"run_Region Modifier Decay Tick": "on"})

        region = test_db["regions"].find_one({"name": "Testlands"})
        remaining_ids = [m["_id"] for m in region["modifiers"]]
        assert remaining_ids == ["m2"], "the duration-1 modifier must expire and be dropped this tick"

    def test_permanent_duration_negative_one_never_decays(self, test_db, monkeypatch):
        monkeypatch.setitem(category_data["regions"], "database", test_db["regions"])
        _seed(test_db, [{"_id": "m1", "modifier_type": "stability", "value": 1, "duration": -1}])

        mock_mongo = MagicMock(db=test_db)
        patches = _patched(mock_mongo)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            th.tick({"run_Region Modifier Decay Tick": "on"})

        region = test_db["regions"].find_one({"name": "Testlands"})
        assert region["modifiers"][0]["duration"] == -1

    def test_not_selecting_the_checkbox_leaves_regions_untouched(self, test_db, monkeypatch):
        """Selecting no (or an unrelated) tick step must not touch regions."""
        monkeypatch.setitem(category_data["regions"], "database", test_db["regions"])
        _seed(test_db, [{"_id": "m1", "modifier_type": "stability", "value": 1, "duration": 3}])

        mock_mongo = MagicMock(db=test_db)
        patches = _patched(mock_mongo)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            th.tick({})

        region = test_db["regions"].find_one({"name": "Testlands"})
        assert region["modifiers"][0]["duration"] == 3
