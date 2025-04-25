from flask import Blueprint, render_template, redirect, request
from helpers.auth_helpers import admin_required
from helpers.tick_helpers import tick, TICK_FUNCTIONS

tick_routes = Blueprint('tick_routes', __name__)

@tick_routes.route("/tick_helper")
@admin_required
def tick_helper():
    return render_template("tick_helper.html", tick_functions=TICK_FUNCTIONS)

@tick_routes.route("/run_tick", methods=["POST"])
@admin_required
def run_tick():
    tick(request.form)
    
    return redirect("/tick_helper")
