"""Regression tests for generate_ai_character's already_calculated handling.

Context: a local test tick kept failing on "AI Vassal Concessions Payment
Tick" with a TickPartialCommitError, even after fixing the separate AI Mech
RP Tick bug. A diagnostic on _merge_pending_by_entity showed the real cause:
generate_ai_character's succession update (queued by ai_ensure_leader_tick
whenever a nation has no living leader) always queued with
already_calculated=False, while the nation's main "Tick Update for X" (queued
once per nation at the end of the nations loop) always uses True.
_merge_pending_by_entity ANDs already_calculated together when two queued
changes target the same entity, so the merged item silently lost its
already_calculated=True status whenever a nation needed a new ruler in the
same session — forcing a real check_no_other_changes comparison at commit
time that aborted the whole commit chunk (see helpers/change_helpers.py's
system_approve_change: skip_recalculation=True, sourced from
already_calculated, is what's supposed to bypass that check for a tick's own
same-entity collisions).

ai_ensure_leader_tick's old_nation is always old_nations[i] from tick()'s
nations loop, which already ran calculate_all_fields on it before any
NATION_TICK_FUNCTIONS dispatch — so its succession update is safe to mark
already_calculated=True, matching the existing precedent in
character_heal_then_death_tick's cross-cutting nation update.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th

_CHARACTER_SCHEMA = {
    "properties": {
        "positive_quirk": {"enum": ["None", "Brave"]},
        "negative_quirk": {"enum": ["None", "Greedy"]},
    }
}


def _nation(name="Test Nation"):
    """A nation with banked Heir Training bonuses — the simplest reliable
    way to make generate_ai_character take its "queue a nation-side update"
    branch (the other trigger, pop_selected, requires succession_type
    Elected/Strength plus a mocked pops.find with real pop data)."""
    return {
        "_id": ObjectId(),
        "name": name,
        "_calc_cache": {},
        "primary_race": "old-race-id",
        "primary_culture": "old-culture-id",
        "primary_religion": "old-religion-id",
        "heir_training_bonuses": {"rulership": 2},
    }


class TestGenerateAiCharacterAlreadyCalculatedDefault:
    def test_defaults_to_false_for_callers_with_a_raw_unrecalculated_org(self, mock_mongo):
        """generate_all_ai_rulers_tick passes a raw org straight from
        org_db.find(), never recalculated — the default must stay False so
        that path keeps forcing a real recalculation at commit time."""
        org = _nation()
        mock_mongo.db.characters.find_one = MagicMock(return_value=None)
        mock_mongo.db.pops.find = MagicMock(return_value=[])

        pending = []
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th.generate_ai_character(org, {}, _CHARACTER_SCHEMA, pending=pending)

        nation_entries = [p for p in pending if p["data_type"] == "nations"]
        assert len(nation_entries) == 1
        assert nation_entries[0]["already_calculated"] is False


class TestAiEnsureLeaderTickPassesAlreadyCalculated:
    def test_succession_change_is_marked_already_calculated(self, mock_mongo):
        org = _nation()
        mock_mongo.db.characters.find_one = MagicMock(return_value=None)
        mock_mongo.db.pops.find = MagicMock(return_value=[])

        pending = []
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th.ai_ensure_leader_tick(org, dict(org), {}, pending=pending)

        nation_entries = [p for p in pending if p["data_type"] == "nations"]
        assert len(nation_entries) == 1, "expected exactly one queued succession update for the nation"
        assert nation_entries[0]["already_calculated"] is True

    def test_merging_succession_onto_the_main_tick_update_preserves_already_calculated(self, mock_mongo):
        """The exact failure scenario: ai_ensure_leader_tick's succession
        update and the main per-nation "Tick Update for X" both target the
        same nation in the same tick and get merged. The merged item must
        stay already_calculated=True (skip_recalculation at commit time),
        not silently downgrade to False and trigger a real
        check_no_other_changes comparison."""
        org = _nation()
        mock_mongo.db.characters.find_one = MagicMock(return_value=None)
        mock_mongo.db.pops.find = MagicMock(return_value=[])

        pending = []
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th.ai_ensure_leader_tick(org, dict(org), {}, pending=pending)

        # Simulate the main bulk "Tick Update for X" queued after the whole
        # nations loop finishes, exactly like tick() does.
        th._queue_change(
            pending,
            data_type="nations",
            item_id=org["_id"],
            change_type="Update",
            before_data=org,
            after_data=dict(org, money=999),
            reason=f"Tick Update for {org['name']}",
            already_calculated=True,
        )

        nation_items = [p for p in pending if p["data_type"] == "nations"]
        assert len(nation_items) == 2, "both the succession and the main update should be queued separately pre-merge"

        merged = th._merge_pending_by_entity(nation_items)
        assert len(merged) == 1
        assert merged[0]["already_calculated"] is True, (
            "merging the succession update onto the main Tick Update must not "
            "downgrade already_calculated to False"
        )
