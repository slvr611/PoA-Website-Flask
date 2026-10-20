from flask import Blueprint, render_template, session, redirect, url_for, g, send_from_directory, after_this_request, request, Response, abort, flash
from pymongo import ASCENDING
from app_core import mongo
import datetime
import os
import time as _time_mod
import boto3


base_routes = Blueprint('base_routes', __name__)

# ---------------------------------------------------------------------------
# Revert-warning banner — process-level cache with 30-second TTL so the DB
# is not hit on every request, but all dynos converge within half a minute.
# ---------------------------------------------------------------------------
_revert_cache: dict = {"active": False, "ts": 0.0}
_REVERT_CACHE_TTL = 30  # seconds


def _get_revert_warning() -> bool:
    now = _time_mod.time()
    if now - _revert_cache["ts"] < _REVERT_CACHE_TTL:
        return _revert_cache["active"]
    doc = mongo.db.global_modifiers.find_one(
        {"name": "global_modifiers"}, {"revert_warning": 1, "_id": 0}
    ) or {}
    active = bool(doc.get("revert_warning"))
    _revert_cache.update({"active": active, "ts": now})
    return active


# ---------------------------------------------------------------------------
# Tick-lockout banner — same process-level-cache shape as the revert
# warning above, reading the tick_status heartbeat (helpers/tick_helpers.py)
# rather than a separate flag. Shorter TTL than the revert warning: this
# reflects a real, currently-enforced block on approving changes (see
# helpers/change_helpers.py's is_tick_locked, which always reads fresh),
# not just an ambient notice, so the banner should catch up quickly.
# ---------------------------------------------------------------------------
_tick_lock_cache: dict = {"active": False, "ts": 0.0}
_TICK_LOCK_CACHE_TTL = 10  # seconds


def _get_tick_in_progress() -> bool:
    now = _time_mod.time()
    if now - _tick_lock_cache["ts"] < _TICK_LOCK_CACHE_TTL:
        return _tick_lock_cache["active"]
    doc = mongo.db.tick_status.find_one({"_id": "current"}, {"running": 1, "_id": 0}) or {}
    active = bool(doc.get("running"))
    _tick_lock_cache.update({"active": active, "ts": now})
    return active

@base_routes.before_app_request
def inject_now():
    g.now = datetime.datetime.now()

@base_routes.before_app_request
def load_user():
    session.permanent = True
    g.user = session.get("user", None)

@base_routes.before_app_request
def calculate_user_permissions():
    # Default permission levels
    g.view_access_level = 0  # Public access
    g.edit_access_level = 0  # No edit access
    g.is_non_player_admin = False
    g.is_rp_mod = False
    g.is_non_player_rp_mod = False

    if g.user:
        # Get user from database to check permissions
        user = mongo.db.players.find_one({"id": g.user.get("id")})

        if user:
            # Base view access for authenticated users
            g.view_access_level = 5

            # Check if user is non-player admin (auto visibility bypass)
            if user.get("is_non_player_admin", False):
                g.is_non_player_admin = True

            # Check RP mod flags
            if user.get("is_rp_mod", False):
                g.is_rp_mod = True
            if user.get("is_non_player_rp_mod", False):
                g.is_non_player_rp_mod = True

            # Check if user is admin
            if user.get("is_admin", False):
                g.view_access_level = 10  # Admin view access
                g.edit_access_level = 10  # Admin edit access
                return

            if g.is_non_player_admin:
                g.view_access_level = 7
                return

            if g.is_non_player_rp_mod:
                g.view_access_level = 7
                return

            # Check if user owns any nations/organizations
            user_id = str(user.get("_id"))
            user_characters = list(mongo.db.characters.find({"player": user_id}))

@base_routes.before_app_request
def inject_revert_warning():
    g.revert_warning = _get_revert_warning()


@base_routes.before_app_request
def inject_tick_in_progress():
    g.tick_in_progress = _get_tick_in_progress()


# ---------------------------------------------------------------------------
# Failed-propagation banner — admins only (an operational concern, not a
# player-facing one). Same process-level-cache shape as the banners above;
# a failed propagation_outbox entry (helpers/change_helpers.py) means a
# dependent-object recalculation cascade gave up after
# _OUTBOX_MAX_ATTEMPTS — the approved/reverted change itself still applied
# correctly, but something that depends on it may be stale until an admin
# investigates (see helpers/change_helpers.py's count_failed_propagations).
# ---------------------------------------------------------------------------
_failed_propagations_cache: dict = {"count": 0, "ts": 0.0}
_FAILED_PROPAGATIONS_CACHE_TTL = 30  # seconds


def _get_failed_propagations_count() -> int:
    now = _time_mod.time()
    if now - _failed_propagations_cache["ts"] < _FAILED_PROPAGATIONS_CACHE_TTL:
        return _failed_propagations_cache["count"]
    from helpers.change_helpers import count_failed_propagations
    count = count_failed_propagations()
    _failed_propagations_cache.update({"count": count, "ts": now})
    return count


@base_routes.before_app_request
def inject_failed_propagations():
    g.failed_propagations_count = _get_failed_propagations_count() if (g.user and g.user.get("is_admin")) else 0


# ---------------------------------------------------------------------------
# AI change review settings — same cached-injector shape as the banners
# above, for the admin tools page's status card
# (helpers/ai_review_helpers.py's get_ai_review_settings is the source of
# truth; this just avoids hitting the DB on every single request).
# ---------------------------------------------------------------------------
_ai_review_settings_cache: dict = {"enabled": True, "dry_run": True, "ts": 0.0}
_AI_REVIEW_SETTINGS_CACHE_TTL = 30  # seconds


def _get_ai_review_settings_cached() -> dict:
    now = _time_mod.time()
    if now - _ai_review_settings_cache["ts"] < _AI_REVIEW_SETTINGS_CACHE_TTL:
        return {"enabled": _ai_review_settings_cache["enabled"], "dry_run": _ai_review_settings_cache["dry_run"]}
    from helpers.ai_review_helpers import get_ai_review_settings
    settings = get_ai_review_settings()
    _ai_review_settings_cache.update({**settings, "ts": now})
    return settings


@base_routes.before_app_request
def inject_ai_review_settings():
    g.ai_review_settings = _get_ai_review_settings_cached() if (g.user and g.user.get("is_admin")) else None


@base_routes.before_app_request
def cache_previous_url():
    if request.endpoint != 'static':
        session['second_previous_url'] = session.get('previous_url', None)
        session['previous_url'] = session.get('current_url', None)
        session['current_url'] = request.url


@base_routes.route("/admin/toggle-revert-warning")
def toggle_revert_warning():
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    doc = mongo.db.global_modifiers.find_one(
        {"name": "global_modifiers"}, {"revert_warning": 1}
    ) or {}
    new_state = not bool(doc.get("revert_warning"))
    mongo.db.global_modifiers.update_one(
        {"name": "global_modifiers"},
        {"$set": {"revert_warning": new_state}},
        upsert=True,
    )
    _revert_cache.update({"active": new_state, "ts": _time_mod.time()})
    flash(f"Revert warning {'enabled' if new_state else 'disabled'}.")
    return redirect(request.referrer or "/")


@base_routes.route("/admin/toggle-ai-review")
def toggle_ai_review():
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    from helpers.ai_review_helpers import get_ai_review_settings
    settings = get_ai_review_settings()
    settings["enabled"] = not settings["enabled"]
    mongo.db.global_modifiers.update_one(
        {"name": "global_modifiers"},
        {"$set": {"ai_review_enabled": settings["enabled"]}},
        upsert=True,
    )
    _ai_review_settings_cache.update({**settings, "ts": _time_mod.time()})
    flash(f"AI change review {'enabled' if settings['enabled'] else 'disabled'}.")
    return redirect(request.referrer or "/")


@base_routes.route("/admin/toggle-ai-review-dry-run")
def toggle_ai_review_dry_run():
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    from helpers.ai_review_helpers import get_ai_review_settings
    settings = get_ai_review_settings()
    settings["dry_run"] = not settings["dry_run"]
    mongo.db.global_modifiers.update_one(
        {"name": "global_modifiers"},
        {"$set": {"ai_review_dry_run": settings["dry_run"]}},
        upsert=True,
    )
    _ai_review_settings_cache.update({**settings, "ts": _time_mod.time()})
    flash(
        "AI change review is now "
        + ("in dry-run mode (records verdicts, approves nothing)." if settings["dry_run"]
           else "LIVE — it will approve sufficiently explained units.")
    )
    return redirect(request.referrer or "/")


@base_routes.route("/")
def home():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    current_session = global_modifiers.get("session_counter", 1)
    return render_template("index.html", current_session=current_session)

@base_routes.route("/map")
def map():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    current_session = global_modifiers.get("session_counter", 1)
    return render_template("map.html", current_session=current_session)

@base_routes.route("/go_back")
def go_back():
    previous_url = session.get('second_previous_url', url_for("base_routes.home"))
    return redirect(previous_url)

@base_routes.route('/api/s3-image/<path:s3_key>')
def s3_image_proxy(s3_key):
    """Serve images stored in the private S3 bucket through the Flask app."""
    s3_bucket     = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    if not all([s3_bucket, aws_access_key, aws_secret_key]):
        abort(503)

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
        )
        obj = client.get_object(Bucket=s3_bucket, Key=s3_key)
        content_type = obj.get("ContentType", "application/octet-stream")
        return Response(
            obj["Body"].read(),
            mimetype=content_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        abort(404)


@base_routes.route('/static/images/maps/<path:filename>')
def optimized_map_serving(filename):
    """Serve map files with optimized caching headers"""
    @after_this_request
    def add_header(response):
        # Cache for 1 week
        response.headers['Cache-Control'] = 'public, max-age=604800'
        return response
    
    # Path to your static files
    maps_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'maps')
    return send_from_directory(maps_dir, filename)
