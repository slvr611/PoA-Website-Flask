from functools import wraps
from flask import g, redirect, url_for

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user or not g.user.get("is_admin", False):
            return redirect(url_for("base_routes.home"))
        return f(*args, **kwargs)
    return decorated_function

def save_user_to_db(user):
    from app_core import mongo

    existing_player = mongo.db.players.find_one({"id": str(user.id)})
    if not existing_player:
        mongo.db.players.insert_one({
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_admin": False,
            "is_rp_mod": False,
            "is_website_helper": False
        })
        print(f"Player {user.name} added to database.")
    else:
        mongo.db.players.update_one(
            {"id": str(user.id)},
            {"$set": {
                "name": user.name,
                "avatar_url": user.avatar_url
            }}
        )
        print(f"Player {user.name} updated in database.")