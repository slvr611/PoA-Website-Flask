"""Tests for the artifact succession system (helpers/tick_helpers.py,
2026-10-25 request): when a nation/merchant/mercenary's ruling character
changes, artifacts still owned by a FORMER ruler of that same org (dead,
retired, or otherwise no longer ruling it) pass to whoever rules it now --
whether the new ruler is player-controlled or an AI-generated successor.

Root problem this fixes: a character's "player" field is wiped on death
(character_death_tick), permanently breaking is_item_owner's "the owning
character's player is you" check for any artifact still attributed to
them, with nothing to hand the artifact off to. generate_ai_character's
own inline predecessor-artifact transfer only ever covered one narrow path
(an immediate AI successor for a fully-AI org with zero players) -- these
tests exercise the general mechanism that covers every other path too.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import helpers.tick_helpers as th


def _patched_mongo(test_db):
    return patch.object(th, "mongo", MagicMock(db=test_db))


class TestRunArtifactSuccession:
    def test_no_current_ruler_does_nothing(self, test_db):
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(str(ObjectId()), "Testland", pending=[])
        assert result == ""

    def test_stamps_former_ruling_nation_orgs_on_first_sight(self, test_db):
        org_id = str(ObjectId())
        ruler_id = ObjectId()
        test_db["characters"].insert_one({
            "_id": ruler_id, "name": "Current Ruler", "health_status": "Healthy",
            "ruling_nation_org": org_id,
        })
        pending = []
        with _patched_mongo(test_db):
            th._run_artifact_succession(org_id, "Testland", pending=pending)

        char_updates = [p for p in pending if p["data_type"] == "characters"]
        assert len(char_updates) == 1
        assert char_updates[0]["after_data"]["former_ruling_nation_orgs"] == [org_id]

    def test_does_not_re_stamp_once_already_recorded(self, test_db):
        org_id = str(ObjectId())
        ruler_id = ObjectId()
        test_db["characters"].insert_one({
            "_id": ruler_id, "name": "Current Ruler", "health_status": "Healthy",
            "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id],
        })
        pending = []
        with _patched_mongo(test_db):
            th._run_artifact_succession(org_id, "Testland", pending=pending)

        assert [p for p in pending if p["data_type"] == "characters"] == []

    def test_transfers_artifacts_from_a_dead_former_ruler(self, test_db):
        org_id = str(ObjectId())
        former_id = ObjectId()
        current_id = ObjectId()
        test_db["characters"].insert_many([
            {"_id": former_id, "name": "Dead King", "health_status": "Dead",
             "ruling_nation_org": None, "former_ruling_nation_orgs": [org_id]},
            {"_id": current_id, "name": "New Ruler", "health_status": "Healthy",
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
        ])
        artifact_id = ObjectId()
        test_db["artifacts"].insert_one({"_id": artifact_id, "name": "Old Crown", "owner": str(former_id)})

        pending = []
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(org_id, "Testland", pending=pending)

        artifact_updates = [p for p in pending if p["data_type"] == "artifacts"]
        assert len(artifact_updates) == 1
        assert artifact_updates[0]["item_id"] == artifact_id
        assert artifact_updates[0]["after_data"]["owner"] == str(current_id)
        assert "Old Crown" in result
        assert "Dead King" in result
        assert "New Ruler" in result
        assert "Testland" in result

    def test_skips_an_actively_ruling_co_ruler(self, test_db):
        """A character recorded in former_ruling_nation_orgs who is STILL
        the org's current ruling_nation_org (e.g. a co-ruler, if the data
        model ever allows one) must never have their own artifacts swept
        away just because another character also currently rules the org."""
        org_id = str(ObjectId())
        co_ruler_id = ObjectId()
        current_id = ObjectId()
        test_db["characters"].insert_many([
            {"_id": co_ruler_id, "name": "Co Ruler", "health_status": "Healthy",
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
            {"_id": current_id, "name": "New Ruler", "health_status": "Healthy",
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
        ])
        test_db["artifacts"].insert_one({"_id": ObjectId(), "name": "Shared Heirloom", "owner": str(co_ruler_id)})

        pending = []
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(org_id, "Testland", pending=pending)

        assert [p for p in pending if p["data_type"] == "artifacts"] == []
        assert result == ""

    def test_archived_artifacts_are_never_swept(self, test_db):
        org_id = str(ObjectId())
        former_id = ObjectId()
        current_id = ObjectId()
        test_db["characters"].insert_many([
            {"_id": former_id, "name": "Dead King", "health_status": "Dead",
             "ruling_nation_org": None, "former_ruling_nation_orgs": [org_id]},
            {"_id": current_id, "name": "New Ruler", "health_status": "Healthy",
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
        ])
        test_db["artifacts"].insert_one({
            "_id": ObjectId(), "name": "Retired Relic", "owner": str(former_id), "archived": True,
        })

        pending = []
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(org_id, "Testland", pending=pending)

        assert [p for p in pending if p["data_type"] == "artifacts"] == []
        assert result == ""

    def test_aggregates_artifacts_from_multiple_former_rulers(self, test_db):
        org_id = str(ObjectId())
        former_a = ObjectId()
        former_b = ObjectId()
        current_id = ObjectId()
        test_db["characters"].insert_many([
            {"_id": former_a, "name": "First Dead Ruler", "health_status": "Dead",
             "ruling_nation_org": None, "former_ruling_nation_orgs": [org_id]},
            {"_id": former_b, "name": "Second Dead Ruler", "health_status": "Dead",
             "ruling_nation_org": None, "former_ruling_nation_orgs": [org_id]},
            {"_id": current_id, "name": "New Ruler", "health_status": "Healthy",
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
        ])
        test_db["artifacts"].insert_many([
            {"_id": ObjectId(), "name": "Relic A", "owner": str(former_a)},
            {"_id": ObjectId(), "name": "Relic B", "owner": str(former_b)},
        ])

        pending = []
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(org_id, "Testland", pending=pending)

        artifact_updates = [p for p in pending if p["data_type"] == "artifacts"]
        assert len(artifact_updates) == 2
        assert {u["after_data"]["owner"] for u in artifact_updates} == {str(current_id)}
        assert "Relic A" in result and "Relic B" in result


class TestAiSuccessorInheritsArtifacts:
    """The explicit scenario from the request: an AI ruler (no player at
    all) dies, a newly-generated AI successor (also player=None) takes
    over -- the artifact must still transfer, with no player-attachment
    required on either side."""

    def test_ai_to_ai_succession_transfers_the_artifact(self, test_db):
        org_id = str(ObjectId())
        dead_ai_ruler = ObjectId()
        new_ai_steward = ObjectId()
        test_db["characters"].insert_many([
            {"_id": dead_ai_ruler, "name": "Old AI King", "health_status": "Dead", "player": None,
             "ruling_nation_org": None, "former_ruling_nation_orgs": [org_id]},
            {"_id": new_ai_steward, "name": "Steward of Testland", "health_status": "Healthy", "player": None,
             "ruling_nation_org": org_id, "former_ruling_nation_orgs": [org_id]},
        ])
        test_db["artifacts"].insert_one({
            "_id": ObjectId(), "name": "Ancestral Blade", "owner": str(dead_ai_ruler),
        })

        pending = []
        with _patched_mongo(test_db):
            result = th._run_artifact_succession(org_id, "Testland", pending=pending)

        artifact_updates = [p for p in pending if p["data_type"] == "artifacts"]
        assert len(artifact_updates) == 1
        assert artifact_updates[0]["after_data"]["owner"] == str(new_ai_steward)
        assert "Ancestral Blade" in result


class TestWrapperFunctionsDelegateCorrectly:
    def test_nation_wrapper(self, test_db):
        org_id = ObjectId()
        ruler_id = ObjectId()
        test_db["characters"].insert_one({
            "_id": ruler_id, "name": "Ruler", "health_status": "Healthy", "ruling_nation_org": str(org_id),
        })
        pending = []
        with _patched_mongo(test_db):
            th.nation_artifact_succession_tick({"_id": org_id, "name": "Nationland"}, {}, {}, pending=pending)
        assert any(p["after_data"].get("former_ruling_nation_orgs") == [str(org_id)] for p in pending)

    def test_merchant_wrapper(self, test_db):
        org_id = ObjectId()
        ruler_id = ObjectId()
        test_db["characters"].insert_one({
            "_id": ruler_id, "name": "Ruler", "health_status": "Healthy", "ruling_nation_org": str(org_id),
        })
        pending = []
        with _patched_mongo(test_db):
            th.merchant_artifact_succession_tick({"_id": org_id, "name": "Merchco"}, {}, {}, pending=pending)
        assert any(p["after_data"].get("former_ruling_nation_orgs") == [str(org_id)] for p in pending)

    def test_mercenary_wrapper(self, test_db):
        org_id = ObjectId()
        ruler_id = ObjectId()
        test_db["characters"].insert_one({
            "_id": ruler_id, "name": "Ruler", "health_status": "Healthy", "ruling_nation_org": str(org_id),
        })
        pending = []
        with _patched_mongo(test_db):
            th.mercenary_artifact_succession_tick({"_id": org_id, "name": "Mercband"}, {}, {}, pending=pending)
        assert any(p["after_data"].get("former_ruling_nation_orgs") == [str(org_id)] for p in pending)


class TestTickRegistration:
    def test_all_three_are_pending_aware(self):
        assert th.nation_artifact_succession_tick in th._PENDING_AWARE_TICK_FUNCTIONS
        assert th.merchant_artifact_succession_tick in th._PENDING_AWARE_TICK_FUNCTIONS
        assert th.mercenary_artifact_succession_tick in th._PENDING_AWARE_TICK_FUNCTIONS

    def test_registered_with_the_right_labels(self):
        assert th.NATION_TICK_FUNCTIONS["Nation Artifact Succession Tick"] is th.nation_artifact_succession_tick
        assert th.MERCHANT_TICK_FUNCTIONS["Merchant Artifact Succession Tick"] is th.merchant_artifact_succession_tick
        assert th.MERCENARY_TICK_FUNCTIONS["Mercenary Artifact Succession Tick"] is th.mercenary_artifact_succession_tick

    def test_nation_succession_runs_after_ai_ensure_leader_so_same_session_resurrection_works(self):
        """AI Ensure Leader Tick must create the new steward BEFORE the
        succession sweep runs, or a nation that was leaderless at the start
        of this exact session wouldn't have anyone to inherit into yet."""
        labels = list(th.NATION_TICK_FUNCTIONS.keys())
        assert labels.index("AI Ensure Leader Tick") < labels.index("Nation Artifact Succession Tick")
