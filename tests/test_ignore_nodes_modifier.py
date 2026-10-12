"""
Regression test for a feature gap: compute_resource_production already read
an "ignore_nodes" flag (a global kill-switch zeroing resource-node production
for every general/unique resource) and the resource_production breakdown
tooltip already respected it too (see test_magic_node_ignore_breakdown.py) —
but no modifier_types.json entry ever exposed "ignore_nodes" as something an
admin could actually attach to a nation via the normal modifier system. Only
the per-resource "ignore_{resource}_nodes" law-driven variant (e.g. the magic
stance "Creation"/"Manifest" laws) was reachable in practice.

Added the missing "ignore_nodes" modifier_type entry so it can be applied like
any other nation modifier. This test exercises it end-to-end through the real
modifier pipeline (calculate_all_fields), not just a hand-fed overall_totals
dict.

ignore_nodes only ever covers resource NODE production — terrain-tile
production has its own separate switch, "ignore_terrain" (see
test_ignore_terrain_modifier.py), so the two can be toggled independently.
"""
from app_core import json_data, category_data
from calculations.field_calculations import calculate_all_fields


def _nation(modifiers):
    return {"name": "Test Nation", "modifiers": modifiers, "districts": [], "jobs": {}}


class TestIgnoreNodesModifierTypeIsRegistered:
    def test_applicable_to_nation(self):
        assert "nation" in json_data["modifier_types"]["ignore_nodes"]["applicable_to"]

    def test_field_template_matches_what_compute_resource_production_reads(self):
        assert json_data["modifier_types"]["ignore_nodes"]["field_template"] == "ignore_nodes"


class TestIgnoreNodesZeroesTerritoryProductionEndToEnd:
    def test_without_the_modifier_node_production_counts_normally(self):
        nation = _nation([
            {"modifier_type": "resource_nodes", "resource": "food", "value": 3, "scope": "nation_self"},
        ])
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 6  # 3 nodes * (2 + 0 node_value)

    def test_with_the_modifier_node_production_is_fully_zeroed(self):
        nation = _nation([
            {"modifier_type": "resource_nodes", "resource": "food", "value": 3, "scope": "nation_self"},
            {"modifier_type": "ignore_nodes", "value": 1, "scope": "nation_self"},
        ])
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 0

    def test_research_nodes_are_also_zeroed_unlike_ignore_non_research_nodes(self):
        """ignore_nodes is the global ALL-resources switch — unlike
        ignore_non_research_nodes, research is not special-cased."""
        nation = _nation([
            {"modifier_type": "resource_nodes", "resource": "research", "value": 2, "scope": "nation_self"},
            {"modifier_type": "ignore_nodes", "value": 1, "scope": "nation_self"},
        ])
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["research"] == 0

    def test_luxury_resource_production_is_unaffected(self):
        """By design (matching the existing tooltip-breakdown scope), ignore_nodes
        only ever gated general/unique resource nodes — luxury resources use a
        different production model and were never part of that gate."""
        nation = _nation([
            {"modifier_type": "luxury_resource_production", "value": 2, "scope": "nation_self"},
            {"modifier_type": "ignore_nodes", "value": 1, "scope": "nation_self"},
        ])
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        luxury_keys = [r["key"] for r in json_data["luxury_resources"]]
        if luxury_keys:
            assert calc["resource_production"][luxury_keys[0]] == 2

    def test_terrain_production_is_unaffected(self):
        """ignore_nodes must not touch terrain-tile production — that's
        ignore_terrain's job (see test_ignore_terrain_modifier.py)."""
        nation = _nation([
            {"modifier_type": "ignore_nodes", "value": 1, "scope": "nation_self"},
        ])
        nation["territory_types"] = {"plains": 8}  # 8 // 4 required = 2 base food
        schema = category_data["nations"]["schema"]
        calc = calculate_all_fields(nation, schema, "nation")
        assert calc["resource_production"]["food"] == 2
