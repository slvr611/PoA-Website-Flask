"""
Regression/feature tests for the new "per_x_minority_pops" modifier scaling
method: a minority pop is one of a nation's own pops whose culture OR
religion differs from the nation's primary — counted once per pop.

This is distinct from two existing, similarly-named mechanisms that could
easily be confused with it:
  - per_x_minority_cultures/_religions count DISTINCT non-primary culture/
    religion VALUES present (culture_count/religion_count - 1), not actual
    pop headcount — a nation with 50 pops of one non-primary culture scores
    the same "1 minority culture" as a nation with just 1 such pop.
  - per_x_foreign_culture_pops/_religion_pops count OTHER nations' pops that
    happen to share THIS nation's primary culture/religion (a diaspora/
    "foreign kin" concept) — not this nation's own pops at all.

minority_pop_count (calculations/field_calculations.py's
_build_nation_calc_cache) and per_x_minority_pops (calculations/
scaling_methods.py) are the new pieces; this file covers both the cache
computation and the scaling method, plus the full modifier pipeline.
"""
from unittest.mock import patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
import calculations.scaling_methods as sm
from app_core import category_data

NATIONS_SCHEMA = category_data["nations"]["schema"]

# primary_culture/primary_religion (on nations) and culture/religion (on
# pops) are all linked_object references (ObjectId strings into the
# cultures/religions collections) — calculate_all_fields's generic
# collect_external_requirements pass tries to ObjectId()-parse every
# linked_object field it finds, so these must look like real ids, not
# human-readable names, even though the minority comparison itself is a
# plain string equality check that doesn't care what the ids resolve to.
HUMAN = str(ObjectId())
ELVEN = str(ObjectId())
DWARVEN = str(ObjectId())
SUN_CULT = str(ObjectId())
MOON_CULT = str(ObjectId())


def _seed(nation_overrides, pops):
    """Build a mongomock db with one nation (+ overrides) and its pops.
    Returns (db, nation_id)."""
    client = mongomock.MongoClient()
    db = client["test"]

    nation_id = ObjectId()
    nation_doc = {"_id": nation_id, "name": "Testland", "modifiers": []}
    nation_doc.update(nation_overrides)
    db["nations"].insert_one(nation_doc)
    for pop in pops:
        db["pops"].insert_one({"nation": str(nation_id), **pop})
    return db, nation_id


def _build_calc_cache(nation_overrides, pops):
    """Calls _build_nation_calc_cache directly — the cache dict it returns
    is deliberately stripped from calculate_all_fields's final output
    (target.pop("_calc_cache", None) near the end), so this is the only way
    to inspect minority_pop_count itself rather than its downstream effect
    on some other field."""
    db, nation_id = _seed(nation_overrides, pops)
    fake_mongo = type("FakeMongo", (), {"db": db})()
    with patch.object(fc, "mongo", fake_mongo), \
         patch("calculations.field_calculations.category_data", {
             **category_data,
             "pops": {**category_data["pops"], "database": db["pops"]},
             "races": {**category_data["races"], "database": db["races"]},
         }):
        nation_doc = db["nations"].find_one({"_id": nation_id})
        return fc._build_nation_calc_cache(nation_doc)


def _calc_with_pops(nation_overrides, pops):
    db, nation_id = _seed(nation_overrides, pops)
    fake_mongo = type("FakeMongo", (), {"db": db})()
    with patch.object(fc, "mongo", fake_mongo), \
         patch("helpers.disease_helpers.mongo", fake_mongo), \
         patch("calculations.field_calculations.category_data", {
             **category_data,
             "pops": {**category_data["pops"], "database": db["pops"]},
             "races": {**category_data["races"], "database": db["races"]},
             "characters": {**category_data["characters"], "database": db["characters"]},
         }):
        nation_doc = db["nations"].find_one({"_id": nation_id})
        return fc.calculate_all_fields(nation_doc, NATIONS_SCHEMA, "nation")


class TestMinorityPopCountCache:
    def test_pops_matching_both_primary_culture_and_religion_are_not_minority(self):
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [{"race": "r1", "culture": HUMAN, "religion": SUN_CULT}] * 3,
        )
        assert cache["minority_pop_count"] == 0

    def test_non_primary_culture_only_counts_as_minority(self):
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [{"race": "r1", "culture": ELVEN, "religion": SUN_CULT}],
        )
        assert cache["minority_pop_count"] == 1

    def test_non_primary_religion_only_counts_as_minority(self):
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [{"race": "r1", "culture": HUMAN, "religion": MOON_CULT}],
        )
        assert cache["minority_pop_count"] == 1

    def test_differing_in_both_culture_and_religion_counts_once_not_twice(self):
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [{"race": "r1", "culture": ELVEN, "religion": MOON_CULT}],
        )
        assert cache["minority_pop_count"] == 1

    def test_mixed_population_counts_only_the_minority_pops(self):
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [
                {"race": "r1", "culture": HUMAN, "religion": SUN_CULT},
                {"race": "r1", "culture": HUMAN, "religion": SUN_CULT},
                {"race": "r1", "culture": ELVEN, "religion": SUN_CULT},
                {"race": "r1", "culture": HUMAN, "religion": MOON_CULT},
                {"race": "r1", "culture": DWARVEN, "religion": MOON_CULT},
            ],
        )
        assert cache["minority_pop_count"] == 3

    def test_not_the_same_as_distinct_minority_culture_count(self):
        """20 pops all sharing ONE non-primary culture: minority_pop_count
        (headcount) is 20, while culture_count (what per_x_minority_cultures
        is built from — distinct culture VALUES actually present among the
        pops, not including the nation's primary_culture unless some pop
        actually has it) only ever sees "1 distinct culture" here."""
        cache = _build_calc_cache(
            {"primary_culture": HUMAN, "primary_religion": SUN_CULT},
            [{"race": "r1", "culture": ELVEN, "religion": SUN_CULT}] * 20,
        )
        assert cache["minority_pop_count"] == 20
        assert cache["culture_count"] == 1  # just Elven — no pop actually has the primary culture


class TestPerXMinorityPopsScalingMethod:
    def test_registered_in_scaling_methods(self):
        assert sm.SCALING_METHODS.get("per_x_minority_pops") is sm.per_x_minority_pops

    def test_reads_minority_pop_count_from_calc_cache(self):
        target = {"_calc_cache": {"minority_pop_count": 10}}
        assert sm.per_x_minority_pops(target, scaling_x=1) == 10
        assert sm.per_x_minority_pops(target, scaling_x=5) == 2

    def test_missing_cache_defaults_to_zero(self):
        assert sm.per_x_minority_pops({}, scaling_x=1) == 0


class TestPerXMinorityPopsModifierEndToEnd:
    def test_modifier_scales_with_minority_pop_headcount(self):
        """4 minority pops, per_x_minority_pops scaling_x=2 -> multiplier 2,
        on a karma modifier of value 3 -> +6 karma."""
        calc = _calc_with_pops(
            {
                "primary_culture": HUMAN, "primary_religion": SUN_CULT,
                "modifiers": [{
                    "modifier_type": "karma", "value": 3, "scope": "nation_self",
                    "scaling": "per_x_minority_pops", "scaling_x": 2,
                }],
            },
            [
                {"race": "r1", "culture": HUMAN, "religion": SUN_CULT},
                {"race": "r1", "culture": ELVEN, "religion": SUN_CULT},
                {"race": "r1", "culture": ELVEN, "religion": SUN_CULT},
                {"race": "r1", "culture": HUMAN, "religion": MOON_CULT},
                {"race": "r1", "culture": DWARVEN, "religion": MOON_CULT},
            ],
        )
        assert calc.get("karma", 0) == 6
