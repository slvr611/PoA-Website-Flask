from flask import Blueprint, render_template, redirect, request, flash, url_for
from helpers.auth_helpers import admin_required
from helpers.tick_helpers import run_tick_async, GENERAL_TICK_FUNCTIONS, CHARACTER_TICK_FUNCTIONS, ARTIFACT_TICK_FUNCTIONS, MERCHANT_TICK_FUNCTIONS, MERCENARY_TICK_FUNCTIONS, FACTION_TICK_FUNCTIONS, MARKET_TICK_FUNCTIONS, NATION_TICK_FUNCTIONS

tick_routes = Blueprint('tick_routes', __name__)

@tick_routes.route("/tick_helper")
@admin_required
def tick_helper():
    TICK_FUNCTIONS = GENERAL_TICK_FUNCTIONS.copy()
    TICK_FUNCTIONS.update(CHARACTER_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(ARTIFACT_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(MERCHANT_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(MERCENARY_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(FACTION_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(MARKET_TICK_FUNCTIONS)
    TICK_FUNCTIONS.update(NATION_TICK_FUNCTIONS)
    return render_template("tick_helper.html", tick_functions=TICK_FUNCTIONS)

@tick_routes.route('/run_tick', methods=['POST'])
@admin_required
def admin_run_tick():
    form_data = request.form.to_dict()
    
    # Run tick asynchronously
    message = run_tick_async(form_data)
    flash(message, "info")
    
    return redirect("/tick_helper")
