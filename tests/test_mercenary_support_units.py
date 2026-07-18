"""
Mercenary support unit recruitment: support units are not a distinct
recruit category for mercenaries — they're unit_type "Land"/"Naval" units
with a support:True sub-flag, and are listed directly in the same
land_units/naval_units dropdown (see forms.py populate_select_field) and
counted via the ordinary land_budget_spent/naval_budget_spent computation.
There is no separate support_units field, dropdown, or budget.
"""
import json

from calculations.compute_functions import compute_budget_spent, CUSTOM_COMPUTE_FUNCTIONS


class TestNoSeparateSupportUnitsField:
    def test_mercenary_schema_has_no_support_units_field(self):
        with open("json-data/schemas/mercenaries.json") as f:
            schema = json.load(f)
        properties = schema["$jsonSchema"]["properties"]
        assert "support_units" not in properties
        assert "land_units" in properties
        assert "naval_units" in properties

    def test_no_support_budget_fields_registered(self):
        assert "support_budget" not in CUSTOM_COMPUTE_FUNCTIONS
        assert "support_budget_spent" not in CUSTOM_COMPUTE_FUNCTIONS


class TestBudgetSpentUnaffectedBySupportUnits:
    """compute_budget_spent needs no special handling — a support unit is just
    a name in land_units/naval_units like any other recruited unit."""

    def test_land_units_containing_a_support_unit_name_counts_normally(self):
        from unittest.mock import MagicMock, patch
        units_list = [
            {"name": "Heavy Infantry", "era": "Classical", "has_recruitment_cost": True, "recruitment_cost": 200},
            {"name": "Siege Weapon", "era": "Classical", "has_recruitment_cost": True, "recruitment_cost": 150},
        ]
        mock_db = MagicMock()
        mock_db.find.return_value = units_list
        target = {"land_units": ["Heavy Infantry", "Siege Weapon"]}
        with patch("calculations.compute_functions.category_data", {"units": {"database": mock_db}}):
            result = compute_budget_spent("land_budget_spent", target, 0, {}, {})
        assert result == 350

    def test_empty_land_units_is_zero(self):
        target = {"land_units": []}
        result = compute_budget_spent("land_budget_spent", target, 0, {}, {})
        assert result == 0
