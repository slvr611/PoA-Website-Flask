"""
Tests for the global name-character filter (helpers/form_helpers.py).

Prompted by a real incident: a player created a character named "Leader #1",
which broke its own URL ("#" is a URL fragment delimiter, so
"/characters/item/Leader #1" only ever reaches the server as
"/characters/item/Leader "). validate_name_characters / wtforms_validate_name
/ the injected JSON-schema "pattern" all guard against a small, deliberately
narrow set of characters that break URL routing or have special meaning in
MongoDB queries, without rejecting ordinary punctuation used in real names
(apostrophes, hyphens, periods) already present in the game's data.
"""
import pytest

from helpers.form_helpers import validate_name_characters, NAME_SCHEMA_PATTERN, wtforms_validate_name


class TestValidateNameCharacters:
    @pytest.mark.parametrize("name", [
        "#1", "Leader #1", "a/b", "a\\b", "100%", "query?", "cost$",
        "line\nbreak", "tab\there", "null\x00byte",
    ])
    def test_rejects_url_and_db_breaking_characters(self, name):
        valid, error = validate_name_characters(name)
        assert valid is False
        assert error is not None

    @pytest.mark.parametrize("name", [
        "Trapper's Den", "Jean-Luc", "St. Whatever", "Xi'an Machinists",
        "Eternal Chancellorship of the Black Scythe of Alturus",
        "Nation (Exiled)", "O'Brien & Sons", "Test123", "1",
    ])
    def test_allows_ordinary_punctuation_and_unicode_friendly_names(self, name):
        valid, error = validate_name_characters(name)
        assert valid is True
        assert error is None

    def test_empty_string_is_valid(self):
        # Emptiness is DataRequired's job, not the character filter's.
        valid, error = validate_name_characters("")
        assert valid is True


class TestWtformsValidateName:
    def test_raises_on_bad_name(self):
        from wtforms.validators import ValidationError

        class _Field:
            data = "Leader #1"

        with pytest.raises(ValidationError):
            wtforms_validate_name(None, _Field())

    def test_passes_on_good_name(self):
        class _Field:
            data = "Trapper's Den"

        wtforms_validate_name(None, _Field())  # should not raise


class TestSchemaPatternInjection:
    def test_pattern_matches_good_names_and_rejects_bad_ones(self):
        import re
        pattern = re.compile(NAME_SCHEMA_PATTERN)
        assert pattern.match("Trapper's Den")
        assert pattern.match("Jean-Luc")
        assert not pattern.match("Leader #1")
        assert not pattern.match("a/b")

    def test_every_schema_with_a_name_field_has_the_pattern_applied(self):
        from app_core import category_data
        for data_type, info in category_data.items():
            schema = info.get("schema")
            if not schema:
                continue
            name_prop = schema.get("properties", {}).get("name")
            if name_prop is not None:
                assert name_prop.get("pattern") == NAME_SCHEMA_PATTERN, (
                    f"{data_type}'s name field is missing the name-character pattern"
                )
