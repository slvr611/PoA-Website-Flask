"""
Regression tests for check_district_requirements's uniqueness gate being
map_count-aware.

Bug: score_buildable_districts already gated new district builds on
existing_count >= map_count (its own advisory pre-check, allowing e.g. a
second "outpost" since district_defs.outpost.map_count == 2), but then
unconditionally called check_district_requirements, whose OWN uniqueness
check only looked at allow_multiple (always False for every district def
in this game, including outpost) and blocked ANY existing instance
regardless of map_count. That stricter, count-blind inner check is what
actually gates every caller, so no nation could ever legally hold more
than one outpost despite map_count explicitly allowing 2 — confirmed live:
every nation with an outpost had exactly 1, never 2.
"""
from unittest.mock import MagicMock, patch

import calculations.field_calculations as fc


def _patched(test_db, tile_type_ok=True):
    return (
        patch.object(fc, "mongo", MagicMock(db=test_db)),
        patch.object(fc, "_nation_has_tile_type", return_value=tile_type_ok),
    )


class TestCheckDistrictRequirementsMapCount:
    def test_second_instance_allowed_up_to_map_count(self, test_db):
        test_db["district_defs"].insert_one({"key": "outpost"})
        nation = {"name": "Test Nation", "districts": [{"_id": "d1", "def_key": "outpost"}]}
        dd = {"key": "outpost", "allow_multiple": False, "map_count": 2, "tile_requirement": "land"}
        p1, p2 = _patched(test_db)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is True

    def test_third_instance_blocked_once_map_count_is_reached(self, test_db):
        test_db["district_defs"].insert_one({"key": "outpost"})
        nation = {
            "name": "Test Nation",
            "districts": [{"_id": "d1", "def_key": "outpost"}, {"_id": "d2", "def_key": "outpost"}],
        }
        dd = {"key": "outpost", "allow_multiple": False, "map_count": 2, "tile_requirement": "land"}
        p1, p2 = _patched(test_db)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is False

    def test_default_map_count_of_one_still_blocks_a_second_instance(self, test_db):
        """A district def with no map_count set (defaults to 1) must keep
        behaving exactly as before this fix — one instance, then blocked."""
        test_db["district_defs"].insert_one({"key": "forge"})
        nation = {"name": "Test Nation", "districts": [{"_id": "d1", "def_key": "forge"}]}
        dd = {"key": "forge", "allow_multiple": False, "tile_requirement": "land"}
        p1, p2 = _patched(test_db)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is False

    def test_no_existing_instance_is_always_allowed(self, test_db):
        nation = {"name": "Test Nation", "districts": []}
        dd = {"key": "forge", "allow_multiple": False, "map_count": 1, "tile_requirement": "land"}
        p1, p2 = _patched(test_db)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is True

    def test_allow_multiple_ignores_map_count_cap_entirely(self, test_db):
        nation = {
            "name": "Test Nation",
            "districts": [{"_id": f"d{i}", "def_key": "farm"} for i in range(5)],
        }
        dd = {"key": "farm", "allow_multiple": True, "map_count": 1, "tile_requirement": "land"}
        p1, p2 = _patched(test_db)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is True

    def test_tile_type_gate_still_applies_before_the_count_check(self, test_db):
        nation = {"name": "Test Nation", "districts": []}
        dd = {"key": "dock", "allow_multiple": False, "map_count": 1, "tile_requirement": "coastal"}
        p1, p2 = _patched(test_db, tile_type_ok=False)
        with p1, p2:
            assert fc.check_district_requirements(nation, dd) is False
