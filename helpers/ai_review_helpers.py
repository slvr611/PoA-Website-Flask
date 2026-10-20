"""AI-assisted partial review of Pending changes.

For each Pending Update change that links at least one Discord message in
its reason, asks Claude to judge — per unit (see
helpers.change_helpers.flatten_change_units) — whether that specific unit
is sufficiently explained by the reason text and the linked message(s).
Sufficiently-explained units are approved immediately, through the exact
same transactional partial-approval path a human admin's checkbox
submission uses (helpers.change_helpers.approve_change_as). Everything else
is simply left Pending — the AI never denies and never approves a unit
that's currently flagged by a live drift conflict, regardless of what the
model says about it; that's for a human.

See the design doc (C:\\Users\\orion\\.claude\\plans\\i-need-to-create-happy-zephyr.md
at the time this was written) for the full rationale and rollout plan.
"""
import os
import threading
import traceback
from datetime import datetime, timedelta, timezone
from time import sleep

import anthropic
from pymongo import ReturnDocument

from app_core import mongo, extract_discord_links
from helpers.change_helpers import (
    flatten_change_units,
    get_change_drift_conflicts,
    approve_change_as,
    is_tick_locked,
    run_in_transaction_with_retry,
)
from helpers.discord_fetch_helpers import fetch_discord_messages


AI_REVIEW_MODEL = os.getenv("AI_REVIEW_MODEL", "claude-sonnet-5-5")

_REVIEW_TOOL_NAME = "submit_change_review"
_REVIEW_TOOL_SCHEMA = {
    "name": _REVIEW_TOOL_NAME,
    "description": (
        "Submit a verdict for every unit of the pending change listed in the "
        "prompt. Each unit must appear exactly once, using its exact path "
        "string copied verbatim from the input."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The exact unit path being judged, copied verbatim from the input.",
                        },
                        "verdict": {
                            "type": "string",
                            "enum": ["approve", "leave"],
                            "description": (
                                "\"approve\" only if the reason and/or linked Discord "
                                "messages sufficiently and specifically explain this "
                                "exact unit. \"leave\" otherwise, including whenever unsure."
                            ),
                        },
                        "justification": {
                            "type": "string",
                            "description": "One or two sentences explaining the verdict.",
                        },
                        "discord_quote": {
                            "type": "string",
                            "description": (
                                "A short quote or close paraphrase from a linked Discord "
                                "message supporting an \"approve\" verdict. Empty string "
                                "if the verdict is \"leave\" or the reason text alone "
                                "(no Discord message) was the basis for approval."
                            ),
                        },
                    },
                    "required": ["path", "verdict", "justification"],
                },
            },
        },
        "required": ["verdicts"],
    },
}


class _AiApprovalAborted(Exception):
    """Raised inside ai_approve_change's transaction callback to abort
    cleanly when approve_change_as reports a rejection — see its own
    docstring for why this can't just rely on approve_change_as's returned
    (False, error) tuple alone to roll back whatever's already happened in
    the shared transaction."""


# ---------------------------------------------------------------------------
# Settings — a kill switch and a dry-run flag, both stored on the same
# global_modifiers document revert_warning already lives on (see
# routes/base_routes.py's toggle_revert_warning for the pattern this
# mirrors). Ships defaulted to enabled+dry_run: the worker reviews real
# traffic and records full verdicts, but never actually approves anything,
# until an admin flips dry_run off.
# ---------------------------------------------------------------------------

def get_ai_review_settings():
    doc = mongo.db.global_modifiers.find_one(
        {"name": "global_modifiers"}, {"ai_review_enabled": 1, "ai_review_dry_run": 1},
    ) or {}
    return {
        "enabled": doc.get("ai_review_enabled", True),
        "dry_run": doc.get("ai_review_dry_run", True),
    }


# ---------------------------------------------------------------------------
# AI Reviewer identity — mirrors change_helpers.py's "System" player
# pattern (used by system_request_change/system_approve_change), kept
# separate so AI-made approvals are distinguishable in the audit trail
# from both human admins and the tick system. Unlike "System", this is
# created on demand the first time it's needed rather than requiring a
# separate manual seeding step.
# ---------------------------------------------------------------------------

def _get_or_create_ai_reviewer_player():
    existing = mongo.db.players.find_one({"name": "AI Reviewer"})
    if existing:
        return existing
    result = mongo.db.players.insert_one({"name": "AI Reviewer", "is_ai_reviewer": True})
    return mongo.db.players.find_one({"_id": result.inserted_id})


# ---------------------------------------------------------------------------
# Candidate units — flatten_change_units, with any unit currently flagged
# by a live drift conflict removed BEFORE the model ever sees it. This is
# a hard rule, not something the model is asked to judge: a field that's
# already drifted since the change was requested is for a human, full stop.
# ---------------------------------------------------------------------------

def get_ai_reviewable_units(change):
    before_data = change.get("before_requested_data") or {}
    after_data = change.get("after_requested_data") or {}
    units = flatten_change_units(before_data, after_data)
    conflicted_paths = {c["path"] for c in get_change_drift_conflicts(change)}
    return [u for u in units if u["path"] not in conflicted_paths]


# ---------------------------------------------------------------------------
# Prompt construction and the model call
# ---------------------------------------------------------------------------

def build_review_prompt(change, discord_messages, candidate_units):
    parts = [
        f"Target: {change.get('target_display_name') or change.get('target')} "
        f"({change.get('target_collection')})",
        f"Request reason (as submitted by the player):\n{change.get('request_reason') or '(empty)'}",
    ]

    if discord_messages:
        lines = ["Linked Discord messages:"]
        for i, msg in enumerate(discord_messages, 1):
            if msg.get("ok"):
                image_count = sum(
                    1 for a in (msg.get("attachments") or [])
                    if (a.get("content_type") or "").startswith("image/")
                )
                image_note = f" [{image_count} image attachment{'s' if image_count != 1 else ''} shown below]" if image_count else ""
                lines.append(
                    f"  [{i}] ({msg.get('url')}) by {msg.get('author') or 'unknown'}:{image_note}\n"
                    f"      {msg.get('content') or '(empty message)'}"
                )
            else:
                lines.append(
                    f"  [{i}] ({msg.get('url')}) could not be read ({msg.get('error')}) — "
                    f"treat this link as unavailable, not as evidence either way."
                )
        parts.append("\n".join(lines))
    else:
        parts.append("No Discord messages were linked in the reason field.")

    unit_lines = ["Units of this change to judge (approve ONLY ones sufficiently explained above):"]
    for unit in candidate_units:
        unit_lines.append(f"  - path: {unit['path']}\n    before: {unit['before']!r}\n    after: {unit['after']!r}")
    parts.append("\n".join(unit_lines))

    parts.append(
        "For each unit listed above, decide \"approve\" only if the request reason "
        "and/or linked Discord messages sufficiently and specifically explain that "
        "exact change. If a unit isn't addressed, is only vaguely addressed, or "
        "you're not confident, the verdict must be \"leave\" — leaving something "
        "Pending for human review is always safe; approving something unexplained "
        "is not. Every unit listed above must appear exactly once in your "
        "response, using the exact path string given. "
        f"Respond ONLY by calling the {_REVIEW_TOOL_NAME} tool with your verdicts — "
        "do not respond with plain text."
    )
    return "\n\n".join(parts)


_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        # An organization-level API key (as opposed to one created inside a
        # specific workspace) isn't scoped to a workspace on its own —
        # Anthropic's API then requires the anthropic-workspace-id header
        # on every request to say which workspace to use/bill. Only needed
        # for that kind of key; leave ANTHROPIC_WORKSPACE_ID unset for a
        # workspace-scoped key.
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
        default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        _anthropic_client = anthropic.Anthropic(api_key=api_key, default_headers=default_headers)
    return _anthropic_client


# A runaway number of image attachments (e.g. a message with a dozen
# screenshots) would inflate cost well beyond what reviewing one change
# should ever take — bounded defensively, same spirit as the batch/poll
# size bounds elsewhere in this module.
_MAX_IMAGES_PER_REVIEW = 10


def _collect_image_blocks(discord_messages):
    """Image content blocks (Anthropic's URL-sourced image type — Discord's
    CDN attachment URLs are directly fetchable, no download/base64-encode
    needed on this end) for every image attachment across all successfully
    fetched discord_messages, in message order, capped at
    _MAX_IMAGES_PER_REVIEW."""
    blocks = []
    for msg in discord_messages:
        if not msg.get("ok"):
            continue
        for attachment in (msg.get("attachments") or []):
            if len(blocks) >= _MAX_IMAGES_PER_REVIEW:
                return blocks
            if (attachment.get("content_type") or "").startswith("image/"):
                blocks.append({"type": "image", "source": {"type": "url", "url": attachment["url"]}})
    return blocks


def call_ai_reviewer(change, discord_messages, candidate_units):
    """Call the model for structured tool output — one verdict per
    candidate unit. Raises on any API/parsing failure, including the model
    not calling the tool at all; the caller (review_change) turns that
    into a failed, recorded review attempt rather than letting it
    propagate and abort a whole worker batch — exactly as safe as any
    other failure to get a usable verdict.

    tool_choice is deliberately left at its default (auto) rather than
    forced ("tool"/"any") — Anthropic's API rejects forced tool_choice
    outright on a model running with extended thinking enabled ("tool_choice:
    type 'tool' and 'any' are not supported for this model"), and this
    needs to work across whichever model AI_REVIEW_MODEL is configured to.
    build_review_prompt's own closing instruction is relied on instead to
    get the tool called.
    """
    client = _get_anthropic_client()
    prompt = build_review_prompt(change, discord_messages, candidate_units)
    content = [{"type": "text", "text": prompt}]
    content.extend(_collect_image_blocks(discord_messages))
    response = client.messages.create(
        model=AI_REVIEW_MODEL,
        max_tokens=4096,
        tools=[_REVIEW_TOOL_SCHEMA],
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input.get("verdicts", [])
    raise ValueError("Model response contained no tool_use block")


# ---------------------------------------------------------------------------
# Approval — the AI-reviewer counterpart to a human's partial-approval
# checkbox submission.
# ---------------------------------------------------------------------------

def ai_approve_change(change_id, approved_paths, review_record):
    """Approve `approved_paths` on behalf of the AI Reviewer identity,
    attaching `review_record` to the change's ai_review history in the
    SAME transaction as the approval — the approval and the record
    describing it are never inconsistent with each other.

    If `approved_paths` is empty, only the record is written (no approval
    call at all — approve_change_as(..., selected_paths=None) would wrongly
    mean "approve everything", not "approve nothing").

    Returns (approved: bool, error: str|None).
    """
    if not approved_paths:
        mongo.db.changes.update_one(
            {"_id": change_id},
            {"$push": {"ai_review": review_record}, "$set": {"ai_reviewed": True}},
        )
        return False, None

    if is_tick_locked():
        return False, "tick_locked"

    approver = _get_or_create_ai_reviewer_player()

    def _do(session):
        approved, error = approve_change_as(change_id, approver, selected_paths=approved_paths, session=session)
        if not approved:
            raise _AiApprovalAborted(error or "approval_rejected")
        mongo.db.changes.update_one(
            {"_id": change_id},
            {"$push": {"ai_review": review_record}, "$set": {"ai_reviewed": True}},
            session=session,
        )
        return True

    try:
        return run_in_transaction_with_retry(_do), None
    except _AiApprovalAborted as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Orchestration for one change
# ---------------------------------------------------------------------------

def _mark_reviewed_with_no_approval(change_id, mode, note):
    mongo.db.changes.update_one(
        {"_id": change_id},
        {"$push": {"ai_review": {
            "timestamp": datetime.now(timezone.utc), "mode": mode, "model": None,
            "units": [], "approved_paths": [], "discord_messages": [], "note": note, "error": None,
        }}, "$set": {"ai_reviewed": True}},
    )


def review_change(change, dry_run=None):
    """Review one Pending Update change. Never raises — any failure
    (Discord fetch, the API call, a malformed response) is recorded on the
    change as a failed review attempt rather than propagating, so one bad
    change never aborts a worker's whole batch.

    `dry_run`: when True, the model is still asked and a full ai_review
    record is still written, but ai_approve_change is never actually
    called. Defaults to the current global_modifiers setting (the worker's
    normal path); pass True/False explicitly for the manual "Request AI
    Review" re-trigger and for tests.

    Returns a result dict describing what happened.
    """
    change_id = change["_id"]

    if change.get("change_type") != "Update":
        return {"skipped": "not_an_update_change"}

    if dry_run is None:
        dry_run = get_ai_review_settings()["dry_run"]
    mode = "dry_run" if dry_run else "live"

    # Always re-extracted from request_reason rather than trusted from the
    # stored referenced_discord_links snapshot — that field is only
    # populated for changes requested after it was added (see
    # request_change/system_request_change), so relying on it alone
    # silently skipped every older still-Pending change, even ones whose
    # reason plainly links a message. request_reason itself is immutable
    # once a change is requested (there's no "edit a pending change"
    # feature), so re-extracting from it is always correct, not just a
    # fallback for old data.
    discord_links = extract_discord_links(change.get("request_reason"))
    if not discord_links:
        _mark_reviewed_with_no_approval(change_id, mode, "no_discord_links")
        return {"skipped": "no_discord_links"}

    candidate_units = get_ai_reviewable_units(change)
    if not candidate_units:
        _mark_reviewed_with_no_approval(change_id, mode, "no_reviewable_units")
        return {"skipped": "no_reviewable_units"}

    discord_messages = fetch_discord_messages(discord_links)

    try:
        verdicts = call_ai_reviewer(change, discord_messages, candidate_units)
    except Exception as e:
        traceback.print_exc()
        mongo.db.changes.update_one(
            {"_id": change_id},
            {"$push": {"ai_review": {
                "timestamp": datetime.now(timezone.utc), "mode": mode, "model": AI_REVIEW_MODEL,
                "units": [], "approved_paths": [],
                "discord_messages": [{"url": m["url"], "ok": m["ok"], "error": m.get("error")} for m in discord_messages],
                "error": f"{type(e).__name__}: {e}",
            }}, "$set": {"ai_reviewed": True}},
        )
        return {"error": str(e)}

    candidate_paths = {u["path"] for u in candidate_units}
    approved_paths = [
        v["path"] for v in verdicts
        if isinstance(v, dict) and v.get("verdict") == "approve" and v.get("path") in candidate_paths
    ]

    review_record = {
        "timestamp": datetime.now(timezone.utc),
        "mode": mode,
        "model": AI_REVIEW_MODEL,
        "units": verdicts,
        "approved_paths": approved_paths,
        "discord_messages": [{"url": m["url"], "ok": m["ok"], "error": m.get("error")} for m in discord_messages],
        "error": None,
    }

    if dry_run:
        mongo.db.changes.update_one(
            {"_id": change_id},
            {"$push": {"ai_review": review_record}, "$set": {"ai_reviewed": True}},
        )
        return {"dry_run": True, "approved_paths": approved_paths}

    approved, error = ai_approve_change(change_id, approved_paths, review_record)
    return {"approved": approved, "approved_paths": approved_paths, "error": error}


# ---------------------------------------------------------------------------
# Background worker — same shape as
# helpers.change_helpers.start_propagation_outbox_worker: idempotent,
# started once from app.py (never at this module's own import time, so
# scripts/tests that import ai_review_helpers directly never spawn a
# persistent polling thread), a daemon thread with a traceback-caught loop.
# ---------------------------------------------------------------------------

_AI_REVIEW_POLL_INTERVAL_SECONDS = 30

# An admin's own "Save" button (e.g. data_item_routes.py's
# data_item_edit_approve) calls request_change(...) then approve_change(...)
# as two separate, sequential writes in one request — not one atomic
# transaction (unlike a tick's system_request_change + system_approve_change,
# which share a single transaction and so are NEVER externally visible as
# Pending at all — nothing to guard there). That leaves a brief real window
# where the change exists as Pending before the admin's own approval lands.
# Refusing to claim anything younger than this closes it without touching
# every save route that repeats this pattern: a change an admin approves
# themselves finishes well under a second, while a change actually meant to
# sit Pending for review has no deadline at all.
_AI_REVIEW_MIN_AGE_SECONDS = 30


def _claim_next_change_for_review():
    """Atomically claim one Pending Update change for AI review, or None.
    find_one_and_update is itself atomic, so concurrent workers (this
    app's 3 gunicorn workers, each running its own copy of this loop) never
    review — and never try to approve — the same change twice. Returns the
    document AFTER the update (claimed_at set) so the caller's own logic
    never needs a second read.

    Filters on request_reason containing "discord.com" as a cheap,
    DB-side pre-filter — not on the stored referenced_discord_links
    snapshot, which is only populated for changes requested after that
    capture logic shipped (see review_change's matching comment) and so
    can't be trusted as a query filter either. request_reason itself is
    immutable once a change is requested, so this is always accurate; the
    precise extraction (which also rules out a false-positive substring
    match) happens in review_change via extract_discord_links.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_AI_REVIEW_MIN_AGE_SECONDS)
    return mongo.db.changes.find_one_and_update(
        {
            "status": "Pending",
            "change_type": "Update",
            "ai_reviewed": {"$ne": True},
            "ai_review_claimed_at": {"$exists": False},
            "request_reason": {"$regex": "discord.com", "$options": "i"},
            "time_requested": {"$lte": cutoff},
        },
        {"$set": {"ai_review_claimed_at": datetime.now(timezone.utc)}},
        sort=[("time_requested", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _release_claim(change_id):
    mongo.db.changes.update_one({"_id": change_id}, {"$unset": {"ai_review_claimed_at": ""}})


_ai_review_worker_lock = threading.Lock()
_ai_review_worker_started = False


def start_ai_review_worker():
    """Start the background thread that reviews Pending changes. Call
    exactly once per process, from app.py at startup. Safe to call more
    than once in the same process: every call after the first is a no-op."""
    global _ai_review_worker_started
    with _ai_review_worker_lock:
        if _ai_review_worker_started:
            return
        _ai_review_worker_started = True

    def _loop():
        while True:
            try:
                if not get_ai_review_settings()["enabled"]:
                    sleep(_AI_REVIEW_POLL_INTERVAL_SECONDS)
                    continue
                change = _claim_next_change_for_review()
                if change is None:
                    sleep(_AI_REVIEW_POLL_INTERVAL_SECONDS)
                    continue
                try:
                    review_change(change)
                except Exception:
                    traceback.print_exc()
                finally:
                    _release_claim(change["_id"])
            except Exception:
                traceback.print_exc()
                sleep(_AI_REVIEW_POLL_INTERVAL_SECONDS)

    thread = threading.Thread(target=_loop, name="ai-review-worker")
    thread.daemon = True
    thread.start()
