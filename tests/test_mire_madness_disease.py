"""
Tests for the new permanent disease mechanics added for "Mire Madness
[Apocalyptic]": multi-disease co-infection, region-scoped infectivity,
prosperity_role-conditional job overrides, and the new
nation_disease_effects_tick (resource windfall + vassal compliance loss).

These are general engine capabilities (not hardcoded to Mire Madness by
name) — every disease opts in via new, default-off schema fields.
"""
from unittest.mock import patch
from bson import ObjectId

import pytest

import helpers.disease_helpers as dh


@pytest.fixture
def patch_disease_mongo(mock_mongo):
    with patch("helpers.disease_helpers.mongo", mock_mongo):
        yield mock_mongo


def _make_disease(**overrides):
    disease = {
        "_id": ObjectId(),
        "name": "Mire Madness",
        "rating": "Apocalyptic",
        "job_type": "Paranoid",
        "job_production": [{"key": "max_stability_gain_chance", "value": -0.05}],
        "job_upkeep": [],
        "infectivity": "Highly",
        "difficulty": "Impossible",
        "cure_progress": 0,
        "cured": False,
        "stages": [],
    }
    disease.update(overrides)
    return disease


def _seed_pop(db, nation_id, diseases=None, **extra):
    doc = {"nation": str(nation_id), "race": "", "culture": "c", "religion": "r"}
    if diseases is not None:
        doc["diseases"] = [str(d) for d in diseases]
    doc.update(extra)
    return db["pops"].insert_one(doc).inserted_id


# ---------------------------------------------------------------------------
# Multi-disease co-infection
# ---------------------------------------------------------------------------

class TestMultiDiseaseCoInfection:
    def test_pop_can_carry_two_diseases_at_once(self, patch_disease_mongo, test_db):
        d1, d2 = _make_disease(name="Rot"), _make_disease(name="Mire Madness")
        pop_id = _seed_pop(test_db, "n1")
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, d1)
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, d2)

        infected = test_db["pops"].find_one({"_id": pop_id})
        assert set(infected["diseases"]) == {str(d1["_id"]), str(d2["_id"])}

    def test_infection_counts_count_a_coinfected_pop_toward_both_diseases(self, patch_disease_mongo, test_db):
        d1, d2 = ObjectId(), ObjectId()
        _seed_pop(test_db, "n1", diseases=[d1, d2])
        _seed_pop(test_db, "n1", diseases=[d1])
        counts = dh.get_nation_infection_counts("n1")
        assert counts == {str(d1): 2, str(d2): 1}

    def test_collect_disease_effects_produces_one_job_per_disease_for_coinfected_nation(
        self, patch_disease_mongo, test_db, flask_app
    ):
        d1 = _make_disease(name="Rot", job_type="Rot Carrier",
                            job_production=[{"key": "food", "value": -1}])
        d2 = _make_disease(name="Mire Madness", job_type="Paranoid",
                            job_production=[{"key": "max_stability_gain_chance", "value": -0.05}])
        test_db["diseases"].insert_many([d1, d2])
        target = {"_id": "n1", "prosperity_role": "None", "_calc_cache": {
            "pop_count": 10,
            "disease_infection_counts": {str(d1["_id"]): 4, str(d2["_id"]): 4},
        }}
        jd, ja, _ = dh.collect_disease_effects(target)
        assert jd["disease_" + str(d1["_id"])]["production"] == {"food": -1.0}
        assert jd["disease_" + str(d2["_id"])]["production"] == {"max_stability_gain_chance": -0.05}
        assert ja["disease_" + str(d1["_id"])] == 4
        assert ja["disease_" + str(d2["_id"])] == 4

    def test_infect_random_pops_default_excludes_any_diseased_pop(self, patch_disease_mongo, test_db):
        """Default (infects_diseased_pops unset/false) behavior must be
        unchanged from before multi-disease support existed."""
        other_disease_id = ObjectId()
        _seed_pop(test_db, "n1", diseases=[other_disease_id])  # already sick with something else
        _seed_pop(test_db, "n1")  # healthy
        disease = _make_disease()  # infects_diseased_pops not set
        infected = dh.infect_random_pops("n1", disease, 10)
        assert infected == 1  # only the healthy pop was eligible

    def test_infects_diseased_pops_true_allows_coinfection(self, patch_disease_mongo, test_db):
        other_disease_id = ObjectId()
        _seed_pop(test_db, "n1", diseases=[other_disease_id])
        _seed_pop(test_db, "n1")
        disease = _make_disease(infects_diseased_pops=True)
        infected = dh.infect_random_pops("n1", disease, 10)
        assert infected == 2  # both pops eligible

    def test_infects_diseased_pops_true_still_excludes_pops_already_carrying_this_disease(
        self, patch_disease_mongo, test_db
    ):
        disease = _make_disease(infects_diseased_pops=True)
        _seed_pop(test_db, "n1", diseases=[disease["_id"]])  # already has THIS disease
        infected = dh.infect_random_pops("n1", disease, 10)
        assert infected == 0

    def test_cure_pop_removes_only_the_specified_disease(self, patch_disease_mongo, test_db):
        d1, d2 = ObjectId(), ObjectId()
        pop_id = _seed_pop(test_db, "n1", diseases=[d1, d2])
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.cure_pop(pop, d1)
        after = test_db["pops"].find_one({"_id": pop_id})
        assert after["diseases"] == [str(d2)]

    def test_race_restored_only_once_fully_healthy(self, patch_disease_mongo, test_db):
        """A pop cured of one of two co-infections (one race-changing) must
        NOT get its race restored until every disease is gone."""
        race_id = test_db["races"].insert_one({"name": "Human", "preferred_terrain": "Plains"}).inserted_id
        race_changer = _make_disease(changes_race=True, race_prefix="Rotting")
        other = _make_disease(name="Other")
        pop_id = test_db["pops"].insert_one(
            {"nation": "n1", "race": str(race_id), "culture": "c", "religion": "r"}
        ).inserted_id
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, race_changer)
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, other)

        # Cure the non-race disease first — race must stay changed.
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.cure_pop(pop, other["_id"])
        mid = test_db["pops"].find_one({"_id": pop_id})
        assert mid["diseases"] == [str(race_changer["_id"])]
        assert mid["pre_disease_race"] == str(race_id)
        derived = test_db["races"].find_one({"name": "Rotting Human"})
        assert mid["race"] == str(derived["_id"])

        # Now cure the race-changer too — fully healthy, race restored.
        dh.cure_pop(mid, race_changer["_id"])
        final = test_db["pops"].find_one({"_id": pop_id})
        assert not final.get("diseases")
        assert final.get("pre_disease_race") is None
        assert final["race"] == str(race_id)


# ---------------------------------------------------------------------------
# Region-scoped infectivity
# ---------------------------------------------------------------------------

class TestRegionScopedInfectivity:
    def test_no_restricted_region_is_unaffected(self, flask_app):
        disease = _make_disease(infectivity="Highly")
        settings = dh.get_infectivity_settings(disease, {"region": "somewhere"})
        assert settings["base_chance"] == 0.25

    def test_nation_inside_restricted_region_uses_base_infectivity(self, flask_app):
        region_id = str(ObjectId())
        disease = _make_disease(infectivity="Highly", restricted_region=region_id,
                                 outside_region_infectivity="Low")
        settings = dh.get_infectivity_settings(disease, {"region": region_id})
        assert settings["base_chance"] == 0.25  # Highly

    def test_nation_outside_restricted_region_uses_outside_infectivity(self, flask_app):
        region_id = str(ObjectId())
        disease = _make_disease(infectivity="Highly", restricted_region=region_id,
                                 outside_region_infectivity="Low")
        settings = dh.get_infectivity_settings(disease, {"region": str(ObjectId())})
        assert settings["base_chance"] == 0.10  # Low

    def test_no_nation_given_falls_back_to_base(self, flask_app):
        disease = _make_disease(infectivity="Highly", restricted_region=str(ObjectId()))
        settings = dh.get_infectivity_settings(disease)
        assert settings["base_chance"] == 0.25


# ---------------------------------------------------------------------------
# Prosperity-role (Ravager) job override
# ---------------------------------------------------------------------------

class TestProsperityRoleOverride:
    def test_non_matching_role_uses_base_production(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(
            prosperity_role_condition="Ravager",
            override_job_production=[{"key": "max_stability_gain_chance", "value": 0.05}],
        )
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "prosperity_role": "None", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 3}}}
        jd, _, _ = dh.collect_disease_effects(target)
        assert jd["disease_" + str(disease["_id"])]["production"] == {"max_stability_gain_chance": -0.05}

    def test_matching_role_uses_override_production(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(
            prosperity_role_condition="Ravager",
            override_job_production=[{"key": "max_stability_gain_chance", "value": 0.05}],
        )
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "prosperity_role": "Ravager", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 3}}}
        jd, _, _ = dh.collect_disease_effects(target)
        assert jd["disease_" + str(disease["_id"])]["production"] == {"max_stability_gain_chance": 0.05}

    def test_no_condition_set_is_unaffected_by_prosperity_role(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease()  # prosperity_role_condition not set
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "prosperity_role": "Ravager", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 3}}}
        jd, _, _ = dh.collect_disease_effects(target)
        assert jd["disease_" + str(disease["_id"])]["production"] == {"max_stability_gain_chance": -0.05}


class TestDiseaseJobDeathRoleOverride:
    def test_ravager_nation_uses_override_death_chance(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(
            job_death_chance=0.10, prosperity_role_condition="Ravager", override_job_death_chance=0.01,
        )
        test_db["diseases"].insert_one(disease)
        nation_id = test_db["nations"].insert_one(
            {"name": "Raiders", "prosperity_role": "Ravager"}
        ).inserted_id
        _seed_pop(test_db, nation_id, diseases=[disease["_id"]])

        # 0.05 fails against the override 0.01 but would succeed against base 0.10
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.random.random", return_value=0.05):
            th.disease_job_death_tick([], [], {}, pending=None)

        assert test_db["pops"].count_documents({"diseases": str(disease["_id"])}) == 1

    def test_non_ravager_nation_uses_base_death_chance(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(
            job_death_chance=0.10, prosperity_role_condition="Ravager", override_job_death_chance=0.01,
        )
        test_db["diseases"].insert_one(disease)
        nation_id = test_db["nations"].insert_one(
            {"name": "Farmers", "prosperity_role": "None"}
        ).inserted_id
        pop_id = _seed_pop(test_db, nation_id, diseases=[disease["_id"]])

        pending = []
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.random.random", return_value=0.05):
            th.disease_job_death_tick([], [], {}, pending=pending)

        assert len(pending) == 1
        assert pending[0]["item_id"] == pop_id

    def test_same_pop_not_queued_twice_across_two_death_diseases(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        d1 = _make_disease(name="Rot", job_death_chance=0.5)
        d2 = _make_disease(name="Madness", job_death_chance=0.5)
        test_db["diseases"].insert_many([d1, d2])
        pop_id = _seed_pop(test_db, "n1", diseases=[d1["_id"], d2["_id"]])

        pending = []
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.random.random", return_value=0.1):
            th.disease_job_death_tick([], [], {}, pending=pending)

        matching = [item for item in pending if item["item_id"] == pop_id]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# nation_disease_effects_tick: windfall + compliance loss
# ---------------------------------------------------------------------------

class TestNationDiseaseEffectsTick:
    def test_windfall_scales_with_infected_count(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(random_resource_windfall_per_infected_pop=2)
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        for _ in range(3):
            _seed_pop(test_db, nation_id, diseases=[disease["_id"]])
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 10, "resource_storage": {}}
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_disease_effects_tick(old_nation, new_nation, {})

        assert "windfall" in result
        total_granted = sum(new_nation["resource_storage"].values())
        assert total_granted == 6  # 2 rolls * 3 infected pops

    def test_no_windfall_rate_means_no_windfall(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease()  # rate unset/0
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pop(test_db, nation_id, diseases=[disease["_id"]])
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 10, "resource_storage": {}}
        new_nation = dict(old_nation)
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_disease_effects_tick(old_nation, new_nation, {})
        assert result == ""
        assert new_nation["resource_storage"] == {}

    def test_compliance_loss_at_max_infection_for_vassal(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        # Highly infectivity: max_infected_pct 0.65 → cap = floor(0.65*10) = 6
        disease = _make_disease(infectivity="Highly", compliance_loss_at_max_infection=True)
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        for _ in range(6):
            _seed_pop(test_db, nation_id, diseases=[disease["_id"]])
        old_nation = {
            "_id": nation_id, "name": "Vassalland", "pop_count": 10,
            "overlord": "SomeOverlord", "compliance": "Loyal", "resource_storage": {},
        }
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_disease_effects_tick(old_nation, new_nation, {"properties": {
                "compliance": {"enum": ["None", "Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]}
            }})

        assert "lost 1 level" in result
        assert new_nation["compliance"] == "Compliant"

    def test_no_compliance_loss_below_max_infection(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(infectivity="Highly", compliance_loss_at_max_infection=True)
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pop(test_db, nation_id, diseases=[disease["_id"]])  # only 1 of 10 — well under cap
        old_nation = {
            "_id": nation_id, "name": "Vassalland", "pop_count": 10,
            "overlord": "SomeOverlord", "compliance": "Loyal", "resource_storage": {},
        }
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            th.nation_disease_effects_tick(old_nation, new_nation, {"properties": {
                "compliance": {"enum": ["None", "Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]}
            }})

        assert new_nation["compliance"] == "Loyal"

    def test_no_compliance_loss_without_overlord(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(infectivity="Highly", compliance_loss_at_max_infection=True)
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        for _ in range(6):
            _seed_pop(test_db, nation_id, diseases=[disease["_id"]])
        old_nation = {
            "_id": nation_id, "name": "Freeland", "pop_count": 10,
            "compliance": "Loyal", "resource_storage": {},
        }  # no overlord
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_disease_effects_tick(old_nation, new_nation, {"properties": {
                "compliance": {"enum": ["None", "Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]}
            }})

        assert "compliance" not in result.lower()
        assert new_nation["compliance"] == "Loyal"

    def test_no_infections_is_a_noop(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        old_nation = {"_id": ObjectId(), "name": "Healthyland", "pop_count": 10}
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_disease_effects_tick(old_nation, dict(old_nation), {})
        assert result == ""
