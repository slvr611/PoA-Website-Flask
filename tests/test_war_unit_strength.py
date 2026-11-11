"""
Tests for routes.war_routes._unit_types_for_nation's "strength" field and
war_item's total_strength -- the war page's "combat power from stats alone"
score (calculate_unit_strength), reusing the unit creator's per-stat point
weights, with nation-level buffs (effective_stats) factored in for a
nation's own units.
"""
import importlib
from unittest.mock import MagicMock

# routes/__init__.py does `from .war_routes import war_routes`, which rebinds
# the `routes` package's `war_routes` attribute (the submodule reference
# Python auto-registers) to the Blueprint object of the same name -- so
# `import routes.war_routes as x` would bind x to the Blueprint, not the
# module, and `monkeypatch.setattr(x, "mongo", ...)` would fail with
# "Blueprint has no attribute 'mongo'". importlib.import_module reads
# sys.modules directly by full dotted name instead, sidestepping that
# shadowing. Same issue/fix as tests/test_pop_flee_route.py.
war_routes_module = importlib.import_module("routes.war_routes")
_unit_types_for_nation = war_routes_module._unit_types_for_nation


def _patch_mongo(monkeypatch, test_db):
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    monkeypatch.setattr(war_routes_module, "mongo", fake_mongo)


class TestOwnUnitStrength:
    def test_strength_uses_effective_buffed_stats_not_base(self, test_db, monkeypatch):
        _patch_mongo(monkeypatch, test_db)
        test_db["units"].insert_one({
            "name": "Spearman", "unit_type": "Land",
            "melee": True, "cavalry": False, "ranged": False, "support": False,
        })

        nation = {
            "land_units": {"Spearman": 5},
            "land_unit_details": {
                "Spearman": {
                    "display_name": "Spearman", "base_name": "Spearman", "era": "",
                    "is_support": False, "is_magical": False,
                    "melee": True, "cavalry": False, "ranged": False,
                    "base_stats": {
                        "hp": 3, "morale": 1, "damage": 1, "retaliation_damage": 0,
                        "range": None, "speed": 1, "armor": 0, "attack": 0, "defense": 0,
                    },
                    # Buffed by a nation-level modifier: hp raised 3 -> 5.
                    "effective_stats": {
                        "hp": 5, "morale": 1, "damage": 1, "retaliation_damage": 0,
                        "range": None, "speed": 1, "armor": 0, "attack": 0, "defense": 0,
                    },
                    "stat_breakdown": {"hp": [{"label": "Tech: Siege Craft", "value": 2}]},
                }
            },
        }

        unit_types = _unit_types_for_nation(nation, "nation-1")
        assert len(unit_types) == 1
        ut = unit_types[0]
        # infantry (melee, non-cavalry, non-support), from the BUFFED stats:
        # hp max(0,5-1-2)=2, morale (1-1)*2=0, damage 1*3=3, speed (1-0)*2=2 -> 7
        assert ut["strength"] == 7
        # base stat display columns stay unbuffed, unaffected by strength's use of effective_stats
        assert ut["baseHp"] == 3

    def test_strength_is_zero_when_a_unit_has_no_combat_stats_at_all(self, test_db, monkeypatch):
        _patch_mongo(monkeypatch, test_db)
        test_db["units"].insert_one({
            "name": "Scout", "unit_type": "Land",
            "melee": False, "cavalry": False, "ranged": False, "support": True,
        })
        nation = {
            "land_units": {"Scout": 1},
            "land_unit_details": {
                "Scout": {
                    "display_name": "Scout", "base_name": "Scout", "era": "",
                    "is_support": True, "is_magical": False,
                    "melee": False, "cavalry": False, "ranged": False,
                    "base_stats": {"attack": 0, "defense": 0, "hp": None, "morale": None,
                                   "damage": None, "retaliation_damage": None, "range": None,
                                   "speed": None, "armor": None},
                    "effective_stats": {"attack": 0, "defense": 0, "hp": None, "morale": None,
                                        "damage": None, "retaliation_damage": None, "range": None,
                                        "speed": None, "armor": None},
                    "stat_breakdown": {},
                }
            },
        }
        unit_types = _unit_types_for_nation(nation, "nation-1")
        assert unit_types[0]["strength"] == 0


class TestMercenaryUnitStrengthFallback:
    def test_strength_is_computed_from_the_unit_doc_when_merc_has_no_details(self, test_db, monkeypatch):
        """Mercenaries have no per-nation buff pipeline (merc_details is
        always {} today), so strength falls back to the raw unit doc's own
        stats instead of a buffed effective_stats dict."""
        _patch_mongo(monkeypatch, test_db)
        test_db["units"].insert_one({
            "name": "Hired Blade",
            "melee": True, "cavalry": False, "ranged": False, "support": False,
            "attack": 0, "defense": 0,
            "hp": 3, "has_hp": True,
            "morale": 2, "has_morale": True,
            "damage": 1, "has_damage": True,
            "retaliation_damage": 0, "has_retaliation_damage": True,
            "speed": 1, "has_speed": True,
            "armor": 0, "has_armor": True,
            "maximum_range": None, "has_range": False,
        })
        test_db["mercenaries"].insert_one({
            "patron": "nation-1", "name": "Sellswords",
            "land_units": {"Hired Blade": 2},
            "land_unit_details": {},
        })

        unit_types = _unit_types_for_nation({}, "nation-1")
        assert len(unit_types) == 1
        ut = unit_types[0]
        # infantry: hp max(0,3-1-2)=0, morale (2-1)*2=2, damage 1*3=3, speed (1-0)*2=2 -> 7
        assert ut["strength"] == 7
        assert ut["mercenaryName"] == "Sellswords"
