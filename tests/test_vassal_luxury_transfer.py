"""
Tests for _apply_vassal_tribute_modifiers's Mercantile vassal handling:
a Mercantile vassal's luxury resource production is gained entirely by its
overlord instead of the vassal itself (a full transfer, unlike Provincial's
50%-lossy research split).

Also covers a bugfix found along the way: the overlord-side vassal query
projected only {pop_count, vassal_type, modifiers}, omitting
resource_production entirely — which silently zeroed out the pre-existing
Provincial research transfer (vassal.get("resource_production", {}) was
always {}). Fixed by adding resource_production to the projection.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

from calculations.field_calculations import _apply_vassal_tribute_modifiers


def _patched_mongo(vassal_docs):
    mock_mongo = MagicMock()
    mock_mongo.db.nations.find.return_value = vassal_docs
    return mock_mongo


class TestMercantileVassalSideRemovesOwnLuxuryProduction:
    def test_luxury_production_removed_from_self(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Mercantile",
            "resource_production": {"gold": 4, "tea": 2},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("gold_production") == -4
        assert overall.get("tea_production") == -2

    def test_zero_production_luxuries_are_not_touched(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Mercantile",
            "resource_production": {"gold": 0},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert "gold_production" not in overall

    def test_non_mercantile_vassal_keeps_its_own_luxury_production(self):
        target = {
            "overlord": "overlord123",
            "pop_count": 50,
            "vassal_type": "Tributary",
            "resource_production": {"gold": 4},
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert "gold_production" not in overall


class TestMercantileOverlordSideGainsVassalLuxuryProduction:
    def test_overlord_gains_full_luxury_amount(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "resource_production": {"gold": 4, "tea": 2},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 4
        assert overall.get("tea_production") == 2

    def test_transfer_is_full_not_half(self):
        """Explicitly distinct from Provincial's 50%-lossy research split —
        Mercantile luxury production moves 1:1."""
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "resource_production": {"gold": 7},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7

    def test_non_mercantile_vassal_grants_no_luxury_transfer(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Tributary", "modifiers": [],
            "resource_production": {"gold": 4},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "gold_production" not in overall

    def test_multiple_mercantile_vassals_stack(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_docs = [
            {"pop_count": 50, "vassal_type": "Mercantile", "modifiers": [], "resource_production": {"gold": 4}},
            {"pop_count": 30, "vassal_type": "Mercantile", "modifiers": [], "resource_production": {"gold": 3}},
        ]
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo(vassal_docs)):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7


class TestProvincialResearchTransferProjectionFix:
    """Regression test for the projection bug: without resource_production in
    the vassal query projection, Provincial's research transfer always saw {}
    and silently transferred 0 regardless of the vassal's actual research."""

    def test_provincial_research_transfer_now_actually_works(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Provincial", "modifiers": [],
            "resource_production": {"research": 10},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("research_production") == 5  # ceil(10 * 0.5)
