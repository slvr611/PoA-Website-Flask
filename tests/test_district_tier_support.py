"""
Districts above tier 1 need an active district of the same category one
tier lower to function (e.g. a tier 3 production district needs an active
tier 2 production district) -- check_district_requirements already
enforced this at BUILD time, but nothing re-checked it afterward, so a
nation that later lost its tier 2 district (territory loss, admin edit,
etc.) kept every bonus its now-unsupported tier 3 district ever granted,
forever.

get_district_support_map / get_district_inactive_reason
(calculations/field_calculations.py) re-run that same tier-chain check
continuously against the nation's current districts, and DistrictAdapter.
collect (calculations/source_adapters.py) -- the actual pipeline
calculate_all_fields uses for a nation's real modifier totals -- skips an
unsupported district's modifiers/synergies/upgrades/node-claim/upkeep
entirely rather than just flagging it for display.

Support cascades: a tier-(N-1) district only counts as "active" support
for a tier-N district if it is itself actively supported (recursively down
to tier 1, which has no requirement). Losing a tier 1 district therefore
disables tier 2 AND any tier 3 relying on that tier 2, not just the
immediate neighbor.
"""
from unittest.mock import patch, MagicMock

from calculations.field_calculations import get_district_support_map, get_district_inactive_reason
from calculations.source_adapters import DistrictAdapter


PRODUCTION_DEFS = {
    "ranch": {"key": "ranch", "display_name": "Ranch", "category": "production", "tier": 1, "modifiers": []},
    "sawmill": {"key": "sawmill", "display_name": "Sawmill", "category": "production", "tier": 1, "modifiers": []},
    "foundry": {"key": "foundry", "display_name": "Foundry", "category": "production", "tier": 2, "modifiers": []},
    "cache": {"key": "cache", "display_name": "Cache", "category": "production", "tier": 2, "modifiers": []},
    "nexus": {
        "key": "nexus", "display_name": "Nexus", "category": "production", "tier": 3,
        "modifiers": [{"modifier_type": "resource_production", "value": 5.0, "resource": "stone", "scope": "nation_self"}],
    },
    # A different category entirely, to make sure cross-category pairs never cross-satisfy a check.
    "forge": {"key": "forge", "display_name": "Forge", "category": "warfare", "tier": 1, "modifiers": []},
}


def _patch_district_defs():
    mock_mongo = MagicMock()
    mock_mongo.db.district_defs.find.return_value = list(PRODUCTION_DEFS.values())
    return patch("calculations.field_calculations.mongo", mock_mongo)


class TestGetDistrictSupportMap:
    def test_tier1_present_is_active(self):
        nation = {"districts": [{"def_key": "ranch", "_id": "d1"}]}
        with _patch_district_defs():
            assert ("production", 1) in get_district_support_map(nation)

    def test_tier2_active_when_tier1_present(self):
        nation = {"districts": [{"def_key": "ranch", "_id": "d1"}, {"def_key": "foundry", "_id": "d2"}]}
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("production", 1) in support
        assert ("production", 2) in support

    def test_tier2_inactive_when_tier1_missing(self):
        nation = {"districts": [{"def_key": "foundry", "_id": "d2"}]}
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("production", 2) not in support

    def test_tier3_cascades_inactive_when_tier1_missing_even_though_tier2_present(self):
        """The core cascading behavior: tier 2 is physically present but
        unsupported (no tier 1), so tier 3 can't lean on it either."""
        nation = {"districts": [{"def_key": "foundry", "_id": "d2"}, {"def_key": "nexus", "_id": "d3"}]}
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("production", 2) not in support
        assert ("production", 3) not in support

    def test_unrelated_category_does_not_satisfy_requirement(self):
        nation = {"districts": [{"def_key": "forge", "_id": "d1"}, {"def_key": "foundry", "_id": "d2"}]}
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("warfare", 1) in support
        assert ("production", 2) not in support


class TestDistrictSupportOverrideModifier:
    """An admin-placed 'district_support_override' modifier acts as a
    simulated, unconditionally-active district of its chosen
    category/tier -- a bypass for a nation that should keep a district's
    bonuses without actually building the supporting lower-tier one."""

    def test_override_directly_satisfies_the_tier_it_simulates(self):
        nation = {
            "districts": [{"def_key": "foundry", "_id": "d2"}],
            "modifiers": [{"modifier_type": "district_support_override", "district_category": "production", "tier": "1"}],
        }
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("production", 1) in support
        assert ("production", 2) in support

    def test_override_cascades_up_to_a_real_higher_tier(self):
        """A tier-1 override lets a real tier-2 become active, and that
        real (now-active) tier-2 in turn supports a real tier-3 -- the
        override doesn't need to be placed at every level, just the
        bottom of whatever gap exists."""
        nation = {
            "districts": [{"def_key": "foundry", "_id": "d2"}, {"def_key": "nexus", "_id": "d3"}],
            "modifiers": [{"modifier_type": "district_support_override", "district_category": "production", "tier": "1"}],
        }
        with _patch_district_defs():
            reason = get_district_inactive_reason(nation, {"def_key": "nexus", "_id": "d3"})
        assert reason == ""

    def test_override_for_a_different_category_does_not_help(self):
        nation = {
            "districts": [{"def_key": "nexus", "_id": "d3"}],
            "modifiers": [{"modifier_type": "district_support_override", "district_category": "warfare", "tier": "2"}],
        }
        with _patch_district_defs():
            reason = get_district_inactive_reason(nation, {"def_key": "nexus", "_id": "d3"})
        assert "Tier 2 Production" in reason

    def test_unrelated_modifier_types_are_ignored(self):
        nation = {
            "districts": [{"def_key": "nexus", "_id": "d3"}],
            "modifiers": [{"modifier_type": "stability_gain_chance", "value": 0.1}],
        }
        with _patch_district_defs():
            support = get_district_support_map(nation)
        assert ("production", 1) not in support


class TestGetDistrictInactiveReason:
    def test_tier1_is_always_active(self):
        nation = {"districts": [{"def_key": "ranch", "_id": "d1"}]}
        with _patch_district_defs():
            assert get_district_inactive_reason(nation, {"def_key": "ranch", "_id": "d1"}) == ""

    def test_tier3_active_with_full_chain(self):
        nation = {"districts": [
            {"def_key": "ranch", "_id": "d1"}, {"def_key": "foundry", "_id": "d2"}, {"def_key": "nexus", "_id": "d3"},
        ]}
        with _patch_district_defs():
            reason = get_district_inactive_reason(nation, {"def_key": "nexus", "_id": "d3"})
        assert reason == ""

    def test_tier3_inactive_without_tier2_names_what_is_needed(self):
        nation = {"districts": [{"def_key": "ranch", "_id": "d1"}, {"def_key": "nexus", "_id": "d3"}]}
        with _patch_district_defs():
            reason = get_district_inactive_reason(nation, {"def_key": "nexus", "_id": "d3"})
        assert "Tier 2 Production" in reason
        assert "Cache" in reason and "Foundry" in reason

    def test_legacy_type_only_district_is_never_flagged(self):
        nation = {"districts": [{"type": "some_legacy_district"}]}
        with _patch_district_defs():
            reason = get_district_inactive_reason(nation, {"type": "some_legacy_district"})
        assert reason == ""

    def test_reused_support_map_matches_freshly_computed_one(self):
        nation = {"districts": [{"def_key": "ranch", "_id": "d1"}, {"def_key": "nexus", "_id": "d3"}]}
        with _patch_district_defs():
            precomputed = get_district_support_map(nation)
            reason = get_district_inactive_reason(nation, {"def_key": "nexus", "_id": "d3"}, support_map=precomputed)
        assert "Tier 2 Production" in reason


class TestDistrictAdapterSkipsUnsupportedDistrictModifiers:
    """End-to-end confirmation through the actual pipeline
    calculate_all_fields uses (DistrictAdapter.collect) -- not just the
    standalone helper -- that an unsupported district contributes nothing
    at all to a nation's real modifier totals."""

    def _collect(self, districts):
        target = {"name": "Test Nation", "districts": districts}
        with _patch_district_defs():
            contributions = DistrictAdapter.collect(target, {"properties": {}}, {}, {}, {})
        merged = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                merged[k] = merged.get(k, 0) + v
        return merged

    def test_nexus_contributes_nothing_without_tier2_support(self):
        mods = self._collect([{"def_key": "ranch", "_id": "d1"}, {"def_key": "nexus", "_id": "d3"}])
        assert mods.get("stone_production", 0) == 0

    def test_nexus_contributes_normally_with_full_chain(self):
        mods = self._collect([
            {"def_key": "ranch", "_id": "d1"}, {"def_key": "foundry", "_id": "d2"}, {"def_key": "nexus", "_id": "d3"},
        ])
        assert mods.get("stone_production", 0) == 5.0

    def test_supporting_lower_tier_districts_keep_contributing_regardless_of_higher_tiers(self):
        """Losing support only ever disables the UNSUPPORTED (higher-tier)
        district -- a tier 1/tier 2 district's own modifiers are untouched
        whether or not a tier 3 district exists above them."""
        extra_defs = dict(PRODUCTION_DEFS)
        extra_defs["foundry"] = dict(
            extra_defs["foundry"],
            modifiers=[{"modifier_type": "resource_production", "value": 2.0, "resource": "wood", "scope": "nation_self"}],
        )
        mock_mongo = MagicMock()
        mock_mongo.db.district_defs.find.return_value = list(extra_defs.values())
        target = {"name": "Test Nation", "districts": [
            {"def_key": "ranch", "_id": "d1"}, {"def_key": "foundry", "_id": "d2"}, {"def_key": "nexus", "_id": "d3"},
        ]}
        with patch("calculations.field_calculations.mongo", mock_mongo):
            contributions = DistrictAdapter.collect(target, {"properties": {}}, {}, {}, {})
        merged = {}
        for c in contributions:
            for k, v in c.modifiers.items():
                merged[k] = merged.get(k, 0) + v
        assert merged.get("wood_production", 0) == 2.0
