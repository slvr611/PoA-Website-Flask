"""
Regression test for check_job_requirements's "wonder" requirement branch
(calculations/field_calculations.py).

Bug: wonderdb.find_one({"name": wonder_name}) correctly returns None when no
nation has built that wonder yet (the normal case for most wonders most of
the time) — but the code immediately called .get("owner_nation", "") on
that None, crashing with AttributeError: 'NoneType' object has no attribute
'get'. This could crash calculate_all_fields in production for any nation
with a job requiring an unbuilt wonder.

It went undetected because ~19 unrelated test files were — unknown to their
authors — silently hitting the REAL production `wonders` collection (via a
pytest/dotenv MONGO_URI leak, fixed separately in tests/conftest.py) instead
of an empty local one, so a real wonder document with a real owner_nation
was usually found, and the None path was never actually exercised until the
leak was sealed.
"""
from unittest.mock import patch

from calculations.field_calculations import check_job_requirements


def _patched_wonders(wonders_db):
    return patch("calculations.field_calculations.category_data", {
        "wonders": {"database": wonders_db},
    })


class _FakeWondersDB:
    """Minimal find_one stand-in — avoids pulling in mongomock just for a
    single-collection, single-method need."""
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query):
        name = query.get("name")
        return next((d for d in self._docs if d.get("name") == name), None)


class TestCheckJobRequirementsWonder:
    def test_unbuilt_wonder_is_an_unmet_requirement_not_a_crash(self):
        """The core regression: no nation has built "Grand Archive" yet."""
        target = {"_id": "nation123", "districts": [], "region": ""}
        job_details = {"requirements": {"wonder": ["Grand Archive"]}}
        with _patched_wonders(_FakeWondersDB([])):
            result = check_job_requirements(target, job_details, {})
        assert result is False

    def test_wonder_owned_by_this_nation_meets_requirement(self):
        target = {"_id": "nation123", "districts": [], "region": ""}
        job_details = {"requirements": {"wonder": ["Grand Archive"]}}
        wonders = _FakeWondersDB([{"name": "Grand Archive", "owner_nation": "nation123"}])
        with _patched_wonders(wonders):
            result = check_job_requirements(target, job_details, {})
        assert result is True

    def test_wonder_owned_by_another_nation_does_not_meet_requirement(self):
        target = {"_id": "nation123", "districts": [], "region": ""}
        job_details = {"requirements": {"wonder": ["Grand Archive"]}}
        wonders = _FakeWondersDB([{"name": "Grand Archive", "owner_nation": "someone_else"}])
        with _patched_wonders(wonders):
            result = check_job_requirements(target, job_details, {})
        assert result is False
