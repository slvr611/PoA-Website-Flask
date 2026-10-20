"""Tests for helpers/ai_review_helpers.py — the AI-assisted partial change
reviewer. Every test mocks the Anthropic client and Discord fetch; nothing
here makes a real network call.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from bson import ObjectId

import helpers.ai_review_helpers as arh
import helpers.change_helpers as ch


@pytest.fixture
def patch_ai_review(mock_mongo, fake_category_data):
    """Patch both change_helpers (get_change_drift_conflicts needs
    category_data to resolve the target collection) and ai_review_helpers
    to the same isolated in-memory database."""
    with patch.object(ch, "mongo", mock_mongo), \
         patch.object(ch, "category_data", fake_category_data), \
         patch.object(arh, "mongo", mock_mongo):
        yield


def _insert_change(db, **overrides):
    doc = {
        "_id": ObjectId(),
        "target_collection": "nations",
        "target": ObjectId(),
        "change_type": "Update",
        "status": "Pending",
        "before_requested_data": {"money": 10},
        "after_requested_data": {"money": 20},
        "request_reason": "see discord",
        "referenced_discord_links": ["https://discord.com/channels/1/2/3"],
        # Safely older than _AI_REVIEW_MIN_AGE_SECONDS by default, so tests
        # that don't care about freshness aren't tripped up by the claim
        # query's min-age gate — see TestClaimNextChangeForReview's own
        # dedicated freshness tests for that behavior specifically.
        "time_requested": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    doc.update(overrides)
    db["changes"].insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# get_ai_review_settings
# ---------------------------------------------------------------------------

class TestGetAiReviewSettings:
    def test_defaults_to_enabled_and_dry_run(self, test_db, patch_ai_review):
        assert arh.get_ai_review_settings() == {"enabled": True, "dry_run": True}

    def test_respects_stored_values(self, test_db, patch_ai_review):
        test_db["global_modifiers"].insert_one({
            "name": "global_modifiers", "ai_review_enabled": False, "ai_review_dry_run": False,
        })
        assert arh.get_ai_review_settings() == {"enabled": False, "dry_run": False}


# ---------------------------------------------------------------------------
# AI Reviewer player identity
# ---------------------------------------------------------------------------

class TestGetOrCreateAiReviewerPlayer:
    def test_creates_if_missing(self, test_db, patch_ai_review):
        player = arh._get_or_create_ai_reviewer_player()
        assert player["name"] == "AI Reviewer"
        assert test_db["players"].count_documents({"name": "AI Reviewer"}) == 1

    def test_returns_existing_without_duplicating(self, test_db, patch_ai_review):
        first = arh._get_or_create_ai_reviewer_player()
        second = arh._get_or_create_ai_reviewer_player()
        assert first["_id"] == second["_id"]
        assert test_db["players"].count_documents({"name": "AI Reviewer"}) == 1


# ---------------------------------------------------------------------------
# get_ai_reviewable_units — the hard drift-conflict exclusion
# ---------------------------------------------------------------------------

class TestGetAiReviewableUnits:
    def test_no_drift_returns_all_units(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10, "wood": 10}).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"money": 10, "wood": 10},
            after_requested_data={"money": 20, "wood": 50},
        )
        units = arh.get_ai_reviewable_units(change)
        assert {u["path"] for u in units} == {"money", "wood"}

    def test_drift_conflicted_unit_is_excluded(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": "tampered", "wood": 10}).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"money": "low", "wood": "low"},
            after_requested_data={"money": "high", "wood": "high"},
        )
        # "wood" live value still matches "low" (no drift); "money" drifted
        # to something matching neither before nor after.
        test_db["nations"].update_one({"_id": nation_id}, {"$set": {"wood": "low"}})
        units = arh.get_ai_reviewable_units(change)
        assert {u["path"] for u in units} == {"wood"}

    def test_nested_dict_sub_key_granularity_preserved(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({
            "name": "N", "stockpiles": {"wood": "tampered", "stone": "low"},
        }).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"stockpiles": {"wood": "low", "stone": "low"}},
            after_requested_data={"stockpiles": {"wood": "high", "stone": "high"}},
        )
        units = arh.get_ai_reviewable_units(change)
        assert {u["path"] for u in units} == {"stockpiles.stone"}


# ---------------------------------------------------------------------------
# build_review_prompt
# ---------------------------------------------------------------------------

class TestBuildReviewPrompt:
    def _units(self):
        return [{"path": "money", "before": 10, "after": 20}]

    def test_includes_target_and_reason(self):
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "reason text"}
        prompt = arh.build_review_prompt(change, [], self._units())
        assert "Testland" in prompt
        assert "nations" in prompt
        assert "reason text" in prompt

    def test_includes_successful_discord_message_content(self):
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "r"}
        messages = [{"url": "https://discord.com/x", "ok": True, "author": "player1", "content": "This explains it."}]
        prompt = arh.build_review_prompt(change, messages, self._units())
        assert "player1" in prompt
        assert "This explains it." in prompt

    def test_notes_failed_discord_fetch_without_treating_as_evidence(self):
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "r"}
        messages = [{"url": "https://discord.com/x", "ok": False, "error": "not_found"}]
        prompt = arh.build_review_prompt(change, messages, self._units())
        assert "could not be read" in prompt
        assert "not_found" in prompt

    def test_no_discord_messages_noted_explicitly(self):
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "r"}
        prompt = arh.build_review_prompt(change, [], self._units())
        assert "No Discord messages were linked" in prompt

    def test_includes_unit_path_and_values(self):
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "r"}
        prompt = arh.build_review_prompt(change, [], [{"path": "stockpiles.wood", "before": 10, "after": 50}])
        assert "stockpiles.wood" in prompt
        assert "10" in prompt
        assert "50" in prompt

    def test_instructs_the_model_to_call_the_tool(self):
        # tool_choice is no longer forced (see call_ai_reviewer's docstring
        # -- the API rejects forcing it on a model running with extended
        # thinking), so this instruction is the only thing steering the
        # model toward actually calling submit_change_review.
        change = {"target_display_name": "Testland", "target_collection": "nations", "request_reason": "r"}
        prompt = arh.build_review_prompt(change, [], self._units())
        assert arh._REVIEW_TOOL_NAME in prompt


# ---------------------------------------------------------------------------
# call_ai_reviewer
# ---------------------------------------------------------------------------

class TestGetAnthropicClient:
    """_get_anthropic_client — in particular the anthropic-workspace-id
    header, needed only for an organization-level API key (as opposed to
    one created inside a specific workspace) that isn't scoped to a
    workspace on its own; the Anthropic API otherwise rejects every
    request with a 400 until that header is present."""

    def setup_method(self):
        arh._anthropic_client = None

    def teardown_method(self):
        arh._anthropic_client = None

    def test_no_workspace_id_omits_the_header(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True), \
             patch.object(arh.anthropic, "Anthropic") as mock_anthropic:
            arh._get_anthropic_client()
        mock_anthropic.assert_called_once_with(api_key="sk-ant-test", default_headers=None)

    def test_workspace_id_set_adds_the_header(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_123"}, clear=True), \
             patch.object(arh.anthropic, "Anthropic") as mock_anthropic:
            arh._get_anthropic_client()
        mock_anthropic.assert_called_once_with(
            api_key="sk-ant-test", default_headers={"anthropic-workspace-id": "wrkspc_123"},
        )

    def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError):
                arh._get_anthropic_client()

    def test_client_is_cached_across_calls(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True), \
             patch.object(arh.anthropic, "Anthropic") as mock_anthropic:
            first = arh._get_anthropic_client()
            second = arh._get_anthropic_client()
        assert first is second
        mock_anthropic.assert_called_once()


class TestCallAiReviewer:
    def _fake_client_returning(self, verdicts):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {"verdicts": verdicts}
        response = MagicMock()
        response.content = [tool_block]
        client = MagicMock()
        client.messages.create.return_value = response
        return client

    def test_extracts_verdicts_from_tool_use_block(self):
        verdicts = [{"path": "money", "verdict": "approve", "justification": "explained", "discord_quote": "..."}]
        client = self._fake_client_returning(verdicts)
        with patch.object(arh, "_get_anthropic_client", return_value=client):
            result = arh.call_ai_reviewer({"request_reason": "r"}, [], [{"path": "money", "before": 1, "after": 2}])
        assert result == verdicts
        client.messages.create.assert_called_once()
        kwargs = client.messages.create.call_args.kwargs
        # tool_choice is deliberately NOT forced (left at the default,
        # "auto") -- the Anthropic API rejects a forced tool_choice
        # outright on a model running with extended thinking enabled, so
        # this must work regardless of which model AI_REVIEW_MODEL names.
        assert "tool_choice" not in kwargs
        assert kwargs["model"] == arh.AI_REVIEW_MODEL
        assert kwargs["tools"] == [arh._REVIEW_TOOL_SCHEMA]

    def test_raises_when_no_tool_use_block(self):
        response = MagicMock()
        response.content = []
        client = MagicMock()
        client.messages.create.return_value = response
        with patch.object(arh, "_get_anthropic_client", return_value=client):
            with pytest.raises(ValueError):
                arh.call_ai_reviewer({"request_reason": "r"}, [], [])


# ---------------------------------------------------------------------------
# ai_approve_change
# ---------------------------------------------------------------------------

class TestAiApproveChange:
    def test_no_approved_paths_only_writes_record(self, test_db, patch_ai_review):
        change = _insert_change(test_db)
        with patch.object(arh, "approve_change_as") as mock_approve:
            approved, error = arh.ai_approve_change(change["_id"], [], {"mode": "live", "units": []})
        mock_approve.assert_not_called()
        assert approved is False
        assert error is None
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["ai_reviewed"] is True
        assert len(stored["ai_review"]) == 1

    def test_tick_locked_blocks_without_creating_reviewer_player(self, test_db, patch_ai_review):
        change = _insert_change(test_db)
        with patch.object(arh, "is_tick_locked", return_value=True):
            approved, error = arh.ai_approve_change(change["_id"], ["money"], {"mode": "live"})
        assert approved is False
        assert error == "tick_locked"
        assert test_db["players"].count_documents({"name": "AI Reviewer"}) == 0

    def test_successful_approval_attributed_to_ai_reviewer(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10}).inserted_id
        change = _insert_change(test_db, target=nation_id)
        review_record = {"mode": "live", "units": [], "approved_paths": ["money"]}

        approved, error = arh.ai_approve_change(change["_id"], ["money"], review_record)

        assert approved is True
        assert error is None
        nation = test_db["nations"].find_one({"_id": nation_id})
        assert nation["money"] == 20
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["status"] == "Approved"
        reviewer = test_db["players"].find_one({"name": "AI Reviewer"})
        assert stored["approver"] == reviewer["_id"]
        assert stored["ai_reviewed"] is True
        assert review_record in stored["ai_review"]

    def test_rejection_does_not_write_the_review_record(self, test_db, patch_ai_review):
        # Non-numeric before/after -- numeric drift is tolerated by
        # check_no_other_changes regardless of what it drifted to, so an
        # int fixture here wouldn't actually produce a rejection.
        nation_id = test_db["nations"].insert_one({"name": "N", "money": "low"}).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"money": "low"}, after_requested_data={"money": "high"},
        )
        test_db["nations"].update_one({"_id": nation_id}, {"$set": {"money": "tampered"}})

        approved, error = arh.ai_approve_change(change["_id"], ["money"], {"mode": "live"})

        assert approved is False
        assert error is not None
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["status"] == "Pending"
        assert "ai_review" not in stored or stored["ai_review"] == []


# ---------------------------------------------------------------------------
# review_change — the orchestration
# ---------------------------------------------------------------------------

class TestReviewChange:
    def test_skips_non_update_change(self, test_db, patch_ai_review):
        change = _insert_change(test_db, change_type="Add")
        result = arh.review_change(change)
        assert result == {"skipped": "not_an_update_change"}
        assert "ai_review" not in test_db["changes"].find_one({"_id": change["_id"]})

    def test_skips_change_with_no_discord_links(self, test_db, patch_ai_review):
        change = _insert_change(test_db, referenced_discord_links=[])
        result = arh.review_change(change)
        assert result == {"skipped": "no_discord_links"}

    def test_skips_and_records_when_no_reviewable_units(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": "tampered"}).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"money": "low"}, after_requested_data={"money": "high"},
        )
        result = arh.review_change(change)
        assert result == {"skipped": "no_reviewable_units"}
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["ai_reviewed"] is True

    def test_dry_run_never_calls_ai_approve_change(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10}).inserted_id
        change = _insert_change(test_db, target=nation_id)
        verdicts = [{"path": "money", "verdict": "approve", "justification": "explained"}]
        with patch.object(arh, "fetch_discord_messages", return_value=[]), \
             patch.object(arh, "call_ai_reviewer", return_value=verdicts), \
             patch.object(arh, "ai_approve_change") as mock_approve:
            result = arh.review_change(change, dry_run=True)

        mock_approve.assert_not_called()
        assert result == {"dry_run": True, "approved_paths": ["money"]}
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["ai_reviewed"] is True
        assert stored["ai_review"][0]["mode"] == "dry_run"
        assert stored["status"] == "Pending"   # never actually touched

    def test_live_mode_calls_ai_approve_change_with_approved_paths_only(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10, "wood": 10}).inserted_id
        change = _insert_change(
            test_db, target=nation_id,
            before_requested_data={"money": 10, "wood": 10},
            after_requested_data={"money": 20, "wood": 50},
        )
        verdicts = [
            {"path": "money", "verdict": "approve", "justification": "explained"},
            {"path": "wood", "verdict": "leave", "justification": "not mentioned"},
        ]
        with patch.object(arh, "fetch_discord_messages", return_value=[]), \
             patch.object(arh, "call_ai_reviewer", return_value=verdicts), \
             patch.object(arh, "ai_approve_change", return_value=(True, None)) as mock_approve:
            result = arh.review_change(change, dry_run=False)

        mock_approve.assert_called_once()
        call_args = mock_approve.call_args.args
        assert call_args[0] == change["_id"]
        assert call_args[1] == ["money"]
        assert result == {"approved": True, "approved_paths": ["money"], "error": None}

    def test_hallucinated_path_not_in_candidates_is_filtered_out(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10}).inserted_id
        change = _insert_change(test_db, target=nation_id)
        verdicts = [{"path": "not_a_real_unit", "verdict": "approve", "justification": "??"}]
        with patch.object(arh, "fetch_discord_messages", return_value=[]), \
             patch.object(arh, "call_ai_reviewer", return_value=verdicts), \
             patch.object(arh, "ai_approve_change", return_value=(False, None)) as mock_approve:
            arh.review_change(change, dry_run=False)

        mock_approve.assert_called_once_with(change["_id"], [], mock_approve.call_args.args[2])

    def test_model_failure_is_recorded_not_raised(self, test_db, patch_ai_review):
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10}).inserted_id
        change = _insert_change(test_db, target=nation_id)
        with patch.object(arh, "fetch_discord_messages", return_value=[]), \
             patch.object(arh, "call_ai_reviewer", side_effect=RuntimeError("API down")):
            result = arh.review_change(change)

        assert "error" in result
        stored = test_db["changes"].find_one({"_id": change["_id"]})
        assert stored["ai_reviewed"] is True
        assert "API down" in stored["ai_review"][0]["error"]

    def test_dry_run_none_falls_back_to_global_setting(self, test_db, patch_ai_review):
        test_db["global_modifiers"].insert_one({"name": "global_modifiers", "ai_review_dry_run": False})
        nation_id = test_db["nations"].insert_one({"name": "N", "money": 10}).inserted_id
        change = _insert_change(test_db, target=nation_id)
        verdicts = [{"path": "money", "verdict": "leave", "justification": "x"}]
        with patch.object(arh, "fetch_discord_messages", return_value=[]), \
             patch.object(arh, "call_ai_reviewer", return_value=verdicts), \
             patch.object(arh, "ai_approve_change", return_value=(False, None)) as mock_approve:
            arh.review_change(change, dry_run=None)
        mock_approve.assert_called_once()   # live mode -- went through ai_approve_change, not the dry-run branch


# ---------------------------------------------------------------------------
# Claim/release — the atomic worker-claim mechanism
# ---------------------------------------------------------------------------

class TestClaimNextChangeForReview:
    def test_claims_oldest_eligible_pending_change(self, test_db, patch_ai_review):
        older = _insert_change(test_db, time_requested=datetime(2026, 1, 1, tzinfo=timezone.utc))
        _insert_change(test_db, time_requested=datetime(2026, 1, 2, tzinfo=timezone.utc))
        claimed = arh._claim_next_change_for_review()
        assert claimed["_id"] == older["_id"]
        assert claimed["ai_review_claimed_at"] is not None

    def test_does_not_reclaim_already_claimed_change(self, test_db, patch_ai_review):
        change = _insert_change(test_db)
        first = arh._claim_next_change_for_review()
        assert first["_id"] == change["_id"]
        second = arh._claim_next_change_for_review()
        assert second is None

    def test_does_not_claim_already_reviewed_change(self, test_db, patch_ai_review):
        _insert_change(test_db, ai_reviewed=True)
        assert arh._claim_next_change_for_review() is None

    def test_does_not_claim_non_update_or_linkless_changes(self, test_db, patch_ai_review):
        _insert_change(test_db, change_type="Add")
        _insert_change(test_db, referenced_discord_links=[])
        assert arh._claim_next_change_for_review() is None

    def test_release_clears_the_claim(self, test_db, patch_ai_review):
        change = _insert_change(test_db)
        arh._claim_next_change_for_review()
        arh._release_claim(change["_id"])
        reclaimed = arh._claim_next_change_for_review()
        assert reclaimed["_id"] == change["_id"]

    def test_does_not_claim_a_change_younger_than_the_min_age(self, test_db, patch_ai_review):
        # Closes the race where an admin's own "Save" button creates a
        # change via request_change(...) then approves it via
        # approve_change(...) as two separate, sequential writes (not one
        # atomic transaction like a tick's system_request_change +
        # system_approve_change) -- there's a brief real window where such
        # a change is visible as Pending before the admin's own approval
        # lands a moment later. A fresh change must not be claimable yet.
        _insert_change(test_db, time_requested=datetime.now(timezone.utc))
        assert arh._claim_next_change_for_review() is None

    def test_claims_a_change_once_past_the_min_age(self, test_db, patch_ai_review):
        old_enough = datetime.now(timezone.utc) - timedelta(seconds=arh._AI_REVIEW_MIN_AGE_SECONDS + 5)
        change = _insert_change(test_db, time_requested=old_enough)
        claimed = arh._claim_next_change_for_review()
        assert claimed["_id"] == change["_id"]


class TestStartAiReviewWorker:
    def test_second_call_in_same_process_is_a_no_op(self):
        arh._ai_review_worker_started = False
        try:
            with patch.object(arh.threading, "Thread") as thread_cls:
                arh.start_ai_review_worker()
                arh.start_ai_review_worker()
            thread_cls.assert_called_once()
        finally:
            arh._ai_review_worker_started = False
