"""
Regression tests for per-nation disease immunity (nations.json's new
disease_immunities field): certain nations can be marked immune to a named
disease (e.g. "Vampirism") so they can never be newly infected with it.

The guard lives in infect_random_pops (helpers/disease_helpers.py) — the
single function every infection pathway funnels through: a nation's own
outbreak spreading to more of its pops, cross-nation spread landing on it
from elsewhere (attempt_dual_spread/pick_external_spread_target), the admin
"Infect" button (routes/disease_routes.py), and any seed script. Checking it
there rather than in each tick function means every pathway is covered at
once and a future pathway can't forget to check it.

Immunity only blocks NEW infections — a nation that already carries the
disease before immunity is added keeps its existing infected pops (nothing
here cures or removes anyone).
"""
import pytest
from unittest.mock import patch
from bson import ObjectId

import helpers.disease_helpers as dh


@pytest.fixture
def patch_disease_mongo(mock_mongo):
    with patch("helpers.disease_helpers.mongo", mock_mongo):
        yield mock_mongo


def _make_disease(name="Vampirism", **overrides):
    disease = {
        "_id": ObjectId(),
        "name": name,
        "rating": "Terrible",
        "job_type": "Vampire",
        "infectivity": "Low",
        "difficulty": "Simple",
        "changes_race": False,
    }
    disease.update(overrides)
    return disease


def _seed_pops(test_db, nation_id_str, count, **fields):
    for _ in range(count):
        doc = {"nation": nation_id_str, "race": "r1", "culture": "c", "religion": "r"}
        doc.update(fields)
        test_db["pops"].insert_one(doc)


class TestNationIsImmuneToDisease:
    def test_true_when_disease_name_listed(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Sanctum", "disease_immunities": ["Vampirism"]})
        disease = _make_disease()
        assert dh.nation_is_immune_to_disease(str(nation_id), disease) is True

    def test_false_when_disease_name_not_listed(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Sanctum", "disease_immunities": ["Mire Madness"]})
        disease = _make_disease()
        assert dh.nation_is_immune_to_disease(str(nation_id), disease) is False

    def test_false_when_nation_has_no_immunities_field(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Sanctum"})
        disease = _make_disease()
        assert dh.nation_is_immune_to_disease(str(nation_id), disease) is False

    def test_false_for_invalid_or_missing_nation_id(self, patch_disease_mongo, test_db):
        disease = _make_disease()
        assert dh.nation_is_immune_to_disease("not-an-object-id", disease) is False
        assert dh.nation_is_immune_to_disease("", disease) is False


class TestInfectRandomPopsRespectsImmunity:
    def test_immune_nation_gets_no_new_infections(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Sanctum", "disease_immunities": ["Vampirism"]})
        disease = _make_disease()
        _seed_pops(test_db, str(nation_id), 5)

        infected = dh.infect_random_pops(str(nation_id), disease, 10)

        assert infected == 0
        assert test_db["pops"].count_documents({"diseases": str(disease["_id"])}) == 0

    def test_non_immune_nation_still_infects_normally(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Open Vale", "disease_immunities": ["Mire Madness"]})
        disease = _make_disease()
        _seed_pops(test_db, str(nation_id), 5)

        infected = dh.infect_random_pops(str(nation_id), disease, 3)

        assert infected == 3
        assert test_db["pops"].count_documents({"nation": str(nation_id), "diseases": str(disease["_id"])}) == 3

    def test_nation_with_no_immunities_field_still_infects_normally(self, patch_disease_mongo, test_db):
        nation_id = ObjectId()
        test_db.nations.insert_one({"_id": nation_id, "name": "Open Vale"})
        disease = _make_disease()
        _seed_pops(test_db, str(nation_id), 5)

        infected = dh.infect_random_pops(str(nation_id), disease, 3)
        assert infected == 3

    def test_immunity_does_not_touch_pre_existing_infected_pops(self, patch_disease_mongo, test_db):
        """Marking a nation immune must not retroactively cure/remove pops it
        already infected before immunity was added — only new infection
        attempts are blocked."""
        nation_id = ObjectId()
        disease = _make_disease()
        _seed_pops(test_db, str(nation_id), 2, diseases=[str(disease["_id"])])
        test_db.nations.insert_one({"_id": nation_id, "name": "Sanctum", "disease_immunities": ["Vampirism"]})

        assert test_db["pops"].count_documents({"nation": str(nation_id), "diseases": str(disease["_id"])}) == 2
        # A further infection attempt against the same nation still fails...
        _seed_pops(test_db, str(nation_id), 3)  # healthy pops available
        assert dh.infect_random_pops(str(nation_id), disease, 10) == 0
        # ...but the pre-existing infected pops are untouched.
        assert test_db["pops"].count_documents({"nation": str(nation_id), "diseases": str(disease["_id"])}) == 2


class TestAttemptDualSpreadRespectsExternalImmunity:
    def test_external_target_immunity_causes_external_leg_to_fail(self, patch_disease_mongo, test_db):
        target_nation_id = ObjectId()
        test_db.nations.insert_one({
            "_id": target_nation_id, "name": "Sanctum", "disease_immunities": ["Vampirism"],
        })
        disease = _make_disease()
        _seed_pops(test_db, str(target_nation_id), 5)

        with patch("helpers.disease_helpers.pick_external_spread_target", return_value={"_id": target_nation_id, "name": "Sanctum"}), \
             patch("random.random", return_value=0.9):  # force external branch to be tried first
            succeeded, target_type, ext_target = dh.attempt_dual_spread(
                {"name": "Source"}, disease, internal_fn=lambda: False
            )

        assert succeeded is False
        assert test_db["pops"].count_documents({"nation": str(target_nation_id), "diseases": str(disease["_id"])}) == 0
