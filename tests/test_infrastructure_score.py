"""
Regression test for calculate_infrastructure_score.py's score_modifiers.

Same bug class as district_duration_tick's production crash: a structured
(modifier_type-based) modifier legitimately has field: None rather than a
plain field name, and `modifier.get("field", "")` only substitutes its
default when the key is *missing*, not when it's present with value None —
so `"nodes" in modifier.get("field", "")` raised
TypeError: argument of type 'NoneType' is not iterable for any real nation
carrying that data shape (confirmed against real nation data, e.g. Vandador).
"""
from calculate_infrastructure_score import score_modifiers


class TestScoreModifiersFieldNone:
    def test_structured_modifier_with_field_none_does_not_crash(self):
        nation = {"modifiers": [
            {"field": None, "modifier_type": "stability_gain_chance", "value": 0.35},
        ]}
        assert score_modifiers(nation) == 0

    def test_modifier_missing_field_key_entirely_does_not_crash(self):
        nation = {"modifiers": [
            {"modifier_type": "import_slots", "value": -2.0},
        ]}
        assert score_modifiers(nation) == 0

    def test_nodes_field_still_scores_normally(self):
        nation = {"modifiers": [
            {"field": "magic_nodes", "value": 3},
            {"field": None, "modifier_type": "stability_gain_chance", "value": 0.1},
            {"field": "resource_nodes", "value": 2},
        ]}
        assert score_modifiers(nation) == 5
