"""
Regression test for a bug where modifiers on an overlord nation's own
`modifiers` array, scoped to "nation_vassals" (meant to apply to every one
of its vassals), were never applied to any vassal at all.

Root cause: nations.json's external_calculation_requirements.overlord entry
(consulted when calculating a VASSAL's fields, via its "overlord" linked
field) only requested the overlord's "subject_stance" field — never
"modifiers" — so the overlord's modifiers array was never even inspected.

The fix adds "modifiers" to that requirement, but naively doing so would
have introduced a second bug: a nation's own modifiers array can contain
several different nation-targeting scopes at once (nation_self,
nation_vassals, nation_overlord all have target_type "nation"), and the
existing target_type-only filter in collect_external_modifiers_from_object
can't tell them apart — it would have leaked the overlord's nation_self
modifiers (meant only for itself) into every vassal too. The fix adds a
"required_scope" restriction (only present on this requirement) so ONLY
nation_vassals-scoped entries are collected, verified by the second test
below.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import mongomock

import calculations.field_calculations as fc
from app_core import category_data

NATIONS_SCHEMA = category_data["nations"]["schema"]


class TestVassalScopedModifiersReachTheVassal:
    def _setup(self, overlord_modifiers):
        client = mongomock.MongoClient()
        db = client["test"]
        overlord_id = ObjectId()
        vassal_id = ObjectId()
        db["nations"].insert_one({"_id": overlord_id, "name": "Overlord", "modifiers": overlord_modifiers})
        db["nations"].insert_one({"_id": vassal_id, "name": "Vassal", "overlord": str(overlord_id)})
        return db, vassal_id

    def test_nation_vassals_scoped_modifier_is_collected(self):
        db, vassal_id = self._setup([
            {"modifier_type": "compliance_loss_chance", "scope": "nation_vassals", "value": 0.5, "scaling": "flat"},
        ])
        fake_mongo = MagicMock()
        fake_mongo.db = db
        vassal_doc = db["nations"].find_one({"_id": vassal_id})

        with patch.object(fc, "mongo", fake_mongo):
            result = fc.collect_external_requirements(vassal_doc, NATIONS_SCHEMA, "nation")

        assert any(m.get("compliance_loss_chance") == 0.5 for m in result), (
            f"overlord's nation_vassals-scoped modifier was not collected for its vassal: {result}"
        )

    def test_nation_self_scoped_modifier_does_not_leak_to_vassal(self):
        db, vassal_id = self._setup([
            {"modifier_type": "compliance_loss_chance", "scope": "nation_vassals", "value": 0.5, "scaling": "flat"},
            {"modifier_type": "money_income", "scope": "nation_self", "value": 9999, "scaling": "flat"},
        ])
        fake_mongo = MagicMock()
        fake_mongo.db = db
        vassal_doc = db["nations"].find_one({"_id": vassal_id})

        with patch.object(fc, "mongo", fake_mongo):
            result = fc.collect_external_requirements(vassal_doc, NATIONS_SCHEMA, "nation")

        assert not any("money_income" in m for m in result), (
            f"overlord's nation_self-scoped modifier leaked into its vassal's calculation: {result}"
        )
        assert any(m.get("compliance_loss_chance") == 0.5 for m in result)
