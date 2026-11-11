"""
Tests for calculate_unit_strength (calculations/field_calculations.py) --
a unit's raw combat power from its stats alone, using the exact per-stat
point weights from the unit creator's point-cost formula (templates/
units_item.html's calculateCost), with cost/upkeep/traits/district factors
and attack/defense (never priced by that formula) excluded entirely.
"""
from calculations.field_calculations import calculate_unit_strength


class TestPerStatWeights:
    def test_hp_is_weighted_one_point_per_point_above_the_first_free_point(self):
        # hp=5 -> (5 - 1) * 1 = 4
        assert calculate_unit_strength({"hp": 5}) == 4

    def test_morale_is_weighted_two_points_per_point_above_the_first_free_point(self):
        # morale=3 -> (3 - 1) * 2 = 4
        assert calculate_unit_strength({"morale": 3}) == 4

    def test_damage_is_weighted_three_points_per_point(self):
        assert calculate_unit_strength({"damage": 2}) == 6

    def test_retaliation_damage_is_weighted_two_points_per_point(self):
        assert calculate_unit_strength({"retaliation_damage": 3}) == 6

    def test_armor_is_weighted_four_points_per_point(self):
        assert calculate_unit_strength({"armor": 2}) == 8

    def test_speed_is_weighted_two_points_per_point(self):
        assert calculate_unit_strength({"speed": 3}) == 6

    def test_stats_sum_together(self):
        stats = {"hp": 3, "morale": 2, "damage": 1, "armor": 1}
        # hp: (3-1)*1=2, morale: (2-1)*2=2, damage: 1*3=3, armor: 1*4=4 -> 11
        assert calculate_unit_strength(stats) == 11


class TestAttackDefenseExcluded:
    def test_attack_and_defense_never_contribute_to_strength(self):
        assert calculate_unit_strength({"attack": 10, "defense": 10}) == 0

    def test_attack_and_defense_buffs_are_still_ignored_alongside_other_stats(self):
        stats = {"attack": 4, "defense": 4, "hp": 3}
        assert calculate_unit_strength(stats) == 2  # only (3-1)*1


class TestDisabledStatsAreSkipped:
    def test_a_stat_missing_entirely_contributes_nothing(self):
        assert calculate_unit_strength({"hp": 3}) == 2

    def test_a_stat_explicitly_none_contributes_nothing(self):
        assert calculate_unit_strength({"hp": 3, "morale": None}) == 2


class TestClassFreeBonuses:
    def test_infantry_gets_two_free_hp(self):
        # melee, non-cavalry, non-support = infantry -> hp=3 free up to 3 (1 base free + 2 infantry free)
        assert calculate_unit_strength({"hp": 3}, melee=True, cavalry=False, is_support=False) == 0
        assert calculate_unit_strength({"hp": 5}, melee=True, cavalry=False, is_support=False) == 2

    def test_cavalry_melee_unit_does_not_get_the_infantry_hp_discount(self):
        # melee + cavalry is not infantry -> normal hp weighting
        assert calculate_unit_strength({"hp": 3}, melee=True, cavalry=True, is_support=False) == 2

    def test_cavalry_gets_one_free_speed(self):
        assert calculate_unit_strength({"speed": 1}, cavalry=True) == 0
        assert calculate_unit_strength({"speed": 3}, cavalry=True) == 4  # (3-1)*2

    def test_hp_and_speed_never_go_negative_from_free_bonuses(self):
        # free bonus larger than the actual stat still floors at 0, not negative
        assert calculate_unit_strength({"hp": 1}, melee=True, cavalry=False, is_support=False) == 0
        assert calculate_unit_strength({"speed": 0}, cavalry=True) == 0

    def test_ranged_unit_gets_one_free_max_range(self):
        assert calculate_unit_strength({"range": 1}, ranged=True) == 0
        assert calculate_unit_strength({"range": 3}, ranged=True) == 4  # (3-1-1)*4


class TestRangeStat:
    def test_symmetric_range_uses_the_single_value_for_both_bounds(self):
        # range=3, min==max -> (0)*2 + max(0, 3-1)*4 = 8
        assert calculate_unit_strength({"range": 3}) == 8

    def test_asymmetric_range_string_prices_both_the_spread_and_the_max(self):
        # "1-3" -> (3-1)*2 + max(0, 3-1)*4 = 4 + 8 = 12
        assert calculate_unit_strength({"range": "1-3"}) == 12


class TestReturnType:
    def test_whole_number_result_is_returned_as_an_int(self):
        result = calculate_unit_strength({"hp": 3})
        assert result == 2
        assert isinstance(result, int)
