"""
Regression test for a real bug: _build_nation_calc_cache (calculations/
field_calculations.py) fetched each nation's pops with a projection of
{"race": 1, "culture": 1, "religion": 1, "slave": 1, "disease": 1} — the
OLD singular field name from before pop.disease was migrated to pop.diseases
(an array, to support multi-disease pops). Since "disease" no longer exists
on any pop document, infection_counts_from_pops(pops) always returned {}
for the cached path.

collect_disease_effects prioritizes this cache value over a live DB query
(`counts = cache.get("disease_infection_counts"); if counts is None: ...`)
— and an empty dict is not None, so the fallback never triggered. The net
effect: every nation, no matter how many pops it actually had infected,
silently got ZERO disease-forced jobs injected into job_details whenever
calculate_all_fields ran through its normal nation path (which always
builds this cache first) — while anything computed by a direct DB query
(get_nation_infection_counts, used elsewhere) still saw the real counts,
which is why disease effects reached through other paths (e.g. resource
totals fed by other mechanisms) kept working while the Jobs section, which
reads job_details, went silently empty of disease entries.

Every existing collect_disease_effects test manually constructs
target["_calc_cache"]["disease_infection_counts"] directly, bypassing
_build_nation_calc_cache entirely — none of them would have caught this.
This test exercises the real cache-building path via calculate_all_fields.
"""
from unittest.mock import patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

NATIONS_SCHEMA = category_data["nations"]["schema"]


class TestNationCalcCacheIncludesDiseaseInfections:
    def test_infected_pops_produce_a_disease_job_via_full_calculate_all_fields(self):
        client = mongomock.MongoClient()
        db = client["test"]

        nation_id = ObjectId()
        disease_id = ObjectId()

        db["diseases"].insert_one({
            "_id": disease_id,
            "name": "Crimson Rot",
            "job_type": "Rot Carrier",
            "job_production": [{"key": "stability_loss_chance", "value": 0.05}],
            "job_upkeep": [],
            "infectivity": "Low",
            "difficulty": "Simple",
            "changes_race": False,
        })
        db["nations"].insert_one({"_id": nation_id, "name": "Testland", "pop_count": 3})
        for _ in range(3):
            db["pops"].insert_one({
                "nation": str(nation_id), "race": "r1", "culture": "c1", "religion": "rel1",
                "diseases": [str(disease_id)],
            })

        fake_mongo = type("FakeMongo", (), {"db": db})()

        with patch.object(fc, "mongo", fake_mongo), \
             patch("helpers.disease_helpers.mongo", fake_mongo), \
             patch("calculations.field_calculations.category_data", {
                 **category_data,
                 "pops": {**category_data["pops"], "database": db["pops"]},
                 "races": {**category_data["races"], "database": db["races"]},
             }):
            nation_doc = db["nations"].find_one({"_id": nation_id})
            calculated = fc.calculate_all_fields(nation_doc, NATIONS_SCHEMA, "nation")

        job_details = calculated.get("job_details", {})
        disease_jobs = {k: v for k, v in job_details.items() if isinstance(v, dict) and v.get("disease")}
        assert disease_jobs, f"expected a disease-forced job in job_details, got: {job_details}"
        key = f"disease_{disease_id}"
        assert disease_jobs[key]["display_name"] == "Rot Carrier"
        assert disease_jobs[key]["forced_count"] == 3
