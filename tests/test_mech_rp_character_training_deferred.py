"""Regression tests for _apply_character_training's move to the deferred
_queue_change pattern.

Context: a local test tick (2026-09-16, run via scripts/run_local_tick.py
against a real replica set) produced dozens of "system_approve_change
blocked ... check_no_other_changes" messages during "AI Mech RP Tick", and
a diagnostic added to check_no_other_changes showed the live character
document's `modifiers` field matched neither the queued before_data nor
after_data. Root cause: _apply_character_training committed its modifier
addition immediately via system_request_change/system_approve_change
instead of deferring through _queue_change. select_mech_rps can attempt up
to MAX_ATTEMPTS_PER_SESSION per nation and can pick the same
acting_character more than once; the second attempt's immediate commit
used a before_data snapshot that predated the first attempt's already-
applied write, so check_no_other_changes correctly (but destructively)
rejected it — silently dropping that Character Training gain. Worse, the
resulting system_approve_change dependency-propagation cascade was writing
to OTHER entities (e.g. the character's nation) mid-compute-phase,
corrupting their own later deferred commits too (the AI Vassal Concessions
Payment Tick nation-commit failures from the same investigation).
"""
from unittest.mock import patch

import helpers.mech_rp_helpers as mrp
import helpers.tick_helpers as th


def _character(char_id, modifiers=None):
    return {
        "_id": char_id,
        "name": "Test Character",
        "rulership": 2,
        "rulership_cap": 4,
        "modifiers": modifiers or [],
    }


class TestApplyCharacterTrainingDeferred:
    def test_pending_none_falls_back_to_immediate_commit(self):
        """Preserves the original behavior for any caller not part of a
        tick's deferred-commit flow (e.g. a direct/manual invocation)."""
        character = _character("char-1")
        with patch("helpers.tick_helpers.system_request_change", return_value="change-1") as req, \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            result = mrp._apply_character_training(
                {}, {}, {}, {"name": "Training"}, magnitude=2, target_resource=None,
                need_weights={}, state={}, acting_character=character, stat_used="rulership",
                pending=None,
            )
        assert "gained 2 rulership" in result
        req.assert_called_once()
        approve.assert_called_once_with("change-1", skip_recalculation=False)

    def test_pending_list_defers_instead_of_committing_immediately(self):
        character = _character("char-1")
        pending = []
        with patch("helpers.tick_helpers.system_request_change") as req, \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            result = mrp._apply_character_training(
                {}, {}, {}, {"name": "Training"}, magnitude=2, target_resource=None,
                need_weights={}, state={}, acting_character=character, stat_used="rulership",
                pending=pending,
            )
        assert "gained 2 rulership" in result
        req.assert_not_called()
        approve.assert_not_called()
        assert len(pending) == 1
        assert pending[0]["data_type"] == "characters"
        assert pending[0]["item_id"] == "char-1"
        assert pending[0]["change_type"] == "Update"
        new_modifiers = pending[0]["after_data"]["modifiers"]
        assert len(new_modifiers) == 1
        assert new_modifiers[0]["attribute"] == "rulership"
        assert new_modifiers[0]["value"] == 2

    def test_two_attempts_against_the_same_character_both_survive_merge(self):
        """The exact failure scenario: select_mech_rps picks the same
        acting_character for two different attempts within one session.
        Both modifier additions must survive _merge_pending_by_entity
        instead of the second one being lost."""
        character = _character("char-1")
        pending = []
        mrp._apply_character_training(
            {}, {}, {}, {"name": "Training A"}, magnitude=2, target_resource=None,
            need_weights={}, state={}, acting_character=character, stat_used="rulership",
            pending=pending,
        )
        # Second attempt still sees the ORIGINAL character snapshot (no live
        # DB write happened), exactly like the real attempt loop.
        mrp._apply_character_training(
            {}, {}, {}, {"name": "Training B"}, magnitude=1, target_resource=None,
            need_weights={}, state={}, acting_character=character, stat_used="cunning",
            pending=pending,
        )

        assert len(pending) == 2  # not yet merged — that happens at commit time
        merged = th._merge_pending_by_entity(pending)
        assert len(merged) == 1, "both attempts target the same character and must merge into one change"
        merged_modifiers = merged[0]["after_data"]["modifiers"]
        attributes_granted = {m["attribute"] for m in merged_modifiers if m.get("source", "").startswith("Mech RP")}
        assert attributes_granted == {"rulership", "cunning"}, (
            f"expected both training attempts' modifiers to survive the merge, got {merged_modifiers}"
        )


class TestAiMechRpTickIsPendingAware:
    def test_registered_in_pending_aware_tick_functions(self):
        assert mrp.ai_mech_rp_tick in th._PENDING_AWARE_TICK_FUNCTIONS

    def test_dispatch_passes_pending_through_to_select_mech_rps(self):
        old_nation = {"_id": "n1", "name": "Testland", "temperament": "Aggressive"}
        new_nation = dict(old_nation)
        pending = []
        with patch.object(mrp, "select_mech_rps", return_value=([], [])) as select:
            th._dispatch(mrp.ai_mech_rp_tick, pending, old_nation, new_nation, {})
        assert select.call_args.kwargs.get("pending") is pending
