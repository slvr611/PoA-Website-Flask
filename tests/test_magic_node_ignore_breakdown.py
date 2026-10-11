"""
Regression test for a real report: the "Creation" (and "Manifest") magic
stance sets the ignore_magic_nodes law, which correctly makes
compute_resource_production skip adding the magic node bonus to the
nation's actual magic production total — but the resource_production
breakdown tooltip (built by _build_computed_contributions's "Node
production" section) never checked ignore_nodes / ignore_non_research_nodes
/ ignore_{resource}_nodes at all, so it kept showing a "Nodes" contribution
line for magic even though that bonus was never actually added to the
total. The real total was correct; only the tooltip was wrong.

Fixed by mirroring compute_resource_production's exact skip_nodes gate in
the breakdown builder, so a resource whose node bonus was excluded from
the total never gets a misleading "Nodes" line in its tooltip either.
"""
from unittest.mock import patch

import calculations.field_calculations as fc
import calculations.compute_functions as cf


def _minimal_target():
    return {"modifiers": [], "districts": []}


class TestNodeProductionBreakdownRespectsIgnoreNodesGates:
    def test_resource_specific_ignore_flag_hides_the_nodes_line(self):
        """Mirrors the real "Creation" magic stance scenario: ignore_magic_nodes."""
        overall_totals = {"magic_nodes": 3, "ignore_magic_nodes": 1}
        contribs = fc._build_computed_contributions(
            _minimal_target(), {}, overall_totals, {}, None, {},
        )
        assert not any(c.label == "Nodes" and "magic_production" in c.modifiers for c in contribs)

    def test_unaffected_resource_still_shows_its_nodes_line(self):
        """A resource without its own ignore flag must still get its Nodes
        line — this isn't a global suppression, just resource-specific."""
        overall_totals = {"magic_nodes": 3, "ignore_magic_nodes": 1, "food_nodes": 2}
        contribs = fc._build_computed_contributions(
            _minimal_target(), {}, overall_totals, {}, None, {},
        )
        food_nodes_contribs = [c for c in contribs if c.label == "Nodes" and "food_production" in c.modifiers]
        assert len(food_nodes_contribs) == 1
        assert food_nodes_contribs[0].modifiers["food_production"] == 4  # 2 nodes * (2 + 0 node_value)

    def test_global_ignore_nodes_hides_every_resources_nodes_line(self):
        overall_totals = {"magic_nodes": 3, "food_nodes": 2, "ignore_nodes": 1}
        contribs = fc._build_computed_contributions(
            _minimal_target(), {}, overall_totals, {}, None, {},
        )
        assert not any(c.label == "Nodes" for c in contribs)

    def test_ignore_non_research_nodes_still_shows_research(self):
        overall_totals = {"research_nodes": 2, "magic_nodes": 3, "ignore_non_research_nodes": 1}
        contribs = fc._build_computed_contributions(
            _minimal_target(), {}, overall_totals, {}, None, {},
        )
        labels_and_keys = [(c.label, set(c.modifiers.keys())) for c in contribs if c.label == "Nodes"]
        assert len(labels_and_keys) == 1
        assert "research_production" in labels_and_keys[0][1]

    def test_breakdown_omission_matches_the_real_total_omission(self):
        """The actual bug: the real total (compute_resource_production) and
        the tooltip (_build_computed_contributions) must agree on whether
        magic's node bonus is included — reproduces the exact "totals
        correct, tooltip wrong" mismatch by computing both side by side."""
        target = {"modifiers": [], "districts": [], "_id": "fake-id", "resource_storage": {}}
        overall_totals = {"magic_nodes": 3, "ignore_magic_nodes": 1}

        real_value = cf.compute_resource_production(
            "resource_production", target, 0, {}, overall_totals,
        )["magic"]

        contribs = fc._build_computed_contributions(
            target, {}, overall_totals, {}, None, {},
        )
        tooltip_includes_magic_nodes = any(
            c.label == "Nodes" and "magic_production" in c.modifiers for c in contribs
        )

        # Node bonus would be 3 * (2 + 0) = 6 if NOT ignored — real_value
        # must be 0 (no other production source configured) and the
        # tooltip must likewise show no Nodes line for magic.
        assert real_value == 0
        assert tooltip_includes_magic_nodes is False
