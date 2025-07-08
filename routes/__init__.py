from flask import g
from app_core import app, mongo, discord
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

def register_routes(app, mongo, discord):
    app.register_blueprint(base_routes)
    app.register_blueprint(data_item_routes)
    app.register_blueprint(change_routes)
    app.register_blueprint(nation_routes)
    app.register_blueprint(character_routes)
    app.register_blueprint(auth_routes)
    app.register_blueprint(admin_tool_routes)
    app.register_blueprint(misc_routes)
    app.register_blueprint(tick_routes)

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
