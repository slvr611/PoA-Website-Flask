"""
Regression test for a data bug that silently broke the resource-node synergy
on 15 district types (courthouse, farm, dock, barracks, artifactory,
guardhouse, refuge, outpost, spire, stables, pasture, range, rift, library,
academy) game-wide — including the Madhan Empire's Courthouse, which sits on
a "stone" node but never got its +15% stability_gain_chance synergy bonus.

Root cause: _parse_synergies (routes/district_def_routes.py) built a
synergy's "requirement" list via `req.split(",")` without stripping each
resulting token. An admin typing "wood, stone" (a space after the comma —
the natural way to type a list) produced ["wood", " stone"] verbatim.
calculations/source_adapters.py's synergy_matches does an exact string
membership check (`node in requirement`), so a live tile node of "stone"
never matched the stored " stone" — the synergy could never activate no
matter what the district actually sat on.

Fixed by stripping each split token, not just the whole input string.
"""
from routes.district_def_routes import _parse_synergies


class TestParseSynergiesStripsEachRequirementToken:
    def test_space_after_comma_is_stripped(self):
        form = {
            "synergies-0-requirement": "wood, stone",
            "synergies-0-node_active": "1",
        }
        synergies = _parse_synergies(form)
        assert synergies[0]["requirement"] == ["wood", "stone"]

    def test_no_comma_single_requirement_still_works(self):
        form = {
            "synergies-0-requirement": "stone",
            "synergies-0-node_active": "1",
        }
        synergies = _parse_synergies(form)
        assert synergies[0]["requirement"] == "stone"

    def test_multiple_spaces_and_tabs_are_stripped(self):
        form = {
            "synergies-0-requirement": "wood ,  stone ,magic",
            "synergies-0-node_active": "1",
        }
        synergies = _parse_synergies(form)
        assert synergies[0]["requirement"] == ["wood", "stone", "magic"]


class TestDistrictAdapterSynergyMatchingAgainstLiveNodeData:
    """End-to-end confirmation through DistrictAdapter.collect (the actual
    code path used by calculate_all_fields) that a stripped requirement list
    matches a live tile node the way the Madhan Empire's courthouse-on-stone
    should, and that the pre-fix stray-space data would not have."""

    def _target_with_courthouse_on_stone(self):
        return {
            "name": "Test Nation",
            "districts": [{"def_key": "courthouse", "_id": "d1"}],
            "_calc_cache": {"district_node_map": {"d1": "stone"}},
        }

    def _collect(self, requirement):
        from unittest.mock import patch, MagicMock
        from calculations.source_adapters import DistrictAdapter

        fake_dd = {
            "key": "courthouse",
            "display_name": "Courthouse",
            "modifiers": [],
            "synergies": [{
                "requirement": requirement,
                "modifiers": [{"modifier_type": "stability_gain_chance", "value": 0.15, "scope": "nation_self"}],
                "node_active": True,
            }],
        }
        mock_mongo = MagicMock()
        mock_mongo.db.district_defs.find_one.return_value = fake_dd
        target = self._target_with_courthouse_on_stone()
        with patch("calculations.field_calculations.mongo", mock_mongo):
            contributions = DistrictAdapter.collect(target, {"properties": {}}, {}, {}, {})
        merged = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                merged[k] = merged.get(k, 0) + v
        return merged

    def test_stripped_requirement_grants_the_synergy_bonus(self):
        mods = self._collect(["wood", "stone"])
        assert mods.get("stability_gain_chance") == 0.15

    def test_stray_leading_space_would_have_silently_blocked_it(self):
        """Reproduces the exact pre-fix stored data: courthouse's synergy
        never activated for anyone, on any stone-node tile, game-wide."""
        mods = self._collect(["wood", " stone"])
        assert mods.get("stability_gain_chance", 0) == 0
