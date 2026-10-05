"""
Regression tests for the "Hearth" nomad camp stance's hunter food production
bonus (json-data/schemas/nations.json's nomad_camp_type law).

Bug found while verifying the setup: Hearth was supposed to grant hunters
+1 baseline food production, plus another +1 if the nation has access to
farmers or fishermen (i.e. a farm or dock district). The baseline was
correctly wired as a flat "hunter_food_production: 1" law key, but the
conditional half was a flat "hunter_food_production_from_dock_or_farm: 1"
key — a field name calculate_job_details's job-modifier matching never
recognized (it only recognizes "{job}_{resource}_production" / "_upkeep",
and this didn't end in either), so it was silently dropped and had zero
effect, regardless of the nation's districts.

Fixed by replacing it with a "_modifiers" entry (job_resource_production,
job=hunter, resource=food) gated by a new condition_scaling,
per_x_specific_districts(scaling_extra="farm,dock") >= 1 — an OR check
across the two specific districts that unlock the farmer/fisherman jobs
(see json-data/jobs.json). per_x_district_category couldn't express this:
farm and dock share the "food" category with Pasture/Granary/Fishery/Mills,
which must NOT trigger this bonus.
"""
from app_core import category_data, json_data
from calculations.field_calculations import sum_modifier_totals, calculate_all_fields
from calculations.scaling_methods import per_x_specific_districts, SCALING_METHODS


def _hearth_law():
    return category_data["nations"]["schema"]["properties"]["nomad_camp_type"]["laws"]["Hearth"]


class TestHearthLawConfiguration:
    def test_baseline_flat_bonus_is_one(self):
        assert _hearth_law()["hunter_food_production"] == 1

    def test_no_longer_has_the_dead_field_key(self):
        assert "hunter_food_production_from_dock_or_farm" not in _hearth_law()

    def test_conditional_modifier_uses_job_and_resource_not_scaling_extra(self):
        """The exact historical footgun from the consumption_stance/
        slavery_stance incident: field_template placeholders must be
        resolved via the modifier_type's own declared extra_fields (job,
        resource for job_resource_production), not "scaling_extra" (that
        key is reserved for the modifier's OWN scaling, and
        condition_scaling_extra for the condition's)."""
        mod = _hearth_law()["_modifiers"][0]
        assert mod["modifier_type"] == "job_resource_production"
        assert mod.get("job") == "hunter"
        assert mod.get("resource") == "food"
        assert "scaling_extra" not in mod

    def test_condition_targets_farm_and_dock_specifically(self):
        mod = _hearth_law()["_modifiers"][0]
        assert mod.get("condition_scaling") == "per_x_specific_districts"
        assert mod.get("condition_scaling_extra") == "farm,dock"
        assert mod.get("condition_operator") == ">="
        assert mod.get("condition_value") == 1

    def test_scope_resolves_direct_to_nation_self(self):
        assert _hearth_law()["_modifiers"][0].get("scope") == "nation_self"


class TestSumModifierTotalsResolvesTheHearthModifier:
    """Pins the resolution mechanism directly, independent of schema content."""

    def test_condition_unmet_without_farm_or_dock(self):
        target = {"districts": []}
        totals = sum_modifier_totals(_hearth_law()["_modifiers"], target)
        assert "hunter_food_production" not in totals

    def test_condition_met_with_a_farm(self):
        target = {"districts": [{"def_key": "farm"}]}
        totals = sum_modifier_totals(_hearth_law()["_modifiers"], target)
        assert totals["hunter_food_production"] == 1

    def test_condition_met_with_a_dock(self):
        target = {"districts": [{"def_key": "dock"}]}
        totals = sum_modifier_totals(_hearth_law()["_modifiers"], target)
        assert totals["hunter_food_production"] == 1

    def test_condition_does_not_double_count_with_both(self):
        target = {"districts": [{"def_key": "farm"}, {"def_key": "dock"}]}
        totals = sum_modifier_totals(_hearth_law()["_modifiers"], target)
        assert totals["hunter_food_production"] == 1

    def test_other_food_category_districts_do_not_satisfy_the_condition(self):
        """The whole reason per_x_specific_districts exists instead of
        reusing per_x_district_category: farm/dock's shared "food" category
        also includes Pasture/Granary/Fishery/Mills, none of which should
        trigger this bonus."""
        target = {"districts": [{"def_key": "pasture"}, {"def_key": "granary"},
                                 {"def_key": "fishery"}, {"def_key": "mills"}]}
        totals = sum_modifier_totals(_hearth_law()["_modifiers"], target)
        assert "hunter_food_production" not in totals


class TestPerXSpecificDistrictsScalingMethod:
    def test_registered_under_its_own_name(self):
        assert SCALING_METHODS.get("per_x_specific_districts") is per_x_specific_districts

    def test_counts_only_matching_def_keys(self):
        target = {"districts": [{"def_key": "farm"}, {"def_key": "mine"}, {"def_key": "dock"}]}
        assert per_x_specific_districts(target, scaling_x=1, scaling_extra="farm,dock") == 2

    def test_divides_by_scaling_x(self):
        target = {"districts": [{"def_key": "farm"}, {"def_key": "dock"}]}
        assert per_x_specific_districts(target, scaling_x=2, scaling_extra="farm,dock") == 1

    def test_no_scaling_extra_returns_zero(self):
        target = {"districts": [{"def_key": "farm"}]}
        assert per_x_specific_districts(target, scaling_x=1, scaling_extra="") == 0

    def test_tolerates_whitespace_in_the_list(self):
        target = {"districts": [{"def_key": "farm"}]}
        assert per_x_specific_districts(target, scaling_x=1, scaling_extra=" farm , dock ") == 1


class TestHearthBonusEndToEnd:
    """Full pipeline: nomad_camp_type law -> law_totals -> calculate_job_details's
    job-modifier matching -> the hunter job's actual computed food production."""

    def _nation(self, districts):
        return {
            "name": "TestHearthNation", "nomad_camp_type": "Hearth", "temperament": "Player",
            "jobs": {"hunter": 1}, "districts": districts, "money": 0,
            "primary_race": "", "primary_culture": "", "primary_religion": "", "modifiers": [],
        }

    def test_baseline_only_without_farm_or_dock(self):
        schema = category_data["nations"]["schema"]
        base = json_data["jobs"]["hunter"]["production"]["food"]
        calculated, _ = calculate_all_fields(self._nation([]), schema, "nation", return_breakdowns=True)
        assert calculated["job_details"]["hunter"]["production"]["food"] == base + 1

    def test_extra_bonus_with_a_farm(self):
        schema = category_data["nations"]["schema"]
        base = json_data["jobs"]["hunter"]["production"]["food"]
        calculated, _ = calculate_all_fields(
            self._nation([{"_id": "d1", "def_key": "farm"}]), schema, "nation", return_breakdowns=True
        )
        assert calculated["job_details"]["hunter"]["production"]["food"] == base + 2

    def test_extra_bonus_with_a_dock(self):
        schema = category_data["nations"]["schema"]
        base = json_data["jobs"]["hunter"]["production"]["food"]
        calculated, _ = calculate_all_fields(
            self._nation([{"_id": "d1", "def_key": "dock"}]), schema, "nation", return_breakdowns=True
        )
        assert calculated["job_details"]["hunter"]["production"]["food"] == base + 2
