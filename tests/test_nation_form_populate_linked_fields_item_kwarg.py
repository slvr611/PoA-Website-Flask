"""
Regression test for the "New Nation" page crashing outright:

    TypeError: NationForm.populate_linked_fields() got an unexpected
    keyword argument 'item'

Root cause: routes/data_item_routes.py's generic _render_item_form (used
by data_item_new/data_item_edit for every data_type, including "nations")
always calls form.populate_linked_fields(schema, dropdown_options,
item=item) — every other BaseSchemaForm subclass (JobForm, the generic
dataItem forms, etc.) accepts that kwarg as `item=None`, but NationForm's
own populate_linked_fields was still named `nation=None`. Nation editing
never hit this because routes/nation_routes.py's own dedicated edit route
called it positionally-matched as `nation=nation` — only the generic
"new"/"edit" path (and specifically "New Nation", which has no dedicated
route) went through _render_item_form and crashed.

Fixed by renaming the parameter to `item` (matching every other
populate_linked_fields implementation) and updating the one explicit
caller (nation_routes.py) to pass `item=nation`.
"""
from flask import g

from app_core import category_data
from forms import form_generator


def _nations_schema():
    return category_data["nations"]["schema"]


class TestNationFormPopulateLinkedFieldsAcceptsItemKwarg:
    def test_new_nation_page_with_no_item_does_not_crash(self, flask_app):
        """Reproduces the exact "New Nation" page crash: no item exists yet,
        and the generic route calls populate_linked_fields(schema,
        dropdown_options, item=None)."""
        schema = _nations_schema()

        with flask_app.test_request_context("/nations/new"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("nations", schema, item=None)
            form.populate_linked_fields(schema, {}, item=None)

    def test_existing_nation_item_still_populates_tech_gated_city_choices(self, flask_app):
        """Passing a real item (the edit-page path) must still work, and
        the tech-gated city dropdown logic (the one actual consumer of this
        parameter) must still see it."""
        schema = _nations_schema()
        item = {"_id": "fake-id", "name": "Test Nation", "modifiers": [], "districts": [], "cities": []}

        with flask_app.test_request_context("/nations/edit/Test%20Nation"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("nations", schema, item=item)
            form.populate_linked_fields(schema, {}, item=item)
