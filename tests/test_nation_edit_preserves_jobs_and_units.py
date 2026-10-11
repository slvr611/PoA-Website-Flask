"""
Regression test for a real production incident (2026-10-03, change
#6ac1315eeb5af7213139732b): a player's jobs AND all three unit dicts
(land/naval/support) were wiped to {} by a nation edit that never touched
any of those fields.

Root cause: FormGenerator.get_form (forms.py) only pulls job_details/
land_unit_details/naval_unit_details/support_unit_details from its `item`
argument — when item is None, all four default to {}. Those details drive
how many actual WTForms sub-fields NationForm.create_form_class (and
JobForm.create_form_class, for the dedicated jobs page) build for jobs/
land_units/naval_units/support_units: with empty details, the sub-forms
get built with ZERO fields, so submitted values (e.g. "jobs-fisherman":
"19") have nothing to bind to, and form.data comes back with a genuinely
empty {} for each — regardless of what was actually submitted.

Three call sites in routes/nation_routes.py built their POST-handling form
via form_generator.get_form(..., formdata=request.form) WITHOUT item=nation
(while their sibling GET handlers correctly did pass it):
  - nation_edit_request (player-submitted change request)
  - nation_edit_approve (admin direct save)
  - nation_edit_jobs_approve (dedicated "Edit Jobs" page save — this one
    has no protective guard against an empty result at all)

Fixed by passing item=nation in all three.

Note on test job/unit key names: JobAssignmentDict/LandUnitAssignmentDict
etc. are mutated in place by create_form_class (setattr on the shared
class, never reset) — fields accumulate across the whole test process
rather than being rebuilt from scratch each call. Using obviously
test-local, unique key names (rather than real job/unit keys that some
OTHER test file might also use) avoids any cross-test leakage affecting
these assertions.
"""
from werkzeug.datastructures import MultiDict

from app_core import category_data
from forms import form_generator


def _nations_schema():
    return category_data["nations"]["schema"]


class TestFormGeneratorGetFormRequiresItemToPreserveJobsAndUnits:
    def test_without_item_jobs_come_back_empty_despite_submitted_values(self, flask_app):
        """Reproduces the actual bug: the exact same submitted form data
        that SHOULD preserve job counts comes back wiped to {} when item
        isn't passed — this is the defect, not the desired behavior."""
        schema = _nations_schema()
        formdata = MultiDict({"jobs-regr_test_job_wophe": "19", "csrf_token": "x"})
        with flask_app.test_request_context():
            form = form_generator.get_form("jobs", schema, formdata=formdata)
        assert "regr_test_job_wophe" not in form.data.get("jobs", {})

    def test_with_item_jobs_are_correctly_preserved_from_submitted_data(self, flask_app):
        schema = _nations_schema()
        nation = {"name": "Testland", "job_details": {"regr_test_job_zufon": {}}}
        formdata = MultiDict({"jobs-regr_test_job_zufon": "19", "csrf_token": "x"})
        with flask_app.test_request_context():
            form = form_generator.get_form("jobs", schema, item=nation, formdata=formdata)
        assert form.data.get("jobs", {}).get("regr_test_job_zufon") == 19

    def test_nations_form_without_item_wipes_all_four_dicts(self, flask_app):
        """The main NationForm path (used by nation_edit_request/
        nation_edit_approve) wipes all four — jobs, land_units,
        naval_units, AND support_units — exactly as seen in the real
        incident, where every one of them was zeroed simultaneously."""
        schema = _nations_schema()
        formdata = MultiDict({
            "name": "Testland",
            "jobs-regr_test_job_xalur": "19",
            "land_units-regr_test_unit_xalur": "5",
            "csrf_token": "x",
        })
        with flask_app.test_request_context():
            form = form_generator.get_form("nations", schema, formdata=formdata)
        # JobAssignmentDict/LandUnitAssignmentDict are shared, cumulatively-
        # mutated classes (see module docstring) — other tests in this
        # process may have left unrelated zeroed keys behind, so check the
        # specific submitted key is missing/dropped rather than asserting
        # the whole dict is empty.
        assert "regr_test_job_xalur" not in form.data.get("jobs", {})
        assert "regr_test_unit_xalur" not in form.data.get("land_units", {})
        assert form.data.get("naval_units") == {}
        assert form.data.get("support_units") == {}

    def test_nations_form_with_item_preserves_all_four_dicts(self, flask_app):
        schema = _nations_schema()
        nation = {
            "name": "Testland",
            "job_details": {"regr_test_job_brivak": {}},
            "land_unit_details": {"regr_test_unit_brivak": {}},
            "naval_unit_details": {},
            "support_unit_details": {},
        }
        formdata = MultiDict({
            "name": "Testland",
            "jobs-regr_test_job_brivak": "19",
            "land_units-regr_test_unit_brivak": "5",
            "csrf_token": "x",
        })
        with flask_app.test_request_context():
            form = form_generator.get_form("nations", schema, item=nation, formdata=formdata)
        assert form.data.get("jobs", {}).get("regr_test_job_brivak") == 19
        assert form.data.get("land_units", {}).get("regr_test_unit_brivak") == 5


class TestNationRoutesPassItemToFormGenerator:
    """Guards the three call sites themselves against a future regression
    (e.g. someone 'simplifying' the call and dropping item= again) by
    asserting the actual source lines, since the bug is purely about what
    gets PASSED to form_generator.get_form, not something easily observed
    through the full route without a large amount of unrelated mocking."""

    def _source(self):
        import importlib
        import inspect
        nr_module = importlib.import_module("routes.nation_routes")
        return inspect.getsource(nr_module)

    def test_nation_edit_request_and_approve_pass_item(self):
        src = self._source()
        occurrences = src.count(
            'form_generator.get_form("nations", schema, item=nation, formdata=request.form)'
        )
        assert occurrences == 2, "expected both nation_edit_request and nation_edit_approve to pass item=nation"

    def test_nation_edit_jobs_approve_passes_item(self):
        src = self._source()
        assert 'form_generator.get_form("jobs", schema, item=nation, formdata=request.form)' in src
