from flask import flash, redirect
from helpers.change_helpers import request_change
from jsonschema import validate, ValidationError

def validate_form_with_jsonschema(form, schema):
    """Validate form data against JSON schema"""
    
    # Remove Flask-WTF specific fields
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    form_data.pop('reason', None)
    
    try:
        validate(instance=form_data, schema=schema)
        return True, None
    except ValidationError as e:
        return False, e.message

"""
def request_change_from_form(form, schema, db, item):
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect(f"/{item.get('_type', 'unknown')}/edit/{item.get('name', item.get('_id'))}")
    
    # Check for unique name if changed
    if "name" in form_data and form_data["name"] != item.get("name") and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/{item.get('_type', 'unknown')}/edit/{item.get('name', item.get('_id'))}")
    
    # Create change request
    reason = form_data.pop("reason", "No Reason Given")
    change_id = request_change(
        data_type=item.get("_type", "unknown"),
        item_id=item["_id"],
        change_type="Update",
        before_data=item,
        after_data=form_data,
        reason=reason
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    return change_id
"""