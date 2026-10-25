"""Regression test for compute_death_chance (calculations/compute_functions.py).

Found live 2026-10-25 on "Meepsi 'the Skinned God'" (age 9, elderly_age 3):
the age-based term alone, (9-3)*0.2 = 1.2, is a guaranteed death roll every
tick. Meepsi carries an immortal modifier, but is_mortal previously only
ever gated health_state_death_chance below -- the age-based component was
completely unaffected by immortality, so an immortal character still died
of old age the moment they got old enough.

Fixed by having is_mortal also gate the age-based terms (the base
(age - elderly_age) * 0.2 calculation and the elderly_death_start_early
bonus), scoped narrowly: cunning-based, flat `field`-modifier-driven, and
minimum_death_chance contributions are untouched, since those are other,
non-aging death sources immortality was never meant to block.
"""
import pytest

from calculations.compute_functions import compute_death_chance


def _mods(**kwargs):
    return dict(kwargs)


class TestMortalCharacterUnaffected:
    def test_age_based_term_still_applies(self):
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance("death_chance", target, 0, {}, _mods())
        assert value == 1.2

    def test_below_elderly_age_is_zero(self):
        target = {"age": 2, "elderly_age": 3}
        value = compute_death_chance("death_chance", target, 0, {}, _mods())
        assert value == 0


class TestImmortalCharacterIsProtectedFromAgeDeath:
    def test_age_based_term_is_zeroed_for_an_immortal_character(self):
        """The exact Meepsi scenario: age 9, elderly_age 3 would otherwise
        be a guaranteed (120%) death roll."""
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance("death_chance", target, 0, {}, _mods(immortal=1))
        assert value == 0

    def test_elderly_death_start_early_bonus_is_also_suppressed(self):
        target = {"age": 3, "elderly_age": 3}
        mortal_value = compute_death_chance(
            "death_chance", target, 0, {}, _mods(elderly_death_start_early=1)
        )
        immortal_value = compute_death_chance(
            "death_chance", target, 0, {}, _mods(elderly_death_start_early=1, immortal=1)
        )
        assert mortal_value > 0
        assert immortal_value == 0

    def test_zero_value_immortal_modifier_does_not_protect(self):
        """An immortal modifier with value 0 (the exact bad data found on
        Meepsi) must NOT be treated as immortal -- only a truthy value
        counts, matching `not overall_total_modifiers.get("immortal", 0)`."""
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance("death_chance", target, 0, {}, _mods(immortal=0))
        assert value == 1.2


class TestImmortalityIsScopedToAgingOnly:
    """Other death sources must still apply to an immortal character --
    immortality only ever blocks the AGE-based component."""

    def test_cunning_based_death_chance_still_applies(self):
        """compute_stat derives cunning from overall_total_modifiers, not
        from target directly -- base_value(0) + modifiers["cunning"],
        capped at target's cunning_cap (default 4)."""
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance(
            "death_chance", target, 0, {}, _mods(immortal=1, cunning=4, death_chance_per_cunning=0.1)
        )
        assert value == pytest.approx(0.4)

    def test_flat_field_modifier_still_applies(self):
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance("death_chance", target, 0, {}, _mods(immortal=1, death_chance=0.3))
        assert value == pytest.approx(0.3)

    def test_minimum_death_chance_floor_still_applies(self):
        target = {"age": 9, "elderly_age": 3}
        value = compute_death_chance(
            "death_chance", target, 0, {}, _mods(immortal=1, minimum_death_chance=0.05)
        )
        assert value == pytest.approx(0.05)

    def test_health_state_death_chance_still_applies_for_an_immortal_character(self):
        """Pre-existing behavior (unchanged by this fix) -- mortal_ignore_
        health_state_death_chance's skip only ever fires when is_mortal is
        True (despite its name, it's gated on "and is_mortal", not "and
        not is_mortal"), so it never actually applies to an immortal
        character -- this test pins that down explicitly so a future
        change to this condition doesn't silently start skipping it."""
        target = {"age": 1, "elderly_age": 3}
        value = compute_death_chance(
            "death_chance", target, 0, {},
            _mods(immortal=1, mortal_ignore_health_state_death_chance=1, health_state_death_chance=0.4),
        )
        assert value == pytest.approx(0.4)

    def test_mortal_ignore_flag_does_skip_it_for_a_mortal_character(self):
        """The flag's actual, intended case: a MORTAL character with
        mortal_ignore_health_state_death_chance set skips the health-state
        term -- unrelated to immortality, despite sharing the is_mortal
        variable."""
        target = {"age": 1, "elderly_age": 3}
        value = compute_death_chance(
            "death_chance", target, 0, {},
            _mods(mortal_ignore_health_state_death_chance=1, health_state_death_chance=0.4),
        )
        assert value == 0

    def test_health_state_death_chance_still_applies_without_mortal_ignore(self):
        target = {"age": 1, "elderly_age": 3}
        value = compute_death_chance(
            "death_chance", target, 0, {}, _mods(immortal=1, health_state_death_chance=0.4)
        )
        assert value == pytest.approx(0.4)
