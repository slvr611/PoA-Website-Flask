"""
Regression tests for a batch of N+1 query fixes found while investigating
why saving a nation edit is slow. Live profiling on a real nation
("Khanya") showed calculate_all_fields issuing 255 separate Mongo round
trips, dropped to 122 after these fixes (a single unavoidable full-map hex
tile fetch — see compute_admin_range_out_of_range — accounts for most of
the remaining wall-clock time and is a separate, larger architectural
question, not fixed here):

  1. _nations_share_market (helpers/trade_route_helpers.py) was called once
     PER TRADE ROUTE from both count_route_slots and its duplicate in
     field_calculations.py's breakdown, each call doing 2 nations.find_one +
     2 market_links.find — a nation with several distinct trade partners
     paid for that in full every time. Replaced with _markets_by_nation_name,
     a single batched helper resolving every nation involved in 2 queries
     total regardless of trade partner count.
  2. _nations_in_stasis was called independently by
     compute_money_income/compute_merchant_income/compute_resource_production/
     compute_resource_consumption/get_trade_route_source_contributions — 5
     separate, otherwise-identical nations.find calls for one nation
     calculation. Now cached on the target's own _calc_cache when a `target`
     is passed through.
  3. _current_session() (a session-counter singleton that can only change on
     a tick) is now cached per Flask request instead of re-querying
     global_modifiers on every call.
  4. _get_all_titles() (calculations/field_calculations.py) is now cached
     per Flask request instead of re-querying the titles collection once
     per positive_titles/negative_titles evaluation (once for the nation,
     again per ruling character via TitleAdapter).
  5. nation_is_undead_horde (helpers/undead_horde_helpers.py) is now cached
     per Flask request, keyed by race_id, instead of re-querying races on
     every call (money income, resource production, resource consumption,
     job collection each call it independently for the same nation).
  6. check_unit_requirements's defensive_pact/military_alliance checks
     (calculations/field_calculations.py) resolved each pact partner's name
     via one nations.find_one per partner; replaced with a single batched
     $in query per requirement check.

     Along the way, batching this surfaced a genuine PRE-EXISTING
     correctness bug, unrelated to performance: the two diplo_relations
     queries used asymmetric projections ({"nation_2": 1} for the
     "nation_1 == this nation" query, {"nation_1": 1} for the
     "nation_2 == this nation" query), but the partner-resolution logic
     unconditionally read pact.get("nation_1") first. For every pact found
     via the first query, nation_1 was never fetched, so
     pact.get("nation_1") was always None — which != the nation's own id
     string, so the code picked "nation_1" (None) as the partner instead of
     the correct "nation_2". Concretely: for any nation that was stored as
     `nation_1` on its own defensive_pact/military_alliance relation (about
     half of all such pacts, depending on which side proposed it), any unit
     with a defensive_pact/military_alliance requirement was silently
     unavailable even with the pact in place. Fixed by projecting both
     nation_1 and nation_2 in both queries.
"""
from unittest.mock import patch

from flask import g

import calculations.field_calculations as fc
import helpers.trade_route_helpers as trh
import helpers.undead_horde_helpers as uhh


class TestMarketsByNationNameBatchesInsteadOfPerPartnerLookups:
    def test_resolves_multiple_nations_in_two_queries(self, test_db, monkeypatch):
        test_db["nations"].insert_many([
            {"name": "Alpha", "_id": "a1"},
            {"name": "Beta", "_id": "b1"},
        ])
        # mongomock requires real ObjectIds for _id typically, but market
        # membership only needs the stringified id to round-trip correctly.
        from bson import ObjectId
        a_id, b_id = ObjectId(), ObjectId()
        test_db["nations"].delete_many({})
        test_db["nations"].insert_many([
            {"_id": a_id, "name": "Alpha"},
            {"_id": b_id, "name": "Beta"},
        ])
        test_db["market_links"].insert_many([
            {"member": str(a_id), "market": "m1"},
            {"member": str(b_id), "market": "m1"},
        ])

        find_calls = {"nations": 0, "market_links": 0}
        orig_nations_find = test_db["nations"].find
        orig_ml_find = test_db["market_links"].find

        def _tracked_nations_find(*a, **k):
            find_calls["nations"] += 1
            return orig_nations_find(*a, **k)

        def _tracked_ml_find(*a, **k):
            find_calls["market_links"] += 1
            return orig_ml_find(*a, **k)

        monkeypatch.setattr(test_db["nations"], "find", _tracked_nations_find)
        monkeypatch.setattr(test_db["market_links"], "find", _tracked_ml_find)

        with patch.object(trh, "category_data", {
            "nations": {"database": test_db["nations"]},
            "market_links": {"database": test_db["market_links"]},
        }):
            result = trh._markets_by_nation_name({"Alpha", "Beta", "Nonexistent"})

        assert result["Alpha"] == {"m1"}
        assert result["Beta"] == {"m1"}
        assert "Nonexistent" not in result
        assert find_calls == {"nations": 1, "market_links": 1}

    def test_empty_names_short_circuits(self):
        assert trh._markets_by_nation_name([]) == {}
        assert trh._markets_by_nation_name([None, ""]) == {}


class TestNationsInStasisCaching:
    def test_second_call_with_same_names_uses_cache(self, test_db):
        test_db["nations"].insert_one({"name": "Frozen", "modifiers": [
            {"modifier_type": "stasis", "value": 0, "duration": 3}
        ]})
        calc_cache = {}
        with patch.object(trh, "mongo") as mock_mongo:
            mock_mongo.db = test_db
            first = trh._nations_in_stasis(["Frozen"], calc_cache=calc_cache)
            # Mutate the DB after the first call — a cached second call must
            # NOT see this change, proving it didn't re-query.
            test_db["nations"].update_one({"name": "Frozen"}, {"$set": {"modifiers": []}})
            second = trh._nations_in_stasis(["Frozen"], calc_cache=calc_cache)

        assert first == {"Frozen"}
        assert second == {"Frozen"}, "Second call should reuse the cached result, not re-query"

    def test_no_calc_cache_means_no_caching(self, test_db):
        """Backward-compatible default: passing no calc_cache re-queries
        every time, exactly like before this fix."""
        test_db["nations"].insert_one({"name": "Frozen", "modifiers": [
            {"modifier_type": "stasis", "value": 0, "duration": 3}
        ]})
        with patch.object(trh, "mongo") as mock_mongo:
            mock_mongo.db = test_db
            first = trh._nations_in_stasis(["Frozen"])
            test_db["nations"].update_one({"name": "Frozen"}, {"$set": {"modifiers": []}})
            second = trh._nations_in_stasis(["Frozen"])

        assert first == {"Frozen"}
        assert second == set(), "Without calc_cache, must re-query and see the update"


class TestCurrentSessionCachedPerRequest:
    def test_cached_within_one_request(self, flask_app, monkeypatch):
        calls = []

        class _FakeCollection:
            def find_one(self, *a, **k):
                calls.append(1)
                return {"session_counter": 7}

        class _FakeDb:
            global_modifiers = _FakeCollection()

        monkeypatch.setattr(trh, "mongo", type("M", (), {"db": _FakeDb()})())

        with flask_app.test_request_context("/"):
            first = trh._current_session()
            second = trh._current_session()

        assert first == 7
        assert second == 7
        assert len(calls) == 1, "Expected exactly one global_modifiers.find_one for the whole request"

    def test_not_cached_across_requests(self, flask_app, monkeypatch):
        state = {"session_counter": 1}

        class _FakeCollection:
            def find_one(self, *a, **k):
                return {"session_counter": state["session_counter"]}

        class _FakeDb:
            global_modifiers = _FakeCollection()

        monkeypatch.setattr(trh, "mongo", type("M", (), {"db": _FakeDb()})())

        with flask_app.test_request_context("/"):
            first = trh._current_session()
        state["session_counter"] = 2
        with flask_app.test_request_context("/"):
            second = trh._current_session()

        assert first == 1
        assert second == 2


class TestGetAllTitlesCachedPerRequest:
    def test_cached_within_one_request(self, flask_app, monkeypatch):
        calls = []

        class _FakeCollection:
            def find(self, *a, **k):
                calls.append(1)
                return [{"name": "Test Title", "display_name": "Test Title"}]

        class _FakeDb:
            titles = _FakeCollection()

        monkeypatch.setattr(fc, "mongo", type("M", (), {"db": _FakeDb()})())

        with flask_app.test_request_context("/"):
            first = fc._get_all_titles()
            second = fc._get_all_titles()

        assert first is second
        assert "Test Title" in first
        assert len(calls) == 1

    def test_cached_per_thread_outside_a_flask_request(self, monkeypatch):
        """The session tick never runs inside a Flask request context at
        all — without a thread-local fallback here, every one of the tick's
        218 nations paid for its own titles.find (measured live on
        2026-09-17: 1,554 calls for one AI Decision Tick run)."""
        calls = []

        class _FakeCollection:
            def find(self, *a, **k):
                calls.append(1)
                return [{"name": "Test Title", "display_name": "Test Title"}]

        class _FakeDb:
            titles = _FakeCollection()

        monkeypatch.setattr(fc, "mongo", type("M", (), {"db": _FakeDb()})())

        first = fc._get_all_titles()
        second = fc._get_all_titles()

        assert first is second
        assert len(calls) == 1


class TestNationIsUndeadHordeCachedPerRequest:
    def test_cached_within_one_request_by_race_id(self, flask_app, monkeypatch):
        calls = []

        class _FakeCollection:
            def find_one(self, *a, **k):
                calls.append(1)
                return {"positive_trait": "Ravenous", "negative_trait": "Mindless"}

        class _FakeDb:
            races = _FakeCollection()

        monkeypatch.setattr(uhh, "mongo", type("M", (), {"db": _FakeDb()})())

        with flask_app.test_request_context("/"):
            nation = {"primary_race": "67ef41c689e17a6e9fa6bbc5"}
            first = uhh.nation_is_undead_horde(nation)
            second = uhh.nation_is_undead_horde(nation)

        assert first is True
        assert second is True
        assert len(calls) == 1

    def test_no_primary_race_short_circuits_without_query(self, flask_app, monkeypatch):
        calls = []

        class _FakeCollection:
            def find_one(self, *a, **k):
                calls.append(1)
                return None

        class _FakeDb:
            races = _FakeCollection()

        monkeypatch.setattr(uhh, "mongo", type("M", (), {"db": _FakeDb()})())

        with flask_app.test_request_context("/"):
            assert uhh.nation_is_undead_horde({}) is False
            assert uhh.nation_is_undead_horde(None) is False

        assert calls == []

    def test_cached_per_thread_outside_a_flask_request(self, monkeypatch):
        """Same as test_cached_within_one_request_by_race_id, but exercising
        the session tick's actual runtime context (no Flask request at
        all) — a per-nation call site (compute_money_income,
        compute_resource_production, compute_resource_consumption,
        collect_undead_horde_job) must not re-query per call once cached."""
        calls = []

        class _FakeCollection:
            def find_one(self, *a, **k):
                calls.append(1)
                return {"positive_trait": "Ravenous", "negative_trait": "Mindless"}

        class _FakeDb:
            races = _FakeCollection()

        monkeypatch.setattr(uhh, "mongo", type("M", (), {"db": _FakeDb()})())

        nation = {"primary_race": "67ef41c689e17a6e9fa6bbc5"}
        first = uhh.nation_is_undead_horde(nation)
        second = uhh.nation_is_undead_horde(nation)

        assert first is True
        assert second is True
        assert len(calls) == 1


class TestCheckUnitRequirementsPactBatching:
    def test_defensive_pact_requirement_uses_single_batched_query(self, test_db, monkeypatch):
        from bson import ObjectId
        target_id = ObjectId()
        partner_id = ObjectId()
        test_db["nations"].insert_one({"_id": partner_id, "name": "Allied Nation"})
        test_db["diplo_relations"].insert_one({
            "nation_1": str(target_id), "nation_2": str(partner_id), "pact_type": "Defensive Pact",
        })

        find_calls = []
        orig_find = test_db["nations"].find

        def _tracked(*a, **k):
            find_calls.append(a)
            return orig_find(*a, **k)

        monkeypatch.setattr(test_db["nations"], "find", _tracked)

        with patch.object(fc, "mongo", type("M", (), {"db": test_db})()):
            target = {"_id": target_id, "name": "Test Nation"}
            requirements = {"defensive_pact": ["Allied Nation"]}
            meets = fc.check_unit_requirements(target, {"requirements": requirements})

        assert meets is True
        assert len(find_calls) == 1, "Expected exactly one batched nations.find for pact partners"

    def test_defensive_pact_requirement_met_when_target_is_nation_2(self, test_db):
        """Regression for a pre-existing bug found while batching this:
        the two diplo_relations queries used asymmetric projections, but
        partner resolution unconditionally read nation_1 first — for any
        pact where this nation was stored as nation_1 (the other direction
        from this test), nation_1 was never fetched, so it was always None,
        and the code picked that None instead of the real partner
        (nation_2). Both directions must resolve correctly now."""
        from bson import ObjectId
        target_id = ObjectId()
        partner_id = ObjectId()
        test_db["nations"].insert_one({"_id": partner_id, "name": "Allied Nation"})
        # This nation is nation_2 here — the direction that already worked
        # even under the old bug. Kept for symmetry/coverage.
        test_db["diplo_relations"].insert_one({
            "nation_1": str(partner_id), "nation_2": str(target_id), "pact_type": "Defensive Pact",
        })

        with patch.object(fc, "mongo", type("M", (), {"db": test_db})()):
            target = {"_id": target_id, "name": "Test Nation"}
            requirements = {"defensive_pact": ["Allied Nation"]}
            meets = fc.check_unit_requirements(target, {"requirements": requirements})

        assert meets is True

    def test_military_alliance_requirement_not_met_when_partner_missing(self, test_db, monkeypatch):
        from bson import ObjectId
        target_id = ObjectId()

        with patch.object(fc, "mongo", type("M", (), {"db": test_db})()):
            target = {"_id": target_id, "name": "Test Nation"}
            requirements = {"military_alliance": ["Nonexistent Ally"]}
            meets = fc.check_unit_requirements(target, {"requirements": requirements})

        assert meets is False
