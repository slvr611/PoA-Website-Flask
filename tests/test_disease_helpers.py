"""
Tests for helpers/disease_helpers.py and the disease tick functions.

Structure
─────────
Section 1 – Pure function tests (no DB)
    TestSettingsLookups
    TestActiveStageIndex
    TestKvListToDict

Section 2 – DB-backed tests (mongomock via patched disease_helpers.mongo)
    TestInfectionCounts
    TestDerivedRaces
    TestInfectCure
    TestCollectDiseaseEffects
    TestDiseaseCivilWar

Section 3 – Tick tests
    TestDiseaseSpreadTick
    TestDiseaseCureTick
"""
import random

import pytest
from unittest.mock import patch
from bson import ObjectId

import helpers.disease_helpers as dh


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_disease_mongo(mock_mongo):
    with patch("helpers.disease_helpers.mongo", mock_mongo):
        yield mock_mongo


def _make_disease(**overrides):
    disease = {
        "_id": ObjectId(),
        "name": "Crimson Rot",
        "rating": "Terrible",
        "job_type": "Rot Carrier",
        "job_production": [{"key": "stability_loss_chance", "value": 0.05}],
        "job_upkeep": [{"key": "food", "value": 1}],
        "infectivity": "Low",
        "difficulty": "Simple",
        "cure_progress": 0,
        "cured": False,
        "stages": [],
    }
    disease.update(overrides)
    return disease


def _seed_pops(db, nation_id, count, disease_id="", race="", slave=False):
    docs = []
    for _ in range(count):
        doc = {"nation": str(nation_id), "race": race, "culture": "c", "religion": "r"}
        if disease_id:
            doc["disease"] = str(disease_id)
        if slave:
            doc["slave"] = True
        docs.append(doc)
    db["pops"].insert_many(docs)


# ============================================================================
# Section 1 — Pure function tests
# ============================================================================

class TestSettingsLookups:
    """Settings come from the real diseases schema loaded by app_core."""

    def test_all_infectivity_values(self, flask_app):
        expected = {
            "Low":       (0.10, 0.02, 0.25),
            "Moderate":  (0.15, 0.04, 0.40),
            "Highly":    (0.25, 0.08, 0.65),
            "Extremely": (0.40, 0.10, 0.80),
        }
        for value, (base, per, cap) in expected.items():
            s = dh.get_infectivity_settings({"infectivity": value})
            assert s["base_chance"] == base
            assert s["chance_per_infected"] == per
            assert s["max_infected_pct"] == cap

    def test_all_difficulty_values(self, flask_app):
        expected = {
            "Simple":      (100, -5, 5),
            "Difficult":   (200, 0, 10),
            "Complicated": (350, 5, 15),
            "Impossible":  (600, 10, 20),
        }
        for value, (progress, dc, pops) in expected.items():
            s = dh.get_difficulty_settings({"difficulty": value})
            assert s["required_progress"] == progress
            assert s["dc_change"] == dc
            assert s["min_infected_pops"] == pops

    def test_unknown_value_falls_back_to_defaults(self, flask_app):
        assert dh.get_infectivity_settings({"infectivity": "Bogus"})["base_chance"] == 0.10
        assert dh.get_difficulty_settings({})["required_progress"] == 100


class TestActiveStageIndex:
    DISEASE = {"stages": [
        {"stage_name": "A", "threshold_pct": 30},
        {"stage_name": "B", "threshold_pct": 60},
    ]}

    def test_below_first_threshold(self):
        assert dh.active_stage_index(self.DISEASE, 2, 10) == -1

    def test_exactly_at_threshold(self):
        assert dh.active_stage_index(self.DISEASE, 3, 10) == 0

    def test_highest_reached_stage_wins(self):
        assert dh.active_stage_index(self.DISEASE, 6, 10) == 1
        assert dh.active_stage_index(self.DISEASE, 10, 10) == 1

    def test_no_stages(self):
        assert dh.active_stage_index({"stages": []}, 5, 10) == -1
        assert dh.active_stage_index({}, 5, 10) == -1

    def test_zero_pop_count(self):
        assert dh.active_stage_index(self.DISEASE, 5, 0) == -1

    def test_unsorted_stages(self):
        disease = {"stages": [
            {"stage_name": "High", "threshold_pct": 80},
            {"stage_name": "Low", "threshold_pct": 20},
        ]}
        assert dh.active_stage_index(disease, 5, 10) == 1   # only Low reached
        assert dh.active_stage_index(disease, 9, 10) == 0   # High wins


class TestKvListToDict:
    def test_basic(self):
        assert dh.kv_list_to_dict([{"key": "food", "value": 2}]) == {"food": 2.0}

    def test_blank_keys_and_bad_values_skipped(self):
        result = dh.kv_list_to_dict([
            {"key": "", "value": 9},
            {"key": "magic", "value": "not-a-number"},
            {"key": "wood", "value": 1.5},
        ])
        assert result == {"wood": 1.5}

    def test_none_input(self):
        assert dh.kv_list_to_dict(None) == {}


# ============================================================================
# Section 2 — DB-backed tests
# ============================================================================

class TestInfectionCounts:
    def test_nation_counts(self, patch_disease_mongo, test_db):
        d1, d2 = ObjectId(), ObjectId()
        _seed_pops(test_db, "n1", 3, disease_id=d1)
        _seed_pops(test_db, "n1", 2, disease_id=d2)
        _seed_pops(test_db, "n1", 4)             # healthy
        _seed_pops(test_db, "n2", 5, disease_id=d1)
        counts = dh.get_nation_infection_counts("n1")
        assert counts == {str(d1): 3, str(d2): 2}

    def test_global_counts(self, patch_disease_mongo, test_db):
        d1 = ObjectId()
        _seed_pops(test_db, "n1", 3, disease_id=d1)
        _seed_pops(test_db, "n2", 5, disease_id=d1)
        assert dh.get_global_infection_counts() == {str(d1): 8}

    def test_counts_from_pops(self):
        pops = [{"disease": "x"}, {"disease": "x"}, {"disease": ""}, {}]
        assert dh.infection_counts_from_pops(pops) == {"x": 2}


class TestDerivedRaces:
    def test_creates_once_and_is_idempotent(self, patch_disease_mongo, test_db):
        base = {"name": "Human", "preferred_terrain": "Plains"}
        rid1 = dh.get_or_create_derived_race(base, "Vampiric", "Undying", "Bloodthirsty")
        rid2 = dh.get_or_create_derived_race(base, "Vampiric", "Undying", "Bloodthirsty")
        assert rid1 and rid1 == rid2
        docs = list(test_db["races"].find({"name": "Vampiric Human"}))
        assert len(docs) == 1
        assert docs[0]["positive_trait"] == "Undying"
        assert docs[0]["negative_trait"] == "Bloodthirsty"
        assert docs[0]["preferred_terrain"] == "Plains"

    def test_skips_already_prefixed(self, patch_disease_mongo, test_db):
        assert dh.get_or_create_derived_race({"name": "Vampiric Human"}, "Vampiric") == ""

    def test_skips_missing_name_or_prefix(self, patch_disease_mongo, test_db):
        assert dh.get_or_create_derived_race({}, "Vampiric") == ""
        assert dh.get_or_create_derived_race({"name": "Human"}, "") == ""


class TestInfectCure:
    def test_race_change_round_trip(self, patch_disease_mongo, test_db):
        race_id = test_db["races"].insert_one(
            {"name": "Human", "preferred_terrain": "Plains"}
        ).inserted_id
        pop_id = test_db["pops"].insert_one(
            {"nation": "n1", "race": str(race_id), "culture": "c", "religion": "r"}
        ).inserted_id
        disease = _make_disease(changes_race=True, race_prefix="Rotting",
                                race_positive_trait="", race_negative_trait="Bloodthirsty")

        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, disease)

        infected = test_db["pops"].find_one({"_id": pop_id})
        assert infected["disease"] == str(disease["_id"])
        assert infected["pre_disease_race"] == str(race_id)
        derived = test_db["races"].find_one({"name": "Rotting Human"})
        assert infected["race"] == str(derived["_id"])

        dh.cure_pop(infected)
        cured = test_db["pops"].find_one({"_id": pop_id})
        assert cured.get("disease") is None
        assert cured.get("pre_disease_race") is None
        assert cured["race"] == str(race_id)

    def test_non_race_changing_disease(self, patch_disease_mongo, test_db):
        pop_id = test_db["pops"].insert_one(
            {"nation": "n1", "race": "r1", "culture": "c", "religion": "r"}
        ).inserted_id
        disease = _make_disease()
        pop = test_db["pops"].find_one({"_id": pop_id})
        dh.infect_pop(pop, disease)
        infected = test_db["pops"].find_one({"_id": pop_id})
        assert infected["disease"] == str(disease["_id"])
        assert infected["race"] == "r1"
        assert "pre_disease_race" not in infected

    def test_infect_random_pops_respects_limits(self, patch_disease_mongo, test_db):
        disease = _make_disease()
        _seed_pops(test_db, "n1", 3)
        _seed_pops(test_db, "n1", 2, disease_id=disease["_id"])   # already infected
        _seed_pops(test_db, "n1", 2, slave=True)                  # slaves skipped
        assert dh.infect_random_pops("n1", disease, 10) == 3
        assert test_db["pops"].count_documents({"nation": "n1", "disease": str(disease["_id"])}) == 5

    def test_cure_disease_pops_by_nation(self, patch_disease_mongo, test_db):
        disease = _make_disease()
        _seed_pops(test_db, "n1", 3, disease_id=disease["_id"])
        _seed_pops(test_db, "n2", 2, disease_id=disease["_id"])
        cured = dh.cure_disease_pops(str(disease["_id"]))
        assert cured == {"n1": 3, "n2": 2}
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 0


class TestCollectDiseaseEffects:
    def test_base_stage_effects(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease()
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 3}}}
        jd, ja, totals = dh.collect_disease_effects(target)
        key = "disease_" + str(disease["_id"])
        assert jd[key]["display_name"] == "Rot Carrier"
        assert jd[key]["production"] == {"stability_loss_chance": 0.05}
        assert jd[key]["upkeep"] == {"food": 1.0}
        assert jd[key]["disease"] is True
        assert ja[key] == 3
        assert totals == {}

    def test_stage_overrides_and_nation_modifiers(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(stages=[{
            "stage_name": "Majority", "threshold_pct": 50,
            "job_type_override": "Rot Lord",
            "job_production_override": [{"key": "magic", "value": 3}],
            "nation_modifiers": [{"key": "civil_war_chance", "value": 0.1}],
        }])
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 6}}}
        jd, ja, totals = dh.collect_disease_effects(target)
        key = "disease_" + str(disease["_id"])
        assert jd[key]["display_name"] == "Rot Lord"
        assert jd[key]["production"] == {"magic": 3.0}
        assert jd[key]["upkeep"] == {"food": 1.0}       # base kept (override empty)
        assert jd[key]["stage_name"] == "Majority"
        assert totals == {"civil_war_chance": 0.1}

    def test_cured_disease_contributes_nothing(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(cured=True)
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "_calc_cache": {
            "pop_count": 10, "disease_infection_counts": {str(disease["_id"]): 6}}}
        assert dh.collect_disease_effects(target) == ({}, {}, {})

    def test_forced_count_clamped_to_pop_count(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease()
        test_db["diseases"].insert_one(disease)
        target = {"_id": "n1", "_calc_cache": {
            "pop_count": 4, "disease_infection_counts": {str(disease["_id"]): 9}}}
        _, ja, _ = dh.collect_disease_effects(target)
        assert ja["disease_" + str(disease["_id"])] == 4


class TestDiseaseCivilWar:
    def _stub_change_pipeline(self, test_db):
        def fake_request(data_type, item_id, change_type, before_data, after_data, reason):
            test_db["nations"].insert_one(dict(after_data))
            return "change-1"

        return patch.multiple(
            "helpers.change_helpers",
            system_request_change=fake_request,
            system_approve_change=lambda change_id: True,
        )

    def test_split_moves_infected_pops_and_splits_resources(self, patch_disease_mongo, test_db, flask_app):
        race_id = test_db["races"].insert_one({"name": "Human", "preferred_terrain": "Plains"}).inserted_id
        disease = _make_disease(changes_race=True, race_prefix="Rotting", job_type="Rot Carrier")
        nation_id = ObjectId()
        source = {
            "_id": nation_id, "name": "Testland", "pop_count": 10, "money": 100,
            "resource_storage": {"food": 50}, "primary_race": str(race_id),
            "stability": "Stable", "districts": [{"def_key": "farm"}],
        }
        test_db["nations"].insert_one(source)
        _seed_pops(test_db, nation_id, 4, disease_id=disease["_id"])
        _seed_pops(test_db, nation_id, 6)

        with self._stub_change_pipeline(test_db):
            new_name, moved = dh.execute_disease_civil_war(source, disease, 4)

        assert new_name == "Rot Carrier Testland"
        assert moved == 4

        breakaway = test_db["nations"].find_one({"name": new_name})
        assert breakaway["money"] == 40                        # 4/10 share
        assert breakaway["resource_storage"] == {"food": 20}
        assert breakaway["stability"] == "Unsettled"
        assert "districts" not in breakaway                    # holdings stay behind
        derived = test_db["races"].find_one({"name": "Rotting Human"})
        assert breakaway["primary_race"] == str(derived["_id"])

        source_after = test_db["nations"].find_one({"_id": nation_id})
        assert source_after["money"] == 60
        assert source_after["resource_storage"]["food"] == 30

        # 4 pops moved; for a race-changing disease the breakaway is an
        # ACCEPTED nation — its pops keep derived races but are no longer sick.
        assert test_db["pops"].count_documents({"nation": str(breakaway["_id"])}) == 4
        assert test_db["pops"].count_documents(
            {"nation": str(breakaway["_id"]), "disease": str(disease["_id"])}) == 0
        assert test_db["pops"].count_documents(
            {"nation": str(nation_id), "disease": str(disease["_id"])}) == 0

    def test_split_keeps_infection_for_non_race_changing_disease(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(job_type="Plague Bearer")   # changes_race False
        source = {"_id": ObjectId(), "name": "Testland", "pop_count": 10,
                  "money": 0, "resource_storage": {}}
        test_db["nations"].insert_one(dict(source))
        _seed_pops(test_db, source["_id"], 4, disease_id=disease["_id"])
        _seed_pops(test_db, source["_id"], 6)

        with self._stub_change_pipeline(test_db):
            new_name, moved = dh.execute_disease_civil_war(source, disease, 4)

        assert moved == 4
        breakaway = test_db["nations"].find_one({"name": new_name})
        # Non-race-changing: the breakaway stays a diseased nation
        assert test_db["pops"].count_documents(
            {"nation": str(breakaway["_id"]), "disease": str(disease["_id"])}) == 4

    def test_name_collision_gets_suffix(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease(job_type="Rot Carrier")
        test_db["nations"].insert_one({"name": "Rot Carrier Testland"})
        source = {"_id": ObjectId(), "name": "Testland", "pop_count": 5, "money": 0,
                  "resource_storage": {}}
        test_db["nations"].insert_one(source)

        with self._stub_change_pipeline(test_db):
            new_name, _ = dh.execute_disease_civil_war(source, disease, 2)
        assert new_name == "Rot Carrier Testland 2"


# ============================================================================
# Section 3 — Tick tests
# ============================================================================

class TestDiseaseSpreadTick:
    def test_spread_infects_one_pop_on_success(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(infectivity="Low")   # 10% + 2%/pop
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pops(test_db, nation_id, 2, disease_id=disease["_id"])
        _seed_pops(test_db, nation_id, 18)
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 20}
        new_nation = dict(old_nation)

        # chance = 0.10 + 0.02*2 = 0.14; force success
        with patch("helpers.tick_helpers.random.random", return_value=0.05):
            result = th.nation_disease_spread_tick(old_nation, new_nation, {})

        assert "has spread" in result
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 3
        roll_info = new_nation["disease_spread_rolls"]["Crimson Rot"]
        assert abs(roll_info["chance_at_tick"] - 0.14) < 1e-9

    def test_spread_capped_at_max_infected_pct(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(infectivity="Low")   # cap 25% → 5 of 20
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pops(test_db, nation_id, 5, disease_id=disease["_id"])
        _seed_pops(test_db, nation_id, 15)
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 20}
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.random.random", return_value=0.0):
            result = th.nation_disease_spread_tick(old_nation, new_nation, {})

        assert "has spread" not in result
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 5

    def test_no_spread_roll_when_stage_halts(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(stages=[{
            "stage_name": "Endemic", "threshold_pct": 10, "halts_spread": True,
        }])
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pops(test_db, nation_id, 4, disease_id=disease["_id"])
        _seed_pops(test_db, nation_id, 16)
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 20}
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.random.random", return_value=0.0):
            th.nation_disease_spread_tick(old_nation, new_nation, {})

        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 4
        assert new_nation["disease_spread_rolls"] == {}

    def test_stage_civil_war_fires_once(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(infectivity="Extremely", stages=[{
            "stage_name": "Majority", "threshold_pct": 50, "trigger_civil_war": True,
        }])
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pops(test_db, nation_id, 6, disease_id=disease["_id"])
        _seed_pops(test_db, nation_id, 4)
        old_nation = {"_id": nation_id, "name": "Testland", "pop_count": 10,
                      "money": 0, "resource_storage": {}}
        test_db["nations"].insert_one(dict(old_nation))
        new_nation = dict(old_nation)

        def fake_request(data_type, item_id, change_type, before_data, after_data, reason):
            test_db["nations"].insert_one(dict(after_data))
            return "change-1"

        with patch.multiple("helpers.change_helpers",
                            system_request_change=fake_request,
                            system_approve_change=lambda cid: True), \
             patch("helpers.tick_helpers.random.random", return_value=0.99):
            result = th.nation_disease_spread_tick(old_nation, new_nation, {})

        assert "CIVIL WAR" in result
        assert new_nation["stability"] == "Unsettled"
        breakaway = test_db["nations"].find_one({"name": "Rot Carrier Testland"})
        assert breakaway is not None
        # All infected pops moved out → stage bookkeeping reset for re-outbreak
        assert str(disease["_id"]) not in new_nation["disease_stages"]

        # Second run: no infected pops left in the source → no re-trigger
        old2 = dict(new_nation)
        new2 = dict(old2)
        with patch("helpers.tick_helpers.random.random", return_value=0.99):
            result2 = th.nation_disease_spread_tick(old2, new2, {})
        assert "CIVIL WAR" not in result2

    def test_fully_infected_nation_does_not_split(self, patch_disease_mongo, test_db, flask_app):
        """A 100%-infected nation entering a civil-war stage must NOT split —
        there are no healthy pops to leave behind. It 'fully succumbs' instead
        (this is the breakaway-nation case: without the guard it would fission
        forever)."""
        import helpers.tick_helpers as th
        disease = _make_disease(stages=[{
            "stage_name": "Majority", "threshold_pct": 50, "trigger_civil_war": True,
        }])
        test_db["diseases"].insert_one(disease)
        nation_id = ObjectId()
        _seed_pops(test_db, nation_id, 10, disease_id=disease["_id"])
        old_nation = {"_id": nation_id, "name": "Vamp Nation", "pop_count": 10,
                      "money": 50, "resource_storage": {}}
        test_db["nations"].insert_one(dict(old_nation))
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.random.random", return_value=0.99):
            result = th.nation_disease_spread_tick(old_nation, new_nation, {})

        assert "CIVIL WAR" not in result
        assert "fully succumbed" in result
        assert test_db["nations"].count_documents({}) == 1        # no breakaway
        # Stage stamped so it doesn't re-fire every tick
        assert new_nation["disease_stages"][str(disease["_id"])] == 0

        old2 = dict(new_nation)
        new2 = dict(old2)
        with patch("helpers.tick_helpers.random.random", return_value=0.99):
            result2 = th.nation_disease_spread_tick(old2, new2, {})
        assert "fully succumbed" not in result2

    def test_execute_civil_war_guard_returns_none_at_full_infection(self, patch_disease_mongo, test_db, flask_app):
        disease = _make_disease()
        source = {"_id": ObjectId(), "name": "Testland", "pop_count": 5,
                  "money": 10, "resource_storage": {}}
        assert dh.execute_disease_civil_war(source, disease, 5) == (None, 0)
        assert dh.execute_disease_civil_war(source, disease, 7) == (None, 0)


# ============================================================================
# Section 4 — Accepted-nation mechanics
# ============================================================================

def _make_accepted_nation(test_db, race_prefix="Vampiric", base_name="Human", jobs=None):
    derived_id = test_db["races"].insert_one(
        {"name": f"{race_prefix} {base_name}", "positive_trait": "Undying",
         "negative_trait": "Bloodthirsty", "preferred_terrain": "Plains"}
    ).inserted_id
    nation_id = test_db["nations"].insert_one({
        "name": "Nocturne", "primary_race": str(derived_id),
        "pop_count": 10, "jobs": jobs or {},
    }).inserted_id
    return test_db["nations"].find_one({"_id": nation_id}), str(derived_id)


class TestAcceptance:
    def test_nation_accepts_disease(self, patch_disease_mongo, test_db):
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        nation, _ = _make_accepted_nation(test_db)
        assert dh.nation_accepts_disease(nation, disease) is True

    def test_non_derived_primary_race_does_not_accept(self, patch_disease_mongo, test_db):
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        race_id = test_db["races"].insert_one({"name": "Human"}).inserted_id
        nation = {"primary_race": str(race_id)}
        assert dh.nation_accepts_disease(nation, disease) is False

    def test_non_race_changing_disease_never_accepted(self, patch_disease_mongo, test_db):
        disease = _make_disease()   # changes_race False
        nation, _ = _make_accepted_nation(test_db)
        assert dh.nation_accepts_disease(nation, disease) is False

    def test_accepts_by_name_uses_db_lookup(self, patch_disease_mongo, test_db):
        disease = _make_disease(name="Vampirism", changes_race=True, race_prefix="Vampiric")
        test_db["diseases"].insert_one(disease)
        nation, _ = _make_accepted_nation(test_db)
        assert dh.nation_accepts_disease_by_name(nation, "Vampirism") is True
        assert dh.nation_accepts_disease_by_name(nation, "Missing Disease") is False

    def test_uses_calc_cache_primary_race_name(self, patch_disease_mongo, test_db):
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        nation = {"_calc_cache": {"primary_race_name": "Vampiric Elf"}}
        assert dh.nation_accepts_disease(nation, disease) is True

    def test_convert_pop_to_accepted_leaves_no_disease_state(self, patch_disease_mongo, test_db):
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        race_id = test_db["races"].insert_one(
            {"name": "Human", "preferred_terrain": "Plains"}).inserted_id
        pop_id = test_db["pops"].insert_one(
            {"nation": "n1", "race": str(race_id), "culture": "c", "religion": "r"}
        ).inserted_id
        pop = test_db["pops"].find_one({"_id": pop_id})
        assert dh.convert_pop_to_accepted(pop, disease) is True
        converted = test_db["pops"].find_one({"_id": pop_id})
        derived = test_db["races"].find_one({"name": "Vampiric Human"})
        assert converted["race"] == str(derived["_id"])
        assert converted.get("disease") is None
        assert converted.get("pre_disease_race") is None

    def test_infect_random_pops_skips_derived_race_pops(self, patch_disease_mongo, test_db):
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        derived_id = test_db["races"].insert_one({"name": "Vampiric Human"}).inserted_id
        _seed_pops(test_db, "n1", 3, race=str(derived_id))   # existing vampires
        assert dh.infect_random_pops("n1", disease, 5) == 0  # nothing infectable

    def test_outbreak_tick_skips_accepted_nation(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        test_db["diseases"].insert_one(disease)
        nation, derived_id = _make_accepted_nation(test_db)
        # Somehow-infected pops inside an accepted nation must not spread/outbreak
        _seed_pops(test_db, nation["_id"], 2, disease_id=disease["_id"])
        _seed_pops(test_db, nation["_id"], 8)
        new_nation = dict(nation)
        with patch("helpers.tick_helpers.random.random", return_value=0.0):
            result = th.nation_disease_spread_tick(nation, new_nation, {})
        assert "has spread" not in result
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 2


class TestAcceptedSpreadTick:
    def _setup(self, test_db, jobs=None, chance=0.1):
        disease = _make_disease(
            name="Vampirism", changes_race=True, race_prefix="Vampiric",
            race_positive_trait="Undying", race_negative_trait="Bloodthirsty",
            accepted_spread_jobs=["full_vampire"], accepted_spread_chance=chance,
        )
        test_db["diseases"].insert_one(disease)
        nation, derived_id = _make_accepted_nation(
            test_db, jobs=jobs if jobs is not None else {"full_vampire": 5})
        return disease, nation, derived_id

    def test_internal_conversion(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease, nation, derived_id = self._setup(test_db)
        human_id = test_db["races"].insert_one(
            {"name": "Human", "preferred_terrain": "Plains"}).inserted_id
        _seed_pops(test_db, nation["_id"], 4, race=str(human_id))
        new_nation = dict(nation)

        # roll 0.2 <= chance 0.5 (5 spreaders x 0.1); second roll 0.2 < 0.5 → internal
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.random.random", side_effect=[0.2, 0.2]):
            result = th.nation_accepted_spread_tick(nation, new_nation, {})

        assert "embraced" in result
        converted = test_db["pops"].count_documents({"race": str(derived_id)})
        assert converted == 1
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 0
        roll = new_nation["disease_spread_rolls"]["Vampirism (accepted)"]
        assert abs(roll["chance_at_tick"] - 0.5) < 1e-9

    def test_external_infection(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease, nation, _ = self._setup(test_db)
        target_race = test_db["races"].insert_one({"name": "Elf"}).inserted_id
        target_id = test_db["nations"].insert_one(
            {"name": "Neighborland", "primary_race": str(target_race), "pop_count": 5}
        ).inserted_id
        _seed_pops(test_db, target_id, 5, race=str(target_race))

        new_nation = dict(nation)
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.random.random", side_effect=[0.2, 0.9]), \
             patch("helpers.hex_map_helpers.get_nations_within_distance",
                   return_value=["Neighborland"]):
            result = th.nation_accepted_spread_tick(nation, new_nation, {})

        assert "has spread from Nocturne to Neighborland" in result
        assert test_db["pops"].count_documents(
            {"nation": str(target_id), "disease": str(disease["_id"])}) == 1

    def test_no_roll_without_spreader_jobs(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease, nation, _ = self._setup(test_db, jobs={})
        new_nation = dict(nation)
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_accepted_spread_tick(nation, new_nation, {})
        assert result == ""
        assert "disease_spread_rolls" not in new_nation

    def test_non_accepted_nation_does_not_spread(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(
            name="Vampirism", changes_race=True, race_prefix="Vampiric",
            accepted_spread_jobs=["full_vampire"], accepted_spread_chance=0.1)
        test_db["diseases"].insert_one(disease)
        race_id = test_db["races"].insert_one({"name": "Human"}).inserted_id
        nation = {"_id": ObjectId(), "name": "Plainland",
                  "primary_race": str(race_id), "jobs": {"full_vampire": 3}}
        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.nation_accepted_spread_tick(nation, dict(nation), {})
        assert result == ""

    def test_external_target_weighting(self, patch_disease_mongo, test_db, flask_app):
        # Trade-connected nations get weight 2 vs 1
        disease = _make_disease(changes_race=True, race_prefix="Vampiric")
        nation, _ = _make_accepted_nation(test_db)
        for name in ("TradePartner", "Stranger"):
            rid = test_db["races"].insert_one({"name": name + "Race"}).inserted_id
            test_db["nations"].insert_one({"name": name, "primary_race": str(rid)})
        test_db["trade_routes"].insert_one(
            {"nation_a": "Nocturne", "nation_b": "TradePartner", "status": "active"})

        picks = {"TradePartner": 0, "Stranger": 0}
        with patch("helpers.hex_map_helpers.get_nations_within_distance",
                   return_value=["TradePartner", "Stranger"]), \
             patch("helpers.trade_route_helpers.get_connectable_nations", return_value=[]):
            import random as _random
            _random.seed(42)
            for _ in range(300):
                target = dh.pick_external_spread_target(nation, disease)
                picks[target["name"]] += 1

        # ~2:1 ratio expected; allow slack
        assert picks["TradePartner"] > picks["Stranger"] * 1.4


class TestDiseaseCureTick:
    def _nations(self, disease_id, contributions):
        nations = []
        for i, per_tick in enumerate(contributions):
            nations.append({
                "_id": ObjectId(), "name": f"Nation{i}",
                "shared_quests": [{
                    "disease": str(disease_id), "slot": "1_progress_slot",
                    "total_progress_per_tick": per_tick,
                }],
            })
        return nations

    def test_gated_below_min_infected(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(difficulty="Simple")   # min 5 pops
        test_db["diseases"].insert_one(disease)
        _seed_pops(test_db, "n1", 3, disease_id=disease["_id"])
        nations = self._nations(disease["_id"], [2, 3])

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo):
            result = th.disease_cure_cross_tick(nations, [dict(n) for n in nations], {})

        assert "gated" in result
        assert test_db["diseases"].find_one({"_id": disease["_id"]})["cure_progress"] == 0

    def test_contributions_accumulate(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(difficulty="Simple", cure_progress=10)
        test_db["diseases"].insert_one(disease)
        _seed_pops(test_db, "n1", 6, disease_id=disease["_id"])   # above min 5
        nations = self._nations(disease["_id"], [2, 3])

        def fake_request(data_type, item_id, change_type, before_data, after_data, reason):
            test_db["diseases"].update_one({"_id": item_id}, {"$set": {
                "cure_progress": after_data["cure_progress"],
                "cured": after_data.get("cured", False)}})
            return "change-1"

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.system_request_change", fake_request), \
             patch("helpers.tick_helpers.system_approve_change", lambda cid: True):
            result = th.disease_cure_cross_tick(nations, [dict(n) for n in nations], {})

        assert "+5" in result
        doc = test_db["diseases"].find_one({"_id": disease["_id"]})
        assert doc["cure_progress"] == 15
        assert not doc.get("cured")

    def test_completion_cures_all_pops(self, patch_disease_mongo, test_db, flask_app):
        import helpers.tick_helpers as th
        disease = _make_disease(difficulty="Simple", cure_progress=98)
        test_db["diseases"].insert_one(disease)
        _seed_pops(test_db, "n1", 6, disease_id=disease["_id"])
        nations = self._nations(disease["_id"], [5])
        new_nations = [dict(n) for n in nations]
        new_nations[0]["disease_stages"] = {str(disease["_id"]): 1}

        def fake_request(data_type, item_id, change_type, before_data, after_data, reason):
            test_db["diseases"].update_one({"_id": item_id}, {"$set": {
                "cure_progress": after_data["cure_progress"],
                "cured": after_data.get("cured", False)}})
            return "change-1"

        with patch("helpers.tick_helpers.mongo", patch_disease_mongo), \
             patch("helpers.tick_helpers.system_request_change", fake_request), \
             patch("helpers.tick_helpers.system_approve_change", lambda cid: True):
            result = th.disease_cure_cross_tick(nations, new_nations, {})

        assert "HAS BEEN CURED" in result
        doc = test_db["diseases"].find_one({"_id": disease["_id"]})
        assert doc["cured"] is True
        assert doc["cure_progress"] == 100                       # clamped
        assert test_db["pops"].count_documents({"disease": str(disease["_id"])}) == 0
        assert str(disease["_id"]) not in new_nations[0]["disease_stages"]
