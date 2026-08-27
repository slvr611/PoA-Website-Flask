"""
Regression tests for relaxing character strengths/weaknesses:

  - Characters no longer need exactly 2 strengths and 2 weaknesses — any
    count (including zero) is allowed at creation and after.
  - The generic character edit page (templates/dataItem.html) previously
    rendered strengths/weaknesses via its catch-all fallback
    ({{ field(class="form-control") }}), which has no "Add"/"Remove" UI at
    all for a FieldList — so even though the backend never enforced a count
    restriction on *editing* an existing character, there was simply no way
    to add or remove an entry after creation. Fixed by giving
    strengths/weaknesses their own template branch (mirroring the existing
    negative_titles pattern) plus matching addToArray/removeFromArray JS
    wiring.
"""
import importlib

from flask import render_template, g

from routes.character_routes import validate_character_strengths_weaknesses

# Importing routes.character_routes triggers routes/__init__.py's
# module-level @app.context_processor registrations (inject_modifier_data,
# etc.) on the shared app_core.app instance the flask_app fixture wraps —
# without needing full register_routes() blueprint registration. Same
# technique as test_district_upgrade_visibility.py.
importlib.import_module("routes.character_routes")


class TestValidateCharacterStrengthsWeaknessesCountIsUnrestricted:
    def test_zero_strengths_and_weaknesses_is_valid(self):
        assert validate_character_strengths_weaknesses({"strengths": [], "weaknesses": []}) is None

    def test_one_strength_and_one_weakness_is_valid(self):
        assert validate_character_strengths_weaknesses(
            {"strengths": ["prowess"], "weaknesses": ["magic"]}
        ) is None

    def test_three_strengths_and_three_weaknesses_is_valid(self):
        assert validate_character_strengths_weaknesses({
            "strengths": ["prowess", "strategy", "magic"],
            "weaknesses": ["rulership", "cunning", "charisma"],
        }) is None


class TestValidateCharacterStrengthsWeaknessesStillCatchesRealProblems:
    def test_duplicate_strength_is_rejected(self):
        error = validate_character_strengths_weaknesses(
            {"strengths": ["prowess", "prowess"], "weaknesses": []}
        )
        assert error is not None
        assert "more than once" in error

    def test_duplicate_weakness_is_rejected(self):
        error = validate_character_strengths_weaknesses(
            {"strengths": [], "weaknesses": ["magic", "magic"]}
        )
        assert error is not None
        assert "more than once" in error

    def test_same_stat_as_strength_and_weakness_is_rejected(self):
        error = validate_character_strengths_weaknesses(
            {"strengths": ["prowess"], "weaknesses": ["prowess"]}
        )
        assert error == "A stat cannot be both a strength and a weakness."

    def test_ruler_type_requirement_still_enforced(self):
        # Steward requires rulership as a strength and magic as a weakness
        # (RULER_TYPE_STATS, tick_helpers.py) — neither is present here.
        error = validate_character_strengths_weaknesses({
            "character_type": "Steward",
            "strengths": ["prowess"],
            "weaknesses": ["cunning"],
        })
        assert error is not None
        assert "Steward must have" in error


class TestCharacterEditFormAllowsAddingAndRemovingStrengthsWeaknesses:
    def test_strengths_and_weaknesses_render_with_add_remove_ui(self, flask_app):
        from app_core import category_data
        from forms import form_generator

        schema = category_data["characters"]["schema"]
        item = {
            "_id": "fake-id", "name": "Test Character",
            "strengths": ["prowess", "strategy"],
            "weaknesses": ["rulership"],
            "modifiers": [],
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

        # Each field must render as its own addable/removable list (a bare
        # {{ field(...) }} fallback, the pre-fix behavior, produces neither
        # a "-tbody" container nor an addToArray('strengths'/'weaknesses') call).
        assert 'id="strengths-tbody"' in html
        assert "addToArray('strengths')" in html
        assert 'id="weaknesses-tbody"' in html
        assert "addToArray('weaknesses')" in html
        # The two currently-set strengths must both still be present.
        assert html.count('id="strengths-0"') == 1
        assert html.count('id="strengths-1"') == 1

    def test_removing_a_strength_or_weakness_reindexes_by_bare_field_index(self, flask_app):
        """Guards the specific shape a plain FieldList(SelectField) needs
        after a row is removed — "{field}-{index}", NOT the
        "{field}-{index}-{subfield}" shape the generic fallback reindexer
        produces (that mismatch is why negative_titles needed its own
        reindex function, and strengths/weaknesses need the same one)."""
        import re
        template_src = (open("templates/dataItem.html", encoding="utf-8").read())

        assert "reindexFlatFieldList" in template_src
        assert re.search(r"categoryName === 'strengths'.*reindexFlatFieldList\('strengths'\)", template_src)
        assert re.search(r"categoryName === 'weaknesses'.*reindexFlatFieldList\('weaknesses'\)", template_src)
