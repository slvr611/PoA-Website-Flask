"""
Tests for helpers.tick_helpers.character_stat_gain_tick.

The tick used to re-derive its own AI-only chance bonus by hand (an extra
cunning*0.1 term stacked on top of compute_stat_gain_chance's already
cunning-scaled value, plus a hardcoded +0.25/+0.5 immortal bonus, with no
upper cap) instead of trusting the already fully modifier-driven
"stat_gain_chance" field computed by compute_stat_gain_chance. That field
already accounts for cunning scaling, the immortal_stat_gain_chance modifier,
and any title/district/tech contributions, clamped to [0, 1]. These tests
lock in that the tick now simply consumes stat_gain_chance as-is for both
AI and player characters.
"""
from unittest.mock import patch

from helpers.tick_helpers import character_stat_gain_tick
from calculations.field_calculations import sum_modifier_totals


def _character(**overrides):
    char = {
        "name": "Test Character",
        "health_status": "Healthy",
        "player": None,  # AI by default
        "stat_gain_chance": 0.5,
        "rulership": 1, "rulership_cap": 4,
        "cunning": 1, "cunning_cap": 4,
        "charisma": 1, "charisma_cap": 4,
        "prowess": 1, "prowess_cap": 4,
        "magic": 1, "magic_cap": 4,
        "strategy": 1, "strategy_cap": 4,
    }
    char.update(overrides)
    return char


class TestStatGainChanceTrustsComputedValue:
    def test_ai_uses_stat_gain_chance_directly_no_extra_bonus(self):
        old_character = _character(stat_gain_chance=0.5, cunning=10)
        new_character = {}
        with patch("random.random", return_value=0.6):  # above 0.5 -> no gain
            character_stat_gain_tick(old_character, new_character, {})
        assert new_character["stat_gain_chance_at_tick"] == 0.5

    def test_ai_no_gain_when_roll_exceeds_stored_chance(self):
        old_character = _character(stat_gain_chance=0.5, cunning=10)
        new_character = {}
        # 0.55 would have passed under the old buggy math (0.5 + 10*0.1 + 0.5
        # uncapped), but must fail against the plain stored chance of 0.5.
        with patch("random.random", return_value=0.55):
            result = character_stat_gain_tick(old_character, new_character, {})
        assert result == ""
        assert new_character.get("modifiers", []) == []

    def test_ai_gain_when_roll_under_stored_chance(self):
        # Cap every stat but cunning so only one stat is eligible to gain,
        # keeping the assertion on modifier count/shape unambiguous.
        old_character = _character(
            stat_gain_chance=0.9,
            rulership=4, rulership_cap=4,
            cunning=1, cunning_cap=4,
            charisma=4, charisma_cap=4,
            prowess=4, prowess_cap=4,
            magic=4, magic_cap=4,
            strategy=4, strategy_cap=4,
        )
        new_character = {}
        with patch("random.random", return_value=0.1):
            result = character_stat_gain_tick(old_character, new_character, {})
        assert "gained a level of cunning" in result
        assert len(new_character["modifiers"]) == 1
        assert new_character["modifiers"][0]["value"] == 1
        assert new_character["modifiers"][0]["duration"] == -1
        assert new_character["modifiers"][0]["modifier_type"] == "attribute"
        assert new_character["modifiers"][0]["attribute"] == "cunning"

    def test_player_uses_stat_gain_chance_directly(self):
        old_character = _character(player="some-player-id", stat_gain_chance=0.3)
        new_character = {}
        with patch("random.random", return_value=0.2):
            result = character_stat_gain_tick(old_character, new_character, {})
        assert "gained a level of" in result
        assert new_character["stat_gain_chance_at_tick"] == 0.3

    def test_zero_chance_short_circuits(self):
        old_character = _character(stat_gain_chance=0)
        new_character = {}
        result = character_stat_gain_tick(old_character, new_character, {})
        assert result == ""
        assert "stat_gain_chance_at_tick" not in new_character

    def test_dead_character_returns_immediately(self):
        old_character = _character(health_status="Dead", stat_gain_chance=1.0)
        new_character = {}
        result = character_stat_gain_tick(old_character, new_character, {})
        assert result == ""
        assert new_character == {}

    def test_stat_at_cap_is_never_gained(self):
        old_character = _character(stat_gain_chance=1.0, cunning=4, cunning_cap=4)
        new_character = {}
        with patch("random.random", return_value=0.0):
            character_stat_gain_tick(old_character, new_character, {})
        gained_stats = [m["attribute"] for m in new_character.get("modifiers", [])]
        assert "cunning" not in gained_stats


class TestStatGainModifierUsesModernShape:
    def test_gained_modifier_uses_modifier_type_attribute(self):
        old_character = _character(
            stat_gain_chance=1.0,
            rulership=4, rulership_cap=4,
            cunning=1, cunning_cap=4,
            charisma=4, charisma_cap=4,
            prowess=4, prowess_cap=4,
            magic=4, magic_cap=4,
            strategy=4, strategy_cap=4,
        )
        new_character = {}
        with patch("random.random", return_value=0.0):
            character_stat_gain_tick(old_character, new_character, {})
        modifier = new_character["modifiers"][0]
        assert modifier["modifier_type"] == "attribute"
        assert modifier["attribute"] == "cunning"
        assert "field" not in modifier

    def test_gained_modifier_resolves_through_sum_modifier_totals(self):
        # End-to-end check that the new shape is actually consumed correctly
        # by the same modifier-summing code every other stat modifier goes
        # through (calculate_title_modifiers-adjacent contribution pipeline).
        old_character = _character(
            stat_gain_chance=1.0,
            rulership=4, rulership_cap=4,
            cunning=1, cunning_cap=4,
            charisma=4, charisma_cap=4,
            prowess=4, prowess_cap=4,
            magic=4, magic_cap=4,
            strategy=4, strategy_cap=4,
        )
        new_character = {}
        with patch("random.random", return_value=0.0):
            character_stat_gain_tick(old_character, new_character, {})
        totals = sum_modifier_totals(new_character["modifiers"])
        assert totals["cunning"] == 1
