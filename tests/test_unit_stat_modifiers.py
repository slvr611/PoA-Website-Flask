"""
Tests for _apply_unit_stat_modifiers, specifically the land_attack/land_defense/
naval_attack/naval_defense bare-key handling that lets nation-wide aggregate
modifiers (e.g. prosperity role bonuses/penalties) also apply per-unit.
"""
from calculations.field_calculations import _apply_unit_stat_modifiers

_BASE_STATS = {
    "attack": 0, "defense": 0, "hp": 5, "morale": 2, "damage": 1,
    "retaliation_damage": 2, "range": 1, "speed": 2, "armor": 0,
}
_NO_ROLES = {"melee": False, "ranged": False, "cavalry": False}


class TestBareStrengthUnchanged:
    def test_bare_strength_applies_to_both_attack_and_defense(self):
        sources = [{"label": "Military Funding: Substantial", "modifiers": {"strength": 2}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] == 2
        assert effective["defense"] == 2
        assert breakdown["attack"] == [{"label": "Military Funding: Substantial", "value": 2}]
        assert breakdown["defense"] == [{"label": "Military Funding: Substantial", "value": 2}]


class TestLandAttackDefenseBareKeys:
    def test_land_defense_only_affects_defense(self):
        sources = [{"label": "Prosperity: Savior (Wretched)", "modifiers": {"land_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] == 0
        assert effective["defense"] == -3
        assert "attack" not in breakdown
        assert breakdown["defense"] == [{"label": "Prosperity: Savior (Wretched)", "value": -3}]

    def test_land_attack_only_affects_attack(self):
        sources = [{"label": "Prosperity: Ravager (Wretched)", "modifiers": {"land_attack": 3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] == 3
        assert effective["defense"] == 0

    def test_naval_defense_does_not_affect_land_units(self):
        sources = [{"label": "Prosperity: Savior (Wretched)", "modifiers": {"naval_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["defense"] == 0
        assert breakdown == {}

    def test_naval_defense_affects_naval_units(self):
        sources = [{"label": "Prosperity: Savior (Wretched)", "modifiers": {"naval_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Naval", False, False, _NO_ROLES, "Galley", sources
        )
        assert effective["defense"] == -3

    def test_disabled_stat_is_never_touched(self):
        """A unit with has_attack/has_defense False (attack/defense is None) must
        stay None even when a matching bare modifier is present."""
        disabled_stats = {**_BASE_STATS, "attack": None, "defense": None}
        sources = [{"label": "Military Funding: Substantial", "modifiers": {"strength": 2}},
                   {"label": "Prosperity: Savior (Wretched)", "modifiers": {"land_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            disabled_stats, "Land", False, False, _NO_ROLES, "Something", sources
        )
        assert effective["attack"] is None
        assert effective["defense"] is None
        assert breakdown == {}

    def test_strength_and_land_defense_stack(self):
        """Matches the real Radhan case: military funding + conscription (strength)
        combined with the Wretched-Savior prosperity penalty (land_defense)."""
        sources = [
            {"label": "Military Funding: Substantial", "modifiers": {"strength": 2}},
            {"label": "Conscription Type: High", "modifiers": {"strength": -1}},
            {"label": "Prosperity: Savior (Wretched)", "modifiers": {"land_defense": -3}},
        ]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] == 1
        assert effective["defense"] == -2
        assert len(breakdown["attack"]) == 2
        assert len(breakdown["defense"]) == 3


class TestSupportUnitsUnaffected:
    def test_support_units_ignore_land_attack_defense_keys(self):
        """Support units have no bare_attack_key/bare_defense_key equivalent —
        land_attack/land_defense must not leak onto them."""
        sources = [{"label": "Prosperity: Savior (Wretched)", "modifiers": {"land_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", True, False, _NO_ROLES, "Merchant", sources
        )
        assert effective["defense"] == 0
        assert breakdown == {}


class TestStatCaps:
    """2026-10-26 request: every stat maxes out at double its base value,
    except attack/defense which instead cap at base + 4."""

    def test_speed_caps_at_double_base(self):
        """Base speed is 2 (see _BASE_STATS), so the cap is 4."""
        sources = [{"label": "Mount Upgrade", "modifiers": {"unit_speed": 5}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["speed"] == 4
        assert breakdown["speed"] == [
            {"label": "Mount Upgrade", "value": 5},
            {"label": "Capped", "value": -3},  # 2 + 5 = 7, capped down to 4
        ]

    def test_zero_base_armor_can_never_rise_above_zero(self):
        """0 * 2 = 0 -- a unit with no base armor has no room to gain any,
        even with a positive modifier."""
        sources = [{"label": "Reinforced Plating", "modifiers": {"unit_armor": 3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["armor"] == 0
        assert breakdown["armor"] == [
            {"label": "Reinforced Plating", "value": 3},
            {"label": "Capped", "value": -3},
        ]

    def test_stat_under_double_base_is_unaffected(self):
        sources = [{"label": "Mount Upgrade", "modifiers": {"unit_speed": 1}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["speed"] == 3  # 2 + 1 = 3, under the cap of 4
        assert breakdown["speed"] == [{"label": "Mount Upgrade", "value": 1}]

    def test_attack_defense_cap_at_base_plus_four_not_double(self):
        """Base attack/defense is 0 (see _BASE_STATS); doubling would cap at
        0, but the flat +4 rule instead allows up to 4."""
        sources = [{"label": "Military Funding: Substantial", "modifiers": {"strength": 6}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] == 4
        assert effective["defense"] == 4
        assert breakdown["attack"][-1] == {"label": "Capped", "value": -2}
        assert breakdown["defense"][-1] == {"label": "Capped", "value": -2}

    def test_negative_modifiers_are_not_capped_from_below(self):
        """The cap is a maximum only -- a stat can still be driven negative
        (or to 0) with no floor, same as before caps existed."""
        sources = [{"label": "Prosperity: Savior (Wretched)", "modifiers": {"land_defense": -3}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            _BASE_STATS, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["defense"] == -3
        assert "Capped" not in [e["label"] for e in breakdown["defense"]]

    def test_disabled_stat_is_never_capped(self):
        disabled_stats = {**_BASE_STATS, "attack": None, "defense": None}
        sources = [{"label": "Military Funding: Substantial", "modifiers": {"strength": 10}}]
        effective, breakdown = _apply_unit_stat_modifiers(
            disabled_stats, "Land", False, False, _NO_ROLES, "Auxiliary", sources
        )
        assert effective["attack"] is None
        assert effective["defense"] is None
