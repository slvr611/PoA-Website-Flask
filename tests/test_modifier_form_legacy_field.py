"""
Regression test for a real production bug: editing any nation/character with
at least one modifier entry (old-format OR new-format — the crash happens
unconditionally, before format is even considered) 500'd with

    jinja2.exceptions.UndefinedError: 'forms.ModifierForm object' has no
    attribute 'field'

Root cause: the modifier_table macro's WTForms-mode fallback loop
(templates/_modifier_macros.html) references a fixed list of sub-field keys
to render as hidden inputs whenever they aren't already shown as one of the
current modifier type's extra_fields — this clears stale values left over
from a previously-selected modifier type. At some point "field" (the legacy
pre-modifier_type storage key, e.g. {"field": "cunning", "value": 5, ...})
was added to that list, but ModifierForm (forms.py) was never given a
matching "field" attribute — so `modifier_field.form["field"]` raised
UndefinedError for every single modifier row rendered in WTForms mode,
regardless of whether that particular row was old- or new-format.

Confirmed against live data before the fix: 3 of 5 real nations' edit pages
(any nation with at least one modifier) 500'd; the 2 that loaded fine simply
had zero modifiers, so the WTForms FieldList never iterated at all.
"""
from wtforms import Form, FieldList, FormField

from forms import ModifierForm


class TestModifierFormHasLegacyFieldAttribute:
    def test_modifier_form_declares_field_attribute(self):
        """Direct guard for the exact missing attribute."""
        form = ModifierForm()
        assert hasattr(form, "field"), (
            "ModifierForm is missing the 'field' attribute that "
            "_modifier_macros.html's fallback loop references — this is "
            "the exact bug that 500'd every nation/character edit page "
            "with at least one modifier"
        )


class _HolderForm(Form):
    modifiers = FieldList(FormField(ModifierForm), min_entries=0)


class TestModifierTableMacroRendersOldFormatModifier:
    def test_macro_does_not_crash_on_old_format_modifier(self, flask_app):
        """Exercises the actual macro code path that crashed, using a
        real old-format modifier (no modifier_type/scope at all — just the
        legacy {"field": ...} shape) inside a real WTForms FieldList, the
        same structure nation_owner.html builds for form.modifiers.

        modifier_types/sorted_modifier_types/scope_definitions are normally
        injected by the inject_modifier_data context processor
        (routes/__init__.py), which only fires once register_routes() has
        run (app.py) — the flask_app fixture imports app_core directly and
        skips that, so they're passed in explicitly here instead."""
        from flask import render_template_string
        from app_core import json_data

        old_format_modifier = {
            "field": "cunning", "value": 5, "duration": -1, "source": "legacy",
        }
        form = _HolderForm(data={"modifiers": [old_format_modifier]})

        modifier_types = json_data.get("modifier_types", {})
        template_src = (
            "{% from '_modifier_macros.html' import modifier_table with context %}"
            "{{ modifier_table(form.modifiers, [], field_name='modifiers', "
            "entity_source_type='nation', show_scaling=False) }}"
        )

        with flask_app.test_request_context():
            # If some other test in the suite has already triggered
            # register_routes() (app.py), routes/__init__.py's app-level
            # context processors (e.g. inject_navbar_data) are registered
            # for the rest of the process and will fire here too, expecting
            # g.user/g.view_access_level the way base_routes.py's
            # before_app_request hooks would normally set them. A bare
            # test_request_context() doesn't run those hooks, so set the
            # bit they need directly to keep this test's outcome independent
            # of what ran before it.
            from flask import g
            g.user = None

            # Would raise jinja2.exceptions.UndefinedError before the fix.
            html = render_template_string(
                template_src,
                form=form,
                modifier_types=modifier_types,
                sorted_modifier_types=sorted(modifier_types.items(), key=lambda x: x[1].get("name", x[0])),
                scope_definitions=json_data.get("scope_definitions", {}),
            )

        assert "modifiers-0-modifier_type" in html
