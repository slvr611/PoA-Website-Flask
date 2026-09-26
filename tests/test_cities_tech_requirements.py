"""
Regression test for a data typo: json-data/cities.json's "agropolis" entry
required tech key "agricultural_estates" (plural), but the real tech in
json-data/tech.json is keyed "agricultural_estate" (singular) — so no
nation could ever satisfy the requirement, regardless of having actually
researched Agricultural Estate. helpers/ai_decision_helpers.py's
evaluate_goal_district (the AI's city-type eligibility filter) checks
membership in {researched tech keys} directly against this string, with no
fuzzy matching — a single mismatched character silently removed Agropolis
from consideration forever.

Generalizes to every city type, not just Agropolis, so a similar future
typo on any other city's tech requirement fails immediately here instead
of only manifesting as a mysteriously-unavailable city option.
"""
from app_core import json_data


class TestEveryCityTechRequirementIsARealTechKey:
    def test_agropolis_requires_the_real_agricultural_estate_key(self):
        assert json_data["cities"]["agropolis"]["requirements"]["tech"] == "agricultural_estate"

    def test_no_city_tech_requirement_references_a_missing_tech_key(self):
        tech_keys = set(json_data["tech"].keys())
        broken = []
        for city_key, city_data in json_data["cities"].items():
            req_tech = city_data.get("requirements", {}).get("tech")
            if req_tech and req_tech not in tech_keys:
                broken.append((city_key, req_tech))
        assert broken == [], f"City tech requirements referencing a nonexistent tech key: {broken}"
