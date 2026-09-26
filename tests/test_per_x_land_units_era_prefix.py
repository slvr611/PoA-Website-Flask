"""
Regression test for a real bug found live: Dyeak's Range district
("Resource Consumption -1 per Land Unit (Wood)", district_defs key "range",
modifier {"modifier_type": "resource_consumption", "value": -1.0,
"resource": "wood", "scaling": "per_x_land_units", "scaling_extra":
"ranged"}) was not reducing wood consumption at all, even with 8 "Classical
Light Archers" (a real ranged unit) on the books.

Root cause: calculations/scaling_methods.py's _count_filtered (used by
per_x_land_units/per_x_naval_units/per_x_units) looked up each land_units/
naval_units dict key directly against a {unit.name: unit_doc} cache built
straight from the units collection's own "name" field. But
calculations/field_calculations.py's load_db_units — the function that
actually produces the key format nation.land_units/naval_units use —
era-prefixes a unit's key ("Classical Light Archers") whenever its base
name ("Light Archers") collides across multiple eras (Dyeak has both
"Classical Light Archers" and "Medieval Light Archers" on the books). A
raw name-keyed lookup for "Classical Light Archers" always missed (only
"Light Archers" exists in the units collection), silently falling back to
an empty unit def — melee/cavalry/ranged/magical all read as False for
every era-collision unit, regardless of its real stats. Same bug affects
Barracks (-1 stone per infantry) and Stables (-1 mounts per cavalry) —
identical per_x_land_units scaling shape.

Fixed by having _get_unit_defs_cache reuse load_db_units() itself, so the
keys these two lookups use always match by construction.
"""
import pytest

import calculations.field_calculations as fc
from calculations.scaling_methods import per_x_land_units, per_x_naval_units, per_x_units


@pytest.fixture(autouse=True)
def _reset_unit_cache():
    """load_db_units falls back to a threading.local cache outside a Flask
    request context (see field_calculations._unit_cache_local's own
    comment) — reset it before every test so one test's fake units
    collection can never leak into the next (same reasoning as this
    session's other _unit_cache_local-style fixtures)."""
    for unit_type in (None, "land", "naval", "support"):
        try:
            delattr(fc._unit_cache_local, f"_unit_cache_{unit_type}")
        except AttributeError:
            pass
    yield


def _fake_units_collection(docs):
    class _FakeCollection:
        def find(self, *args, **kwargs):
            return list(docs)

    return _FakeCollection()


# Two "Light Archers" units across different eras, forcing load_db_units to
# era-prefix both — exactly Dyeak's real shape.
_ERA_COLLIDING_UNITS = [
    {"name": "Light Archers", "era": "Classical", "unit_type": "Land", "ranged": True, "melee": False, "cavalry": False},
    {"name": "Light Archers", "era": "Medieval", "unit_type": "Land", "ranged": True, "melee": False, "cavalry": False},
    {"name": "Heavy Infantry", "era": "Classical", "unit_type": "Land", "ranged": False, "melee": True, "cavalry": False},
]


class TestCountFilteredEraPrefixedUnits:
    def test_era_prefixed_ranged_unit_is_counted(self, monkeypatch):
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {"land_units": {"Classical Light Archers": 8, "Heavy Infantry": 3}}
        assert per_x_land_units(target, scaling_extra="ranged") == 8

    def test_the_other_era_variant_is_also_counted(self, monkeypatch):
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {"land_units": {"Medieval Light Archers": 5}}
        assert per_x_land_units(target, scaling_extra="ranged") == 5

    def test_infantry_subtype_unaffected_by_ranged_units(self, monkeypatch):
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {"land_units": {"Classical Light Archers": 8, "Heavy Infantry": 3}}
        assert per_x_land_units(target, scaling_extra="infantry") == 3

    def test_range_district_style_modifier_now_reduces_wood_consumption(self, monkeypatch):
        """End-to-end shape of the actual bug report: value=-1 scaled by
        per_x_land_units(scaling_extra='ranged') against 8 archers must
        yield -8, not 0."""
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {"land_units": {"Classical Light Archers": 8}}
        modifier = {
            "modifier_type": "resource_consumption", "value": -1.0, "resource": "wood",
            "scaling": "per_x_land_units", "scaling_extra": "ranged", "scope": "nation_self",
        }
        totals = fc.sum_modifier_totals([modifier], target)
        assert totals["wood_consumption"] == -8

    def test_naval_units_also_resolve_era_prefixed_keys(self, monkeypatch):
        naval_units = [
            {"name": "Light Archers", "era": "Classical", "unit_type": "Naval", "ranged": True, "melee": False, "cavalry": False},
            {"name": "Light Archers", "era": "Medieval", "unit_type": "Naval", "ranged": True, "melee": False, "cavalry": False},
        ]
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(naval_units)},
        })
        target = {"naval_units": {"Classical Light Archers": 4}}
        assert per_x_naval_units(target, scaling_extra="ranged") == 4

    def test_per_x_units_sums_land_and_naval(self, monkeypatch):
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {
            "land_units": {"Classical Light Archers": 8},
            "naval_units": {},
        }
        assert per_x_units(target, scaling_extra="ranged") == 8

    def test_non_colliding_unit_name_needs_no_era_prefix(self, monkeypatch):
        """A unit whose base name never collides across eras is keyed by
        its plain name in load_db_units — must still resolve correctly."""
        monkeypatch.setattr(fc, "category_data", {
            **fc.category_data, "units": {"database": _fake_units_collection(_ERA_COLLIDING_UNITS)},
        })
        target = {"land_units": {"Heavy Infantry": 3}}
        assert per_x_land_units(target, scaling_extra="infantry") == 3
