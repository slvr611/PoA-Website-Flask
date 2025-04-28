from flask import Blueprint, render_template, redirect, request
from helpers.auth_helpers import admin_required
from helpers.tick_helpers import tick, GENERAL_TICK_FUNCTIONS, CHARACTER_TICK_FUNCTIONS, MERCHANT_TICK_FUNCTIONS, MERCENARY_TICK_FUNCTIONS, NATION_TICK_FUNCTIONS

tick_routes = Blueprint('tick_routes', __name__)

@tick_routes.route("/tick_helper")
@admin_required
def tick_helper():
    TICK_FUNCTIONS = GENERAL_TICK_FUNCTIONS.copy()
    TICK_FUNCTIONS.update(CHARACTER_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(MERCHANT_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(MERCENARY_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(NATION_TICK_FUNCTIONS)
    return render_template("tick_helper.html", tick_functions=TICK_FUNCTIONS)

@tick_routes.route("/run_tick", methods=["POST"])
@admin_required
def run_tick():
    tick(request.form)
    
    return redirect("/tick_helper")
