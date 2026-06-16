from flask import flash, redirect
from helpers.change_helpers import request_change
from jsonschema import validate, ValidationError

def validate_form_with_jsonschema(form, schema, *, data=None, partial=False):
    """Validate form data against JSON schema.

    data: pre-built dict to validate; if omitted, form.data is used.
    partial: if True, remove the schema's required list so absent fields don't fail
             (correct for visibility-filtered change requests).
    """
    if data is not None:
        form_data = dict(data)
    else:
        form_data = form.data.copy()
        form_data.pop('csrf_token', None)
        form_data.pop('submit', None)
    form_data.pop('reason', None)

    validation_schema = schema
    if partial:
        validation_schema = {k: v for k, v in schema.items() if k != 'required'}
        # None values mean the field was absent from the submitted form (WTForms
        # default when a field is not in request.form). Omitting a non-required
        # field is valid; keeping None triggers enum/type constraints spuriously.
        form_data = {k: v for k, v in form_data.items() if v is not None}

    try:
        validate(instance=form_data, schema=validation_schema)
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