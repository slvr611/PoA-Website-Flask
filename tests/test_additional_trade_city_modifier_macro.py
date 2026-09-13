"""
Regression test for the exact bug class fixed earlier for ModifierForm's
"field" attribute: _modifier_macros.html's WTForms-mode fallback loop
references a fixed list of extra-field keys to clear as hidden inputs
whenever they aren't part of the currently-selected modifier type's own
extra_fields — any key added to that list without a matching ModifierForm
attribute crashes the WHOLE modifier table for every row, regardless of
that row's own modifier_type.

"city" was added to that list (and to ModifierForm) for the new
"additional_trade_city" modifier type — this pins that ModifierForm
actually declares it, and that a merchant modifiers list mixing an
additional_trade_city entry with an unrelated modifier type renders fine.
"""
import importlib

from wtforms import Form, FieldList, FormField

from forms import ModifierForm


class TestModifierFormHasCityAttribute:
    def test_modifier_form_declares_city_attribute(self):
        form = ModifierForm()
        assert hasattr(form, "city"), (
            "ModifierForm is missing the 'city' attribute that "
            "_modifier_macros.html's fallback loop references for the "
            "additional_trade_city modifier type"
        )


class _HolderForm(Form):
    modifiers = FieldList(FormField(ModifierForm), min_entries=0)


class TestModifierTableMacroRendersAdditionalTradeCity:
    def test_macro_does_not_crash_with_mixed_modifier_rows(self, flask_app):
        """Exercises the actual macro code path with two rows: one
        additional_trade_city (using the new 'city' extra field) and one
        unrelated modifier type — proves neither the new field nor the
        fallback-clearing loop crashes the table."""
        from flask import render_template_string, g
        from app_core import json_data

        importlib.import_module("routes.trade_route_routes")  # unrelated, just ensures app wiring is loaded

        rows = [
            {"modifier_type": "additional_trade_city", "city": "cityB1", "value": 1, "duration": -1, "source": "test"},
            {"modifier_type": "money_income", "value": 10, "duration": -1, "source": "test"},
        ]
        form = _HolderForm(data={"modifiers": rows})

        modifier_types = json_data.get("modifier_types", {})
        template_src = (
            "{% from '_modifier_macros.html' import modifier_table with context %}"
            "{{ modifier_table(form.modifiers, [], field_name='modifiers', "
            "entity_source_type='merchant', show_scaling=False) }}"
        )

        with flask_app.test_request_context():
            g.user = None
            html = render_template_string(
                template_src,
                form=form,
                modifier_types=modifier_types,
                sorted_modifier_types=sorted(modifier_types.items(), key=lambda x: x[1].get("name", x[0])),
                scope_definitions=json_data.get("scope_definitions", {}),
                all_cities=[{"key": "cityB1", "name": "Beta — NationB"}],
            )

        assert "modifiers-0-modifier_type" in html
        assert "modifiers-1-modifier_type" in html
