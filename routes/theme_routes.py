import re

from flask import Blueprint, render_template, request, redirect, flash, g, abort

from app_core import mongo

theme_routes = Blueprint("theme_routes", __name__)

# (token key, display label, dark-theme default shown when nothing is saved yet)
# type="color" only supports opaque #rrggbb, so these defaults are opaque
# approximations of the (mostly translucent) dark-theme CSS variables in
# static/styles.css, not literal copies of them.
CUSTOMIZABLE_THEME_TOKENS = [
    ("bg-page", "Page Background", "#191309"),
    ("bg-card", "Card Background", "#221a12"),
    ("bg-deep", "Nav Bar Background", "#1a140d"),
    ("surface", "Inset Card Surface", "#2a2018"),
    ("surface-hi", "Hover / Chip Fill", "#332619"),
    ("border", "Card Border", "#3a2e20"),
    ("border-hi", "Table Row Border", "#3f3222"),
    ("border-strong", "Strong Border / Outline", "#5a4a34"),
    ("text", "Primary Text", "#f5ecda"),
    ("text-dim", "Dim Text (labels)", "#a89c86"),
    ("text-mid", "Secondary Text", "#c2b89e"),
    ("accent", "Accent", "#d98a54"),
    ("accent-hi", "Accent (Hover / Active)", "#f4c89a"),
    ("heading", "Headings", "#b0c090"),
    ("link", "Links", "#e8b46a"),
    ("pos", "Positive Values", "#a8d88a"),
    ("warn", "Warning Values", "#e8d46a"),
    ("crit", "Critical Values", "#e08a6a"),
    ("done", "Completed / Researched", "#5a7a44"),
    ("avail", "Available Border", "#8aa86a"),
    ("avail-text", "Available Text", "#c8dcae"),
]

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _current_player_or_403():
    if not g.user:
        flash("You must be logged in to access theme settings.")
        return None
    player = mongo.db.players.find_one({"id": g.user.get("id")})
    if not player or not (player.get("is_patreon_supporter") or player.get("is_admin")):
        abort(403)
    return player


@theme_routes.route("/settings/theme", methods=["GET"])
def theme_settings():
    player = _current_player_or_403()
    if player is None:
        return redirect("/login")

    saved = player.get("custom_theme") or {}
    tokens = [
        {"key": key, "label": label, "default": default, "value": saved.get(key, default)}
        for key, label, default in CUSTOMIZABLE_THEME_TOKENS
    ]
    return render_template(
        "theme_settings.html",
        tokens=tokens,
        enabled=bool(player.get("custom_theme_enabled")),
    )


@theme_routes.route("/settings/theme/save", methods=["POST"])
def theme_settings_save():
    player = _current_player_or_403()
    if player is None:
        return redirect("/login")

    custom_theme = {}
    for key, _label, _default in CUSTOMIZABLE_THEME_TOKENS:
        value = (request.form.get(f"token_{key}") or "").strip()
        if _HEX_COLOR_RE.match(value):
            custom_theme[key] = value

    enabled = request.form.get("enabled") == "on"

    mongo.db.players.update_one(
        {"_id": player["_id"]},
        {"$set": {"custom_theme": custom_theme, "custom_theme_enabled": enabled}},
    )
    flash("Theme settings saved.")
    return redirect("/settings/theme")


@theme_routes.route("/settings/theme/reset", methods=["POST"])
def theme_settings_reset():
    player = _current_player_or_403()
    if player is None:
        return redirect("/login")

    mongo.db.players.update_one(
        {"_id": player["_id"]},
        {"$set": {"custom_theme": {}, "custom_theme_enabled": False}},
    )
    flash("Custom theme reset to defaults.")
    return redirect("/settings/theme")
