"""
District upgrade toggles should be hidden from the nation page/edit form
until the nation meets the upgrade's requirements (most commonly a
researched technology) — unless the upgrade is already unlocked, in which
case it always stays visible so a player can see/toggle off what they have.

The actual hide/show logic lives in templates/_district_macros.html, calling
check_upgrade_requirements (calculations/field_calculations.py) which must be
registered as a Jinja global for the template to reach it at all.
"""
from calculations.field_calculations import check_upgrade_requirements


class TestCheckUpgradeRequirementsJinjaRegistration:
    def test_registered_as_jinja_global(self):
        from app_core import app
        import routes.nation_routes  # noqa: F401 — module-level import registers the Jinja global
        assert app.jinja_env.globals.get("check_upgrade_requirements") is check_upgrade_requirements


class TestCheckUpgradeRequirementsTechGating:
    def test_upgrade_requiring_unresearched_tech_is_blocked(self):
        nation = {"technologies": {"crop_rotation": {"researched": False}}}
        upg = {"key": "crop_rotation", "requirements": [{"type": "technology", "value": "crop_rotation"}]}
        assert check_upgrade_requirements(nation, upg) is False

    def test_upgrade_requiring_researched_tech_is_allowed(self):
        nation = {"technologies": {"crop_rotation": {"researched": True}}}
        upg = {"key": "crop_rotation", "requirements": [{"type": "technology", "value": "crop_rotation"}]}
        assert check_upgrade_requirements(nation, upg) is True

    def test_upgrade_with_no_requirements_is_always_allowed(self):
        nation = {"technologies": {}}
        upg = {"key": "free_upgrade", "requirements": []}
        assert check_upgrade_requirements(nation, upg) is True

    def test_missing_technology_entry_counts_as_unresearched(self):
        nation = {"technologies": {}}
        upg = {"key": "crop_rotation", "requirements": [{"type": "technology", "value": "crop_rotation"}]}
        assert check_upgrade_requirements(nation, upg) is False
