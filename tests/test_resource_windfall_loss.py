"""
Regression tests for a real bug found while adding "United Peoples"'
resource-loss-on-stability-loss mechanic: _grant_resource_windfall only
ever GRANTED resources — a negative `rolls` value (as authored by a
negative resource_windfall_on_stability_loss modifier, meant to represent
a LOSS rather than a gain) hit its `if rolls <= 0: return ""` guard and
silently did nothing. The modifier_type is meant to be bidirectional by
design: Disjointed Anarchists profits from instability with a positive
value, United Peoples wastes resources on the same event with a negative
one — only the negative/loss direction was ever actually broken.

Fixed by making _grant_resource_windfall apply a loss (clamped at 0 per
resource) when rolls < 0, instead of no-op'ing.
"""
from unittest.mock import patch
from bson import ObjectId

import helpers.tick_helpers as th


class TestGrantResourceWindfallSupportsLoss:
    def test_negative_rolls_removes_resources_instead_of_a_no_op(self):
        old_nation = {"name": "United Peoples Nation"}
        new_nation = {"resource_storage": {"food": 20, "wood": 20, "stone": 20, "mounts": 20, "magic": 20}}

        with patch("random.choice", side_effect=["food", "wood", "food", "stone", "mounts"]):
            result = th._grant_resource_windfall(old_nation, new_nation, -5, "test event")

        assert new_nation["resource_storage"]["food"] == 18   # -2
        assert new_nation["resource_storage"]["wood"] == 19   # -1
        assert new_nation["resource_storage"]["stone"] == 19  # -1
        assert new_nation["resource_storage"]["mounts"] == 19  # -1
        assert "lost" in result

    def test_loss_is_clamped_at_zero_per_resource(self):
        old_nation = {"name": "United Peoples Nation"}
        new_nation = {"resource_storage": {"food": 2}}

        with patch("random.choice", return_value="food"):
            th._grant_resource_windfall(old_nation, new_nation, -5, "test event")

        assert new_nation["resource_storage"]["food"] == 0

    def test_positive_rolls_still_grants_as_before(self):
        """Regression guard: the existing gain behavior (tech researched,
        expansion, Disjointed Anarchists' positive stability-loss windfall)
        must be completely unaffected."""
        old_nation = {"name": "Disjointed Anarchists Nation"}
        new_nation = {"resource_storage": {"food": 5}}

        with patch("random.choice", return_value="food"):
            result = th._grant_resource_windfall(old_nation, new_nation, 3, "test event")

        assert new_nation["resource_storage"]["food"] == 8
        assert "gained" in result

    def test_zero_rolls_is_a_no_op(self):
        old_nation = {"name": "Test Nation"}
        new_nation = {"resource_storage": {"food": 5}}

        result = th._grant_resource_windfall(old_nation, new_nation, 0, "test event")

        assert result == ""
        assert new_nation["resource_storage"] == {"food": 5}

    def test_research_is_never_selected_for_gain_or_loss(self):
        old_nation = {"name": "Test Nation"}
        new_nation = {"resource_storage": {}}

        with patch("random.random", return_value=0.0):
            th._grant_resource_windfall(old_nation, new_nation, 20, "test event")
            gained_keys = set(new_nation["resource_storage"].keys())
            new_nation["resource_storage"] = {}
            th._grant_resource_windfall(old_nation, new_nation, -20, "test event")
            lost_keys = set(new_nation["resource_storage"].keys())

        assert "research" not in gained_keys
        assert "research" not in lost_keys


class TestAdjustStabilityWindfallIntegration:
    """End-to-end through adjust_stability, exactly as nation_stability_tick
    calls it — the actual United Peoples / Disjointed Anarchists scenario."""

    _SCHEMA = {"properties": {"stability": {"enum": ["Broken", "Unstable", "Balanced", "Stable", "Thriving"]}}}

    def test_negative_modifier_value_causes_a_real_resource_loss_on_stability_loss(self):
        old_nation = {
            "name": "United Peoples Test Nation", "stability": "Balanced",
            "resource_windfall_on_stability_loss": -5,
        }
        new_nation = {"resource_storage": {"food": 20, "wood": 20, "stone": 20, "mounts": 20, "magic": 20}}

        with patch("random.choice", return_value="food"):
            result = th.adjust_stability(old_nation, new_nation, self._SCHEMA, amounts=[-1], reasons=["stability_loss_chance"])

        assert new_nation["resource_storage"]["food"] == 15  # lost 5
        assert "lost" in result
        assert new_nation["stability"] == "Unstable"

    def test_positive_modifier_value_still_grants_on_stability_loss(self):
        """Disjointed Anarchists-style: unaffected by the loss-handling fix."""
        old_nation = {
            "name": "Disjointed Anarchists Test Nation", "stability": "Balanced",
            "resource_windfall_on_stability_loss": 5,
        }
        new_nation = {"resource_storage": {"food": 10}}

        with patch("random.choice", return_value="food"):
            result = th.adjust_stability(old_nation, new_nation, self._SCHEMA, amounts=[-1], reasons=["stability_loss_chance"])

        assert new_nation["resource_storage"]["food"] == 15  # gained 5
        assert "gained" in result

    def test_windfall_scales_with_number_of_levels_lost(self):
        old_nation = {
            "name": "United Peoples Test Nation", "stability": "Thriving",
            "resource_windfall_on_stability_loss": -5,
        }
        new_nation = {"resource_storage": {"food": 20}}

        with patch("random.choice", return_value="food"):
            th.adjust_stability(old_nation, new_nation, self._SCHEMA, amounts=[-2], reasons=["stability_loss_chance"])

        assert new_nation["resource_storage"]["food"] == 10  # lost 5 * 2 levels = 10

    def test_no_modifier_means_no_windfall_either_direction(self):
        old_nation = {"name": "Plain Nation", "stability": "Balanced"}
        new_nation = {"resource_storage": {"food": 10}}

        result = th.adjust_stability(old_nation, new_nation, self._SCHEMA, amounts=[-1], reasons=["stability_loss_chance"])

        assert new_nation["resource_storage"] == {"food": 10}
        assert "windfall" not in result

    def test_gaining_stability_never_triggers_the_loss_windfall(self):
        old_nation = {
            "name": "United Peoples Test Nation", "stability": "Balanced",
            "resource_windfall_on_stability_loss": -5,
        }
        new_nation = {"resource_storage": {"food": 10}}

        result = th.adjust_stability(old_nation, new_nation, self._SCHEMA, amounts=[1], reasons=["stability_gain_chance"])

        assert new_nation["resource_storage"] == {"food": 10}
        assert "windfall" not in result
