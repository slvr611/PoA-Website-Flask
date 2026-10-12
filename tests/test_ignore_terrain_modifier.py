"""
Regression test for a new modifier type: "ignore_terrain", the terrain-tile
equivalent of "ignore_nodes" (see test_ignore_nodes_modifier.py). Originally
a single "ignore_nodes" switch was made to zero both resource-node AND
terrain-tile production, but that conflated two independently-toggleable game
concepts, so they were split into separate modifiers:

- ignore_nodes: zeroes resource node production only.
- ignore_terrain: zeroes terrain-tile production only.

ignore_terrain must zero the WHOLE terrain contribution (base per-tile yield
plus any swap_resource/multiplier/extra_resource/hazardous-terrain-magic
modifiers to it), not just the base conversion while a modifier-driven bonus
still leaks through.
"""
from app_core import json_data, category_data
from calculations.field_calculations import calculate_all_fields


def _nation(modifiers):
    return {"name": "Test Nation", "modifiers": modifiers, "districts": [], "jobs": {}}


class TestIgnoreTerrainModifierTypeIsRegistered:
    def test_applicable_to_nation(self):
        assert "nation" in json_data["modifier_types"]["ignore_terrain"]["applicable_to"]

    def test_field_template_matches_what_calculate_all_fields_reads(self):
        assert json_data["modifier_types"]["ignore_terrain"]["field_template"] == "ignore_terrain"


class TestIgnoreTerrainZeroesTerrainProductionEndToEnd:
    def test_without_the_modifier_terrain_production_counts_normally(self):
        nation = _nation([])
        nation["territory_types"] = {"plains": 8}  # 8 // 4 required = 2 base food
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 2

    def test_with_the_modifier_base_terrain_production_is_zeroed(self):
        nation = _nation([
            {"modifier_type": "ignore_terrain", "value": 1, "scope": "nation_self"},
        ])
        nation["territory_types"] = {"plains": 8}
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 0

    def test_terrain_multiplier_and_extra_resource_modifiers_also_leak_through_without_the_switch(self):
        """Control: confirms the multiplier/extra_resource terrain-rule
        modifiers actually apply on top of the base yield absent ignore_terrain
        — so the next test's zeroing is meaningful, not a no-op."""
        nation = _nation([
            {"modifier_type": "terrain_production_multiplier", "terrain": "plains",
             "resource": "food", "value": 3, "scope": "nation_self"},
            {"modifier_type": "terrain_extra_resource", "terrain": "plains",
             "resource": "stone", "value": 2, "scope": "nation_self"},
        ])
        nation["territory_types"] = {"plains": 8}
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 6   # 2 base * 3 multiplier
        assert calc["resource_production"]["stone"] == 4  # 8 tiles // 2 required

    def test_terrain_multiplier_and_extra_resource_modifiers_are_fully_zeroed_with_the_switch(self):
        """The actual fix: ignore_terrain must discard the ENTIRE terrain
        contribution (base + multiplier + extra_resource), not just the base
        per-tile conversion."""
        nation = _nation([
            {"modifier_type": "terrain_production_multiplier", "terrain": "plains",
             "resource": "food", "value": 3, "scope": "nation_self"},
            {"modifier_type": "terrain_extra_resource", "terrain": "plains",
             "resource": "stone", "value": 2, "scope": "nation_self"},
            {"modifier_type": "ignore_terrain", "value": 1, "scope": "nation_self"},
        ])
        nation["territory_types"] = {"plains": 8}
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 0
        assert calc["resource_production"]["stone"] == 0

    def test_node_production_is_unaffected(self):
        """ignore_terrain must not touch resource-node production — that's
        ignore_nodes's job (see test_ignore_nodes_modifier.py)."""
        nation = _nation([
            {"modifier_type": "resource_nodes", "resource": "food", "value": 3, "scope": "nation_self"},
            {"modifier_type": "ignore_terrain", "value": 1, "scope": "nation_self"},
        ])
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 6  # 3 nodes * (2 + 0 node_value)


class TestIgnoreTerrainTooltipBreakdownStaysInSync:
    """Mirrors test_magic_node_ignore_breakdown.py's approach for the Node
    production section: the resource_production tooltip must never show a
    Terrain contribution line for a resource whose real total excluded it."""

    def test_terrain_line_is_hidden_when_ignore_terrain_is_set(self):
        import calculations.field_calculations as fc

        target = {"modifiers": [], "districts": [], "territory_types": {"plains": 8}}
        overall_totals = {"ignore_terrain": 1}
        contribs = fc._build_computed_contributions(target, {}, overall_totals, {}, None, {})
        assert not any(c.source_type == "terrain" for c in contribs)
        assert not any(c.label == "Flaming Ravager (Frigid Caps)" for c in contribs)

    def test_terrain_line_still_shows_without_ignore_terrain(self):
        import calculations.field_calculations as fc

        target = {"modifiers": [], "districts": [], "territory_types": {"plains": 8}}
        contribs = fc._build_computed_contributions(target, {}, {}, {}, None, {})
        assert any(c.source_type == "terrain" and "food_production" in c.modifiers for c in contribs)
