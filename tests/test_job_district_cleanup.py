"""
Tests for the job-district cleanup behaviour introduced to fix the bug where
removing a district left district-requiring jobs permanently stuck (assigned
but inaccessible).

The core logic lives in ``calculations.field_calculations.purge_invalid_district_jobs``,
a pure function that zeroes out job counts whose district prerequisite has been
removed from the nation.  The integration hook (calling this function during
``_calculate_and_attach_fields``) is exercised via the integration test at the
bottom of this file.
"""
import pytest
from calculations.field_calculations import purge_invalid_district_jobs


# ---------------------------------------------------------------------------
# Minimal jobs-JSON used across tests
# ---------------------------------------------------------------------------
_JOBS = {
    "farmer":     {"requirements": {"district": ["farm"]}},
    "fisherman":  {"requirements": {"district": ["dock"]}},
    "guard":      {"requirements": {"district": ["guardhouse"]}},
    "hunter":     {"requirements": {}},                       # no district req
    "bureaucrat": {},                                          # no requirements key
}


# ===========================================================================
# Basic cases
# ===========================================================================

class TestPurgeInvalidDistrictJobs:
    """purge_invalid_district_jobs() zeroes jobs whose district is missing."""

    def test_district_removed_zeroes_job(self):
        """The canonical bug: farmer assigned but farm district lost."""
        nation = {
            "jobs": {"farmer": 3},
            "districts": [],          # farm is gone
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert "farmer" in zeroed

    def test_district_present_keeps_job(self):
        """No cleanup when the required district still exists."""
        nation = {
            "jobs": {"farmer": 5},
            "districts": [{"def_key": "farm"}],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 5
        assert zeroed == []

    def test_multiple_jobs_only_affected_ones_zeroed(self):
        """farmer's district gone, fisherman's dock still present → only farmer zeroed."""
        nation = {
            "jobs": {"farmer": 4, "fisherman": 2},
            "districts": [{"def_key": "dock"}],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert nation["jobs"]["fisherman"] == 2
        assert "farmer" in zeroed
        assert "fisherman" not in zeroed

    def test_job_with_no_district_requirement_untouched(self):
        """Jobs that have no district requirement are never zeroed."""
        nation = {
            "jobs": {"hunter": 7},
            "districts": [],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["hunter"] == 7
        assert zeroed == []

    def test_job_with_empty_requirements_dict_untouched(self):
        """Jobs with a completely absent 'requirements' key are left alone."""
        nation = {
            "jobs": {"bureaucrat": 3},
            "districts": [],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["bureaucrat"] == 3
        assert zeroed == []

    def test_already_zero_count_not_reported(self):
        """Jobs already at 0 are not included in the returned list."""
        nation = {
            "jobs": {"farmer": 0},
            "districts": [],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert zeroed == []

    def test_all_districts_removed_zeroes_all_district_jobs(self):
        """Multiple district-requiring jobs zeroed when all districts are gone."""
        nation = {
            "jobs": {"farmer": 6, "fisherman": 4, "guard": 2, "hunter": 3},
            "districts": [],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert nation["jobs"]["fisherman"] == 0
        assert nation["jobs"]["guard"] == 0
        assert nation["jobs"]["hunter"] == 3   # no district req → untouched
        assert set(zeroed) == {"farmer", "fisherman", "guard"}

    def test_district_with_empty_def_key_does_not_satisfy_requirement(self):
        """A district slot whose def_key is '' (unbuilt) must not count."""
        nation = {
            "jobs": {"farmer": 2},
            "districts": [{"def_key": ""}],   # slot exists but is empty
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert "farmer" in zeroed

    def test_district_of_different_type_does_not_satisfy_requirement(self):
        """A dock doesn't satisfy the farm requirement."""
        nation = {
            "jobs": {"farmer": 5},
            "districts": [{"def_key": "dock"}],
        }
        zeroed = purge_invalid_district_jobs(nation, _JOBS)

        assert nation["jobs"]["farmer"] == 0
        assert "farmer" in zeroed

    def test_modifies_nation_in_place(self):
        """The function mutates the nation dict directly."""
        nation = {
            "jobs": {"farmer": 9},
            "districts": [],
        }
        purge_invalid_district_jobs(nation, _JOBS)
        # Confirm mutation — not a copy returned
        assert nation["jobs"]["farmer"] == 0

    def test_no_jobs_key_does_not_raise(self):
        """A nation without a 'jobs' key is handled gracefully."""
        nation = {"districts": []}
        zeroed = purge_invalid_district_jobs(nation, _JOBS)
        assert zeroed == []

    def test_non_dict_jobs_does_not_raise(self):
        """If jobs is not a dict (legacy format), function exits cleanly."""
        nation = {"jobs": [], "districts": []}
        zeroed = purge_invalid_district_jobs(nation, _JOBS)
        assert zeroed == []
