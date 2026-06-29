"""
Unified visibility gate system.

Entry points for route handlers:
    gate_item_view(data_type, item, *, user, view_access_level, is_non_player_admin)
        -> (visibility_level, visibility_bypassed)

    gate_item_edit(data_type, item, *, user, view_access_level, is_non_player_admin)
        -> True | redirect Response

Supporting functions also used by change_routes.py (replacing the private helpers
that previously lived there):
    build_nation_visibility_map()
    apply_nation_visibility(changes, visibility_map)
    is_change_visibility_bypassed()
    log_visibility_bypass(*, page_url, nation_name, source, user)
"""

from datetime import datetime, timezone
from bson import ObjectId
from flask import g, request, redirect, flash
from app_core import mongo


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maps data_type → how to resolve the parent nation ObjectId.
# "direct"  : the item IS the nation
# "one_hop" : item[nation_field] is the nation ObjectId
# "two_hop" : item[owner_field] → hop_collection document → [nation_field]
VISIBILITY_CONFIG = {
    "nations": {
        "resolution": "direct",
    },
    "characters": {
        "resolution": "one_hop",
        "nation_field": "ruling_nation_org",
    },
    "artifacts": {
        "resolution": "two_hop",
        "owner_field": "owner",
        "hop_collection": "characters",
        "nation_field": "ruling_nation_org",
    },
}

# Maps nation form field names → minimum visibility tier required to see/edit them.
# Fields absent from this dict pass through POST handlers unchanged (calculated/internal).
NATION_EDIT_FIELD_TIERS = {
    # Tier 0 — always visible / editable
    "name": 0, "region": 0, "government_type": 0, "origin": 0, "notes": 0,
    "nomad_camp_type": 0,
    # Tier 1 — demographics
    "primary_race": 1, "primary_culture": 1, "primary_religion": 1,
    # Tier 2 — stability / laws
    "stability": 2, "infamy": 2, "karma": 2, "rolling_karma": 2, "temporary_karma": 2,
    "administration": 2,
    "tax_stance": 2, "military_funding": 2, "conscription_type": 2,
    "diplomatic_stance": 2, "expansion_stance": 2, "economy_type": 2,
    "magic_stance": 2, "centralization_law": 2, "citizenship_stance": 2,
    "foreign_acceptance": 2, "subject_stance": 2, "land_doctrine": 2,
    "naval_doctrine": 2, "scientific_stance": 2, "mercenary_law": 2,
    "consumption_stance": 2, "succession_type": 2, "slavery_stance": 2,
    "vassal_type": 2, "justice_stance": 2,
    # Tier 3 — districts / modifiers / tech
    "districts": 3, "cities": 3, "technologies": 3, "modifiers": 3,
    "progress_quests": 3, "territory_types": 3, "nodes": 3,
    "current_territory": 3, "road_usage": 3,
    "overlord": 3, "compliance": 3, "concessions": 3,
    # Tier 4 — military / resources
    "land_units": 4, "naval_units": 4, "support_units": 4,
    "resource_storage": 4, "money": 4, "jobs": 4,
    # Tier 4 — admin-only fields (never shown to non-admins on edit form)
    "rp_mod_notes": 4,
}

# Maps data_type → field_name → minimum visibility tier required to VIEW the field.
# Fields absent from a type's dict default to tier 0 (always visible).
# Only applies to data types in _VISIBILITY_GATED_TYPES (characters, artifacts).
ITEM_VIEW_FIELD_TIERS = {
    "characters": {
        # Tier 0
        "name": 0, "race": 0, "culture": 0, "religion": 0, "region": 0,
        "player": 0, "creator": 0, "ruling_nation_org": 0,
        # Tier 1
        "character_type": 1,
        "age": 1, "health_status": 1,
        # Tier 2
        "character_subtype": 2,
        "positive_titles": 2, "negative_titles": 2,
        "strengths": 2, "weaknesses": 2,
        "positive_quirk": 2, "negative_quirk": 2,
        "artifacts": 2, 
        # Tier 3
        "rulership": 3, "cunning": 3, "charisma": 3,
        "prowess": 3, "magic": 3, "strategy": 3,
        "death_chance": 3, "heal_chance": 3, "stat_gain_chance": 3,
        "elderly_age": 3, "age_status": 3, 
        "artifact_slots": 3, "used_artifact_slots": 3, "artifact_loss_chance": 3,
        "magic_point_income": 3, "magic_point_capacity": 3,
        "progress_quests": 3, "modifiers": 3, "notes": 3,
        # Tier 4
        "magic_points": 4,
    },
    "artifacts": {
        # Tier 0 — public info
        "name": 0, "rarity": 0,
        # Tier 1
        "creator": 1, 
        # Tier 2
        "owner": 2,
        # Tier 3
        "effect_description": 3, "modifiers": 3,
        "equipped": 3, "artifact_slot_usage": 3,
    },
}


# ---------------------------------------------------------------------------
# Nation-id resolution
# ---------------------------------------------------------------------------

def get_nation_id_for_item(data_type: str, item: dict):
    """
    Return the parent nation ObjectId string for an item, following VISIBILITY_CONFIG.
    Returns None if the chain is broken (item has no nation → treated as publicly visible).
    """
    cfg = VISIBILITY_CONFIG.get(data_type)
    if not cfg:
        return None

    resolution = cfg["resolution"]

    if resolution == "direct":
        return str(item.get("_id", "")) or None

    if resolution == "one_hop":
        raw = item.get(cfg["nation_field"])
        if not raw:
            return None
        try:
            ObjectId(str(raw))  # validate
        except Exception:
            return None
        return str(raw)

    if resolution == "two_hop":
        owner_raw = item.get(cfg["owner_field"])
        if not owner_raw:
            return None
        try:
            owner_oid = ObjectId(str(owner_raw))
        except Exception:
            return None
        hop_doc = mongo.db[cfg["hop_collection"]].find_one(
            {"_id": owner_oid}, {cfg["nation_field"]: 1}
        )
        if not hop_doc:
            return None
        nation_raw = hop_doc.get(cfg["nation_field"])
        if not nation_raw:
            return None
        try:
            ObjectId(str(nation_raw))  # validate
        except Exception:
            return None
        return str(nation_raw)

    return None


# ---------------------------------------------------------------------------
# Ownership check
# ---------------------------------------------------------------------------

def is_item_owner(data_type: str, item: dict, user) -> bool:
    """
    Return True if the logged-in user owns the given item.
    Ownership rules:
        nations    : user has a character with ruling_nation_org == item._id
        characters : item["player"] matches user's player._id
        artifacts  : owner character["player"] matches user's player._id
    """
    if not user:
        return False

    player = mongo.db.players.find_one({"id": user.get("id")}, {"_id": 1})
    if not player:
        return False

    player_id = str(player["_id"])

    if data_type == "nations":
        nation_id = str(item.get("_id", ""))
        char = mongo.db.characters.find_one(
            {"player": player_id, "ruling_nation_org": nation_id}, {"_id": 1}
        )
        # Also try as ObjectId in case it's stored that way
        if not char:
            try:
                char = mongo.db.characters.find_one(
                    {"player": player_id,
                     "ruling_nation_org": ObjectId(nation_id)}, {"_id": 1}
                )
            except Exception:
                pass
        return char is not None

    if data_type == "characters":
        return str(item.get("player", "")) == player_id

    if data_type == "artifacts":
        owner_raw = item.get("owner")
        if not owner_raw:
            return False
        try:
            owner_oid = ObjectId(str(owner_raw))
        except Exception:
            return False
        char = mongo.db.characters.find_one(
            {"_id": owner_oid, "player": player_id}, {"_id": 1}
        )
        return char is not None

    return False


# ---------------------------------------------------------------------------
# Core visibility computation
# ---------------------------------------------------------------------------

def _is_bypass_requested(user) -> bool:
    """True when ?bypass_visibility=1 and user is admin or RP mod."""
    if not user or request.args.get("bypass_visibility") != "1":
        return False
    return bool(user.get("is_admin") or user.get("is_rp_mod"))


def get_item_visibility(
    data_type: str,
    item: dict,
    *,
    user,
    view_access_level: int,
    is_non_player_admin: bool,
) -> tuple:
    """
    Compute (visibility_level: int, visibility_bypassed: bool) for this item.

    Never writes to the DB — callers decide whether to log.
    """
    from calculations.visibility import get_viewer_nation, compute_visibility

    # 1. Non-player admin or non-player RP mod → always full access, no log
    if is_non_player_admin or getattr(g, "is_non_player_rp_mod", False):
        return (4, True)

    # 2. No nation association → publicly visible
    nation_id = get_nation_id_for_item(data_type, item)
    if not nation_id:
        return (4, False)

    # 3. Owner → tier 4
    if is_item_owner(data_type, item, user):
        return (4, False)

    # 4. Admin or RP mod with explicit bypass param → tier 4, caller must log
    if _is_bypass_requested(user):
        return (4, True)

    # 5. Compute via viewer nation
    viewer_nation = get_viewer_nation(user)
    if not viewer_nation:
        return (0, False)

    tier = compute_visibility(viewer_nation, nation_id)
    return (tier, False)


# ---------------------------------------------------------------------------
# Bypass logging
# ---------------------------------------------------------------------------

def log_visibility_bypass(*, page_url: str, nation_name: str = "", source: str, user) -> None:
    """
    Write one record to admin_visibility_logs.
    No-op if user is None or not an admin/RP mod.
    """
    if not (user and (user.get("is_admin") or user.get("is_rp_mod"))):
        return
    mongo.db.admin_visibility_logs.insert_one({
        "admin_id": user.get("id"),
        "admin_username": user.get("name", "unknown"),
        "timestamp": datetime.now(timezone.utc),
        "page_url": page_url,
        "nation": nation_name,
        "source": source,
    })


# ---------------------------------------------------------------------------
# High-level gates for route handlers
# ---------------------------------------------------------------------------

def gate_item_view(
    data_type: str,
    item: dict,
    *,
    user,
    view_access_level: int,
    is_non_player_admin: bool,
) -> tuple:
    """
    Gate for VIEW routes on gated data types (characters, artifacts).

    Returns (visibility_level, visibility_bypassed).
    Writes the bypass log when an admin bypass is active.
    Callers should redirect/abort if visibility_level == 0 and not bypassed.
    """
    visibility_level, visibility_bypassed = get_item_visibility(
        data_type, item,
        user=user,
        view_access_level=view_access_level,
        is_non_player_admin=is_non_player_admin,
    )
    # Log explicit admin bypass (not non-player-admin auto-bypass)
    if visibility_bypassed and _is_bypass_requested(user):
        nation_name = ""
        if data_type == "nations":
            nation_name = item.get("name", "")
        log_visibility_bypass(
            page_url=request.url,
            nation_name=nation_name,
            source=f"{data_type}_view",
            user=user,
        )
    return (visibility_level, visibility_bypassed)


def gate_item_edit(
    data_type: str,
    item: dict,
    *,
    user,
    view_access_level: int,
    is_non_player_admin: bool,
):
    """
    Gate for EDIT routes.

    Returns True if the user may proceed.
    Returns a Flask redirect Response if access is denied.

    Rules:
    - Non-player-admin → always allowed, no log.
    - Admin → always allowed (edit is normal admin work, no log).
    - Owner → allowed.
    - Anyone else → redirect to item view page with a flash message.
    """
    if is_non_player_admin or view_access_level >= 7:
        return True
    if user and user.get("is_admin"):
        return True
    if is_item_owner(data_type, item, user):
        return True

    item_ref = item.get("name") or str(item.get("_id", ""))
    flash("You do not have permission to edit this item.")
    return redirect(f"/{data_type}/item/{item_ref}")


def strip_form_data_to_tier(form_data: dict, tier: int) -> dict:
    """
    Remove fields from form_data that require a visibility tier above `tier`.
    Fields not in NATION_EDIT_FIELD_TIERS pass through unchanged.
    """
    result = {}
    for key, value in form_data.items():
        required = NATION_EDIT_FIELD_TIERS.get(key)
        if required is None or required <= tier:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Change-log helpers (moved from change_routes.py)
# These replace the private _prefixed versions that previously lived there.
# ---------------------------------------------------------------------------

def is_change_visibility_bypassed() -> bool:
    """True when an admin or RP mod has explicitly requested bypass via ?bypass_visibility=1."""
    return _is_bypass_requested(g.user) if g.user else False


def build_nation_visibility_map():
    """
    Return {nation_id_str: tier (0-4)} for all nations from the current user's
    perspective, or None if visibility is unrestricted (admin bypass or non-player-admin).
    Empty dict means the user has no ruling nation → no nation changes visible.
    """
    from calculations.visibility import get_viewer_nation, compute_all_visibilities

    if is_change_visibility_bypassed() or getattr(g, "is_non_player_admin", False):
        return None

    viewer_nation = get_viewer_nation(g.user)
    if not viewer_nation:
        return {}

    name_to_tier = compute_all_visibilities(viewer_nation)
    id_to_tier = {}
    for nation in mongo.db.nations.find({}, {"_id": 1, "name": 1}):
        id_to_tier[str(nation["_id"])] = name_to_tier.get(nation.get("name", ""), 0)
    return id_to_tier


def apply_nation_visibility(changes: list, visibility_map) -> list:
    """
    Filter and annotate changes with _visibility_tier.

    visibility_map is None  → bypass active; all changes shown, _visibility_tier=None
    visibility_map is dict  → tier-0 nation changes removed; others annotated with tier
    """
    filtered = []
    for change in changes:
        if change.get("target_collection") == "nations" and visibility_map is not None:
            tier = visibility_map.get(str(change.get("target", "")), 0)
            if tier == 0:
                continue
            change["_visibility_tier"] = tier
        else:
            change["_visibility_tier"] = None
        filtered.append(change)
    return filtered
