from helpers.data_helpers import get_data_on_category
from pymongo import ASCENDING
from helpers.change_helpers import request_change, approve_change
import random

def tick(form_data):
    for key, function in TICK_FUNCTIONS.items():
        run_key = f"run_{key}"
        if run_key in form_data:
            TICK_FUNCTIONS[key]()

def update_rolling_karma():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        new_nation = nation.copy()
        event_type = nation.get("event_type", "Unknown")
        if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
            new_nation["rolling_karma"] = int(nation.get("rolling_karma", 0)) + 1

            change_id = request_change(
                data_type="nations",
                item_id=nation["_id"],
                change_type="Update",
                before_data=nation,
                after_data=new_nation,
                reason="Rolling Karma Update for " + nation["name"]
            )
            approve_change(change_id)
        elif event_type in ["Neutral", "Good", "Very Good", "Fantastic", "Wonderous"]:
            new_nation["rolling_karma"] = int(nation.get("rolling_karma", 0)) - 1
            change_id = request_change(
                data_type="nations",
                item_id=nation["_id"],
                change_type="Update",
                before_data=nation,
                after_data=new_nation,
                reason="Rolling Karma Update for " + nation["name"]
            )
            approve_change(change_id)

    return

def reset_rolling_karma_to_zero():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        new_nation = nation.copy()
        new_nation["rolling_karma"] = 0
        change_id = request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason="Rolling Karma Update for " + nation["name"]
        )
        approve_change(change_id)

    return
    
TICK_FUNCTIONS = {
    "Update Rolling Karma": update_rolling_karma,
    "Reset Rolling Karma to Zero": reset_rolling_karma_to_zero,
}
