from flask import g
from app_core import app, mongo, discord, json_data
from helpers.data_helpers import get_user_entities

from .base_routes import base_routes
from .data_item_routes import data_item_routes
from .change_routes import change_routes
from .nation_routes import nation_routes
from .character_routes import character_routes
from .auth_routes import auth_routes
from .admin_tool_routes import admin_tool_routes
from .misc_routes import misc_routes
from .tick_routes import tick_routes
from .war_routes import war_routes
from .pops_routes import pops_routes
from .hex_map_routes import hex_map_routes
from .district_def_routes import district_def_routes
from .trade_route_routes import trade_route_routes

def register_routes(app, mongo, discord):
    app.register_blueprint(base_routes)
    # war_routes must be registered before data_item_routes so /wars/item/<ref>
    # uses the custom war view instead of the generic dataItem template.
    app.register_blueprint(war_routes)
    app.register_blueprint(data_item_routes)
    app.register_blueprint(change_routes)
    app.register_blueprint(nation_routes)
    app.register_blueprint(character_routes)
    app.register_blueprint(auth_routes)
    app.register_blueprint(admin_tool_routes)
    app.register_blueprint(misc_routes)
    app.register_blueprint(tick_routes)
    app.register_blueprint(pops_routes)
    app.register_blueprint(hex_map_routes)
    app.register_blueprint(district_def_routes)
    app.register_blueprint(trade_route_routes)

@app.context_processor
def inject_navbar_data():
    return {
        'user_entities': get_user_entities()
    }

@app.context_processor
def inject_permission_data():
    """Make permission data available to all templates"""
    return {
        'view_access_level': getattr(g, 'view_access_level', 0),
        'edit_access_level': getattr(g, 'edit_access_level', 0),
        'is_non_player_admin': getattr(g, 'is_non_player_admin', False),
    }

@app.context_processor
def inject_modifier_data():
    modifier_types = json_data.get("modifier_types", {})
    sorted_modifier_types = sorted(modifier_types.items(), key=lambda x: x[1].get("name", x[0]))

    all_resources = [{"key": "resource", "name": "All Resources"}]
    for r in json_data.get("general_resources", []):
        all_resources.append({"key": r["key"], "name": r["name"]})
    for r in json_data.get("unique_resources", []):
        all_resources.append({"key": r["key"], "name": r["name"]})
    all_resources.append({"key": "money", "name": "Money"})
    all_resources[1:] = sorted(all_resources[1:], key=lambda x: x["name"])

    all_trade_resources = []
    for r in json_data.get("general_resources", []):
        all_trade_resources.append({"key": r["key"], "name": r["name"]})
    for r in json_data.get("unique_resources", []):
        all_trade_resources.append({"key": r["key"], "name": r["name"]})
    for r in json_data.get("luxury_resources", []):
        all_trade_resources.append({"key": r["key"], "name": r["name"]})
    all_trade_resources = sorted(all_trade_resources, key=lambda x: x["name"])

    all_attributes = [
        {"key": "attribute", "name": "All Attributes"},
        {"key": "rulership", "name": "Rulership"},
        {"key": "cunning", "name": "Cunning"},
        {"key": "charisma", "name": "Charisma"},
        {"key": "prowess", "name": "Prowess"},
        {"key": "magic", "name": "Magic"},
        {"key": "strategy", "name": "Strategy"},
    ]

    jobs = json_data.get("jobs", {})
    all_jobs = sorted(
        [{"key": k, "name": v.get("display_name", k)} for k, v in jobs.items()],
        key=lambda x: x["name"]
    )

    scope_definitions = json_data.get("scope_definitions", {})
    scopes_by_source_type = {}
    for scope_key, scope_data in scope_definitions.items():
        src_type = scope_data.get("source_type", "")
        scopes_by_source_type.setdefault(src_type, []).append({
            "key": scope_key,
            "name": scope_data.get("name", scope_key),
            "description": scope_data.get("description", ""),
            "target_type": scope_data.get("target_type", ""),
        })

    scaling_types = json_data.get("scaling_types", {})
    # "flat" always first, remaining sorted alphabetically by display name
    sorted_scaling_types = sorted(
        scaling_types.items(),
        key=lambda x: (0 if x[0] == "flat" else 1, x[1].get("name", x[0]))
    )
    sorted_scaling_type_keys = [k for k, _ in sorted_scaling_types]

    terrains_data = json_data.get("terrains", {})
    all_terrains = sorted(
        [{"key": k, "name": v.get("display_name", k)} for k, v in terrains_data.items()],
        key=lambda x: x["name"]
    )

    _db_cats = list(mongo.db.district_categories.find({}, {"_id": 0, "key": 1, "display_name": 1}).sort("sort_order", 1))
    all_district_categories = [{"key": c["key"], "name": c.get("display_name") or c["key"]} for c in _db_cats]

    _db_defs = list(mongo.db.district_defs.find({}, {"_id": 0, "key": 1, "display_name": 1}).sort("display_name", 1))
    all_district_defs = [{"key": d["key"], "display_name": d.get("display_name") or d["key"]} for d in _db_defs]

    all_unit_categories = [
        {"key": "naval", "name": "Naval"},
        {"key": "cavalry", "name": "Cavalry"},
        {"key": "infantry", "name": "Infantry"},
        {"key": "archer", "name": "Archer"},
        {"key": "siege", "name": "Siege"},
        {"key": "support", "name": "Support"},
        {"key": "ruler", "name": "Ruler"},
        {"key": "land", "name": "Land (All)"},
        {"key": "all", "name": "All Units"},
    ]

    all_unit_stats = [
        {"key": "speed", "name": "Speed"},
        {"key": "morale", "name": "Morale"},
        {"key": "damage", "name": "Damage"},
        {"key": "hp", "name": "HP"},
        {"key": "defense", "name": "Defense"},
        {"key": "attack", "name": "Attack"},
        {"key": "armor", "name": "Armor"},
        {"key": "range", "name": "Range"},
    ]

    all_visibility_targets = [
        {"key": "all_nations", "name": "All Nations"},
        {"key": "region", "name": "Specific Region"},
        {"key": "specific_nation", "name": "Specific Nation"},
    ]

    all_progress_tiers = [
        {"key": "0", "name": "Tier 0"},
        {"key": "1", "name": "Tier 1"},
        {"key": "2", "name": "Tier 2"},
        {"key": "3", "name": "Tier 3"},
        {"key": "4", "name": "Tier 4"},
    ]

    _tech_category_keys = sorted({v.get("type", "") for v in json_data.get("tech", {}).values() if v.get("type")})
    all_tech_categories = [{"key": k.lower(), "name": k} for k in _tech_category_keys]

    return {
        "modifier_types": modifier_types,
        "sorted_modifier_types": sorted_modifier_types,
        "all_resources": all_resources,
        "all_trade_resources": all_trade_resources,
        "all_attributes": all_attributes,
        "all_jobs": all_jobs,
        "scope_definitions": scope_definitions,
        "scopes_by_source_type": scopes_by_source_type,
        "scaling_types": scaling_types,
        "sorted_scaling_types": sorted_scaling_types,
        "sorted_scaling_type_keys": sorted_scaling_type_keys,
        "all_terrains": all_terrains,
        "all_district_categories": all_district_categories,
        "all_district_defs": all_district_defs,
        "all_unit_categories": all_unit_categories,
        "all_unit_stats": all_unit_stats,
        "all_progress_tiers": all_progress_tiers,
        "all_tech_categories": all_tech_categories,
        "all_visibility_targets": all_visibility_targets,
    }
