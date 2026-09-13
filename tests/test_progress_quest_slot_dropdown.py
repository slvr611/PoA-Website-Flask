"""
Regression tests for the "Add Quest" slot dropdown bug: adding a new
progress_quests row to a character/merchant/mercenary offered only "No Slot"
plus the spell-slot options — never the entity's actual N_progress_slot tiers
— until the item was saved and re-opened for editing.

Root cause was two independent bugs in forms.py's BaseSchemaForm, both in the
non-nation code path (nations already worked correctly):

1. get_available_slots's non-nation branch was hardcoded to always return
   exactly ["no_slot", "1_progress_slot", tier_1/2/3_spell_slot] regardless
   of the entity's real 0-4_progress_slots values.
2. populate_linked_fields (which calls get_available_slots to build the
   dropdown) was only ever given form-field data — and 0-4_progress_slots are
   "calculated": true fields, which create_form_class deliberately never
   turns into form fields, so that data could never contain them anyway.
   Only a save-and-reload round trip (which re-fetches the DB item on the
   subsequent GET) ever exposed the real slot counts.

The fix: get_available_slots's non-nation branch now iterates the entity's
real 0-4_progress_slots (falling back to the schema's own base_value for a
brand-new, not-yet-saved item), and populate_linked_fields now takes an
`item` parameter and prefers the raw DB item over form-field data when
computing slot choices.
"""
from flask import g, render_template

from app_core import category_data
from forms import BaseSchemaForm, form_generator


def _character_schema():
    return category_data["characters"]["schema"]


class TestGetAvailableSlotsReflectsEntitysRealSlotCounts:
    def test_new_item_falls_back_to_schema_base_value(self):
        """No item saved yet (item=None / {}) — must still offer the base
        3x 1_progress_slot tier from the schema's own base_value, not just
        the old hardcoded set."""
        form = BaseSchemaForm.__new__(BaseSchemaForm)
        slots = form.get_available_slots({}, _character_schema())
        values = [s[0] for s in slots]
        assert "no_slot" in values
        assert "1_progress_slot" in values
        assert "0_progress_slot" not in values  # base_value 0 -> not offered
        assert "tier_1_spell_slot" in values

    def test_existing_item_with_bonus_slot_reflects_it(self):
        """An item whose calculated fields show a bonus 2_progress_slots
        (e.g. from an equipped artifact modifier) must offer that tier too
        — this is exactly what was missing before the fix, since only a
        save-and-reload ever surfaced it."""
        form = BaseSchemaForm.__new__(BaseSchemaForm)
        entity = {"1_progress_slots": 3, "2_progress_slots": 1}
        slots = form.get_available_slots(entity, _character_schema())
        values = [s[0] for s in slots]
        assert "1_progress_slot" in values
        assert "2_progress_slot" in values

    def test_zero_slot_tier_is_not_offered(self):
        form = BaseSchemaForm.__new__(BaseSchemaForm)
        entity = {"1_progress_slots": 3, "2_progress_slots": 0}
        slots = form.get_available_slots(entity, _character_schema())
        values = [s[0] for s in slots]
        assert "2_progress_slot" not in values


class TestPopulateLinkedFieldsPrefersItemOverFormData:
    """Uses a real character form built by form_generator (as the actual
    edit route does) rather than a hand-rolled fake, since
    populate_linked_fields also walks every other schema property looking
    for collections/enum fields — a minimal fake form lacks those attributes
    and errors out before reaching the progress_quests handling at all."""

    def _quest_item(self, extra_slots=None):
        item = {
            "_id": "fake-id", "name": "Test Character", "modifiers": [],
            "progress_quests": [{
                "_id": "q1", "quest_name": "Study", "slot": "no_slot",
                "current_progress": 0, "required_progress": 10,
            }],
        }
        if extra_slots:
            item.update(extra_slots)
        return item

    def test_slot_choices_use_item_not_stale_form_data(self, flask_app):
        """Simulates the real bug: form-field data has no 0-4_progress_slots
        (calculated fields are never turned into form fields), but the raw
        DB item does — populate_linked_fields must use the item."""
        schema = _character_schema()
        item = self._quest_item({"1_progress_slots": 3, "2_progress_slots": 1})

        with flask_app.test_request_context("/characters/edit/Test%20Character"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("characters", schema, item=item)
            form.populate_linked_fields(schema, {}, item=item)

            choices = [c[0] for c in form.progress_quests[0].slot.choices]
        assert "1_progress_slot" in choices
        assert "2_progress_slot" in choices

    def test_new_item_with_no_db_record_falls_back_to_schema_default(self, flask_app):
        schema = _character_schema()
        item = self._quest_item()

        with flask_app.test_request_context("/characters/edit/Test%20Character"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("characters", schema, item=item)
            # No 0-4_progress_slots on the item at all (a brand-new item)
            # and no form data either — must fall back to schema base_value.
            form.populate_linked_fields(schema, {}, item=None)

            choices = [c[0] for c in form.progress_quests[0].slot.choices]
        assert "1_progress_slot" in choices
        assert "2_progress_slot" not in choices


class TestTemplateEmbedsEntityProgressSlotCounts:
    """dataItem.html's ENTITY_PROGRESS_SLOT_COUNTS Jinja block went through
    several rewrites while being simplified — a rendering smoke test guards
    against a Jinja syntax error slipping back in, for both an existing item
    (edit page) and a brand-new one (item=None, new page)."""

    def test_renders_with_existing_item_using_its_calculated_slots(self, flask_app):
        import importlib
        importlib.import_module("routes.character_routes")

        schema = _character_schema()
        item = {
            "_id": "fake-id", "name": "Test Character", "modifiers": [],
            "progress_quests": [], "1_progress_slots": 3, "2_progress_slots": 1,
        }
        with flask_app.test_request_context("/characters/edit/Test%20Character"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("characters", schema, item=item)
            html = render_template(
                "dataItem.html",
                editable=True, item=item, title="Test Character",
                form=form, schema=schema, data_type="characters",
                entity_source_type="character", item_ref="Test Character",
            )

        collapsed = html.replace(" ", "").replace("\n", "").replace("\t", "")
        assert "constENTITY_PROGRESS_SLOT_COUNTS=[0,3,1,0,0]" in collapsed

    def test_renders_with_no_item_using_schema_base_values(self, flask_app):
        import importlib
        importlib.import_module("routes.character_routes")

        schema = _character_schema()
        with flask_app.test_request_context("/characters/new"):
            g.user = {"id": "tester", "is_admin": True}
            form = form_generator.get_form("characters", schema, item=None)
            html = render_template(
                "dataItem.html",
                editable=True, item=None, title="New Character",
                form=form, schema=schema, data_type="characters",
                entity_source_type="character",
            )

        collapsed = html.replace(" ", "").replace("\n", "").replace("\t", "")
        assert "constENTITY_PROGRESS_SLOT_COUNTS=[0,3,0,0,0]" in collapsed
