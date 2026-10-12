"""
Regression test for the "Mindless" race negative_trait's compliance
mechanic: json-data/schemas/races.json gives it
{"nation_compliance_loss_chance": 1.0, "nation_compliance_gain_chance":
-999, "nation_concessions_chance": -999} — a vassal whose primary race has
this trait gets +100 percentage points added to its compliance_loss_chance
(not doubled), which combined with any nonzero base (Compliant/Loyal)
pushes the total past 1.0 — nation_vassal_compliance_tick's own "while
loss_chance > 1: compliance_lost += 1" loop then treats that as a
guaranteed loss every session, plus a chance at a second level.

This flows through the generic primary_race external_calculation_
requirements pull (collect_external_requirements's "enum field with laws"
branch stripping the "nation_" prefix) — no nation-side special-casing for
this trait exists, or should exist (duplicating it in Python would
double-apply it for a nation that also matches some other special case,
e.g. the unrelated "undead horde" Ravenous+Mindless combo).
"""
from bson import ObjectId
from unittest.mock import patch, MagicMock

import calculations.field_calculations as fc
from app_core import category_data


def _patched_category_data(monkeypatch, db):
    for key in ("races", "nations"):
        monkeypatch.setitem(category_data[key], "database", db[key])


class TestMindlessTraitCompliancePipeline:
    def _nation(self, db, negative_trait, compliance="Loyal"):
        race_id = ObjectId()
        db["races"].insert_one({
            "_id": race_id, "name": "Test Race",
            "positive_trait": "None", "negative_trait": negative_trait,
        })
        overlord_id = db["nations"].insert_one({"name": "Overlordia"}).inserted_id
        return {
            "name": "Testland", "primary_race": str(race_id),
            "overlord": str(overlord_id), "compliance": compliance, "modifiers": [],
        }

    def test_mindless_vassal_gets_plus_one_not_doubled(self, test_db, monkeypatch):
        db = test_db
        _patched_category_data(monkeypatch, db)
        nation = self._nation(db, "Mindless", compliance="Loyal")
        schema = category_data["nations"]["schema"]

        fake_mongo = MagicMock(db=db)
        with patch.object(fc, "mongo", fake_mongo):
            calc = fc.calculate_all_fields(dict(nation), schema, "nation")

        # Loyal's own base (0.15) + Mindless's +1.0 = 1.15, NOT 0.30 (which
        # is what a x2 doubling of 0.15 would give instead).
        assert calc.get("compliance_loss_chance") == 1.15

    def test_non_mindless_vassal_unaffected(self, test_db, monkeypatch):
        db = test_db
        _patched_category_data(monkeypatch, db)
        nation = self._nation(db, "Slow", compliance="Loyal")
        schema = category_data["nations"]["schema"]

        fake_mongo = MagicMock(db=db)
        with patch.object(fc, "mongo", fake_mongo):
            calc = fc.calculate_all_fields(dict(nation), schema, "nation")

        assert calc.get("compliance_loss_chance") == 0.15

    def test_mindless_vassal_compliance_gain_and_concessions_are_zeroed(self, test_db, monkeypatch):
        """The same trait also kills compliance_gain_chance and
        concessions_chance outright (-999 each, floored at 0 by their own
        compute functions) — a mindless vassal can never improve or
        negotiate, only ever backslide."""
        db = test_db
        _patched_category_data(monkeypatch, db)
        nation = self._nation(db, "Mindless", compliance="Loyal")
        schema = category_data["nations"]["schema"]

        fake_mongo = MagicMock(db=db)
        with patch.object(fc, "mongo", fake_mongo):
            calc = fc.calculate_all_fields(dict(nation), schema, "nation")

        assert calc.get("compliance_gain_chance") == 0
        assert calc.get("concessions_chance") == 0
