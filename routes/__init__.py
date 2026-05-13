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
        'edit_access_level': getattr(g, 'edit_access_level', 0)
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
    all_resources[1:] = sorted(all_resources[1:], key=lambda x: x["name"])

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

    return {
        "modifier_types": modifier_types,
        "sorted_modifier_types": sorted_modifier_types,
        "all_resources": all_resources,
        "all_attributes": all_attributes,
        "all_jobs": all_jobs,
        "scope_definitions": scope_definitions,
        "scopes_by_source_type": scopes_by_source_type,
    }
