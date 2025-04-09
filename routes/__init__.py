from app_core import app, mongo, discord

from .base_routes import base_routes
from .data_routes import data_routes
from .edit_routes import edit_routes
from .change_routes import change_routes
from .nation_routes import nation_routes
from .auth_routes import auth_routes

def register_routes(app, mongo, discord):
    app.register_blueprint(base_routes)
    app.register_blueprint(data_routes)
    app.register_blueprint(edit_routes)
    app.register_blueprint(change_routes)
    app.register_blueprint(nation_routes)
    app.register_blueprint(auth_routes)
