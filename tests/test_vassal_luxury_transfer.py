"""
Tests for _apply_vassal_tribute_modifiers's Mercantile vassal handling:
a Mercantile vassal's luxury resource production is gained entirely by its
overlord instead of the vassal itself (a full transfer, unlike Provincial's
own research mechanic — see TestProvincialResearchTransferIsFlatOneNotHalfOfProduction
below: the vassal loses 100% of its own research, but the overlord's gain
is a flat +1 per Provincial vassal, granted by nations.json's own
"Provincial" law modifier rather than anything in this function).

Rewritten 2026-10-11 for a real, confirmed bug found live ("Flaerk
Collective" never reliably receiving furs from its Mercantile vassal
"Workshop Caretakers"): the subtraction amount used to be read from
target["resource_production"] — the value STORED from the vassal's
PREVIOUS calculation pass, not the value being computed right now. Since
the overlord's gain, in turn, read the vassal's CURRENTLY STORED
resource_production (already net of its own transfer-out once that field
updated), and nations recalculate in a fixed per-tick order, this produced
a permanent one-session oscillation that never settles — the vassal's own
stored luxury production flipped between its full node value and 0 every
tick, and the overlord's gain flipped roughly out of phase with it.

Fixed by computing the vassal's GROSS (pre-transfer) production fresh,
every pass, via the real compute_resource_production function (reused, not
duplicated — avoids drifting out of sync with it) and persisting that
snapshot on target["mercantile_luxury_production"] /
target["provincial_research_production"]. The overlord now reads THAT
persisted snapshot (via a corresponding DB projection change) instead of
the already-net resource_production, so both sides agree within the same
pass regardless of which nation calculates first.

These tests drive the real compute_resource_production path by seeding
overall_total_modifiers with the modifier keys a district/node would
actually produce (e.g. "gold_production": 4), rather than pre-setting
resource_production directly — that field is no longer read by this
function at all. compute_resource_production also unconditionally computes
every general resource (including "research", which queries
category_data["pops"]["database"] for a religious-homogeneity check), so
any vassal-side test (where _apply_vassal_tribute_modifiers reaches that
code path) needs that patched to an empty collection — the overlord-side
tests never call compute_resource_production at all (no "overlord" key on
the target passed in), so they don't need it.

Also covers a bugfix found along the way: the overlord-side vassal query
projected only {pop_count, vassal_type, modifiers}, omitting
resource_production entirely — which silently zeroed out Mercantile's
luxury transfer too (vassal.get("resource_production", {}) was always
{}). Fixed by adding resource_production to the projection. (Originally
found via Provincial's research transfer, which at the time still lived
in this function — see TestProvincialResearchTransferIsFlatOneNotHalfOfProduction
below for why that overlord-side logic was later removed entirely.)
"""
import mongomock
from unittest.mock import patch, MagicMock
from bson import ObjectId

from app_core import category_data
from calculations.field_calculations import _apply_vassal_tribute_modifiers


def _patched_mongo(vassal_docs):
    mock_mongo = MagicMock()
    mock_mongo.db.nations.find.return_value = vassal_docs
    return mock_mongo


def _patched_empty_pops(monkeypatch):
    """compute_resource_production's "research" special case unconditionally
    queries category_data["pops"]["database"] — needed by any vassal-side
    call (overlord_id set on the target), regardless of which luxury/
    research resource the test actually cares about."""
    client = mongomock.MongoClient()
    monkeypatch.setitem(category_data["pops"], "database", client["test"]["pops"])


class TestMercantileVassalSideRemovesOwnLuxuryProduction:
    def test_luxury_production_removed_from_self(self, monkeypatch):
        _patched_empty_pops(monkeypatch)
        target = {"overlord": "overlord123", "pop_count": 50, "vassal_type": "Mercantile"}
        overall = {"gold_production": 4, "furs_production": 2}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("gold_production") == 0
        assert overall.get("furs_production") == 0
        assert target["mercantile_luxury_production"] == {"gold": 4, "furs": 2}

    def test_zero_production_luxuries_are_not_touched(self, monkeypatch):
        _patched_empty_pops(monkeypatch)
        target = {"overlord": "overlord123", "pop_count": 50, "vassal_type": "Mercantile"}
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert "gold_production" not in overall
        assert target["mercantile_luxury_production"] == {}

    def test_non_mercantile_vassal_keeps_its_own_luxury_production(self, monkeypatch):
        _patched_empty_pops(monkeypatch)
        target = {"overlord": "overlord123", "pop_count": 50, "vassal_type": "Tributary"}
        overall = {"gold_production": 4}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("gold_production") == 4  # untouched — no transfer
        assert target["mercantile_luxury_production"] == {}

    def test_snapshot_is_reset_for_a_nation_that_is_no_longer_mercantile(self, monkeypatch):
        """A nation that used to be Mercantile (or Provincial) must not keep
        a stale gross-production snapshot from a previous session once it
        stops being one."""
        _patched_empty_pops(monkeypatch)
        target = {
            "overlord": "overlord123", "pop_count": 50, "vassal_type": "Tributary",
            "mercantile_luxury_production": {"gold": 99}, "provincial_research_production": 50,
        }
        overall = {}
        _apply_vassal_tribute_modifiers(target, overall)
        assert target["mercantile_luxury_production"] == {}
        assert target["provincial_research_production"] == 0

    def test_computed_fresh_from_overall_total_modifiers_not_stale_target_field(self, monkeypatch):
        """The actual regression: target["resource_production"] (a stale,
        previous-session value) must have zero influence — only the fresh
        overall_total_modifiers this pass is computing from matters."""
        _patched_empty_pops(monkeypatch)
        target = {
            "overlord": "overlord123", "pop_count": 50, "vassal_type": "Mercantile",
            "resource_production": {"gold": 999},  # stale — must be ignored
        }
        overall = {"gold_production": 4}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("gold_production") == 0
        assert target["mercantile_luxury_production"] == {"gold": 4}

    def test_node_based_production_is_also_transferred(self, monkeypatch):
        """Luxury production from an active resource node (field_template
        "{resource}_nodes", not just a direct "_production" modifier) must
        be captured by the gross snapshot too — compute_resource_production
        is the single source of truth for what "gross" means.

        The node count itself isn't touched (there's no "_nodes" to zero
        out) — instead a canceling "-1" lands on "_production", so the net
        result (node_production + direct_production, computed later by
        compute_resource_production's own pass over the same
        overall_total_modifiers) still correctly nets to 0."""
        _patched_empty_pops(monkeypatch)
        target = {"overlord": "overlord123", "pop_count": 50, "vassal_type": "Mercantile"}
        overall = {"furs_nodes": 1}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("furs_nodes") == 1
        assert overall.get("furs_production") == -1
        net = overall.get("furs_nodes", 0) + overall.get("furs_production", 0)
        assert net == 0
        assert target["mercantile_luxury_production"] == {"furs": 1}


class TestMercantileOverlordSideGainsVassalLuxuryProduction:
    """These never reach compute_resource_production (the target passed in
    has no "overlord" key), so they don't need pops mocking."""

    def test_overlord_gains_full_luxury_amount(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "mercantile_luxury_production": {"gold": 4, "furs": 2},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 4
        assert overall.get("furs_production") == 2

    def test_transfer_is_full_not_half(self):
        """Explicitly distinct from Provincial's flat-+1 research grant —
        Mercantile luxury production moves 1:1."""
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Mercantile", "modifiers": [],
            "mercantile_luxury_production": {"gold": 7},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7

    def test_non_mercantile_vassal_grants_no_luxury_transfer(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Tributary", "modifiers": [],
            "mercantile_luxury_production": {"gold": 4},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "gold_production" not in overall

    def test_multiple_mercantile_vassals_stack(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_docs = [
            {"pop_count": 50, "vassal_type": "Mercantile", "modifiers": [], "mercantile_luxury_production": {"gold": 4}},
            {"pop_count": 30, "vassal_type": "Mercantile", "modifiers": [], "mercantile_luxury_production": {"gold": 3}},
        ]
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo(vassal_docs)):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert overall.get("gold_production") == 7

    def test_missing_snapshot_field_is_treated_as_empty_not_an_error(self):
        """A vassal whose document predates this fix (or was never
        recalculated since) won't have mercantile_luxury_production at
        all — must not raise, just contribute nothing."""
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {"pop_count": 50, "vassal_type": "Mercantile", "modifiers": []}
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "gold_production" not in overall


class TestProvincialResearchTransferIsFlatOneNotHalfOfProduction:
    """Provincial's overlord-side research gain used to be a hardcoded
    ceil(vassal_research * 0.5) added on top of json-data/schemas/nations.json's
    own "Provincial": {"overlord_nation_research_production": 1} law modifier
    — which was ALREADY correctly delivering a flat +1 per Provincial vassal
    via the standard external_calculation_requirements/modifier_prefix
    mechanism (nations.json's "vassals": {"fields": ["vassal_type"],
    "modifier_prefix": "overlord"}). So an overlord was silently double-
    dipping: +1 flat (schema law) plus ceil(vassal_research * 0.5) (this
    hardcoded Python addition) for every Provincial vassal.

    Fixed by removing the hardcoded addition entirely — _apply_vassal_
    tribute_modifiers (and its tooltip-breakdown twin in
    _build_computed_contributions) must no longer touch research_production
    for a Provincial vassal at all; the flat +1 the schema already grants is
    the sole, correct transfer amount. The vassal's OWN side is unaffected
    by this change — a Provincial vassal still loses 100% of its own
    research production (see the "Vassal side" test below), which is a
    separate, deliberate mechanic from what the overlord gains.
    """

    def test_overlord_side_no_longer_adds_a_hardcoded_research_bonus(self):
        overlord_target = {"_id": ObjectId(), "pop_count": 100}
        vassal_doc = {
            "pop_count": 50, "vassal_type": "Provincial", "modifiers": [],
            "mercantile_luxury_production": {},
        }
        overall = {}
        with patch("calculations.field_calculations.mongo", _patched_mongo([vassal_doc])):
            _apply_vassal_tribute_modifiers(overlord_target, overall)
        assert "research_production" not in overall

    def test_vassal_side_still_loses_all_of_its_own_research(self, monkeypatch):
        """Unaffected by this fix — a Provincial vassal still can't keep any
        of its own research (nations.json's "nation_max_research_production":
        0 for the Provincial law caps it, and this is the modifier-side
        mirror of that: the vassal's own production is fully zeroed out)."""
        _patched_empty_pops(monkeypatch)
        target = {"overlord": "overlord123", "pop_count": 50, "vassal_type": "Provincial"}
        overall = {"research_production": 10}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("research_production") == 0
        assert target["provincial_research_production"] == 10

    def test_computed_fresh_not_from_stale_target_field(self, monkeypatch):
        _patched_empty_pops(monkeypatch)
        target = {
            "overlord": "overlord123", "pop_count": 50, "vassal_type": "Provincial",
            "resource_production": {"research": 999},  # stale — must be ignored
        }
        overall = {"research_production": 10}
        _apply_vassal_tribute_modifiers(target, overall)
        assert overall.get("research_production") == 0
        assert target["provincial_research_production"] == 10

    def test_overlord_gets_exactly_one_flat_point_per_provincial_vassal_end_to_end(self):
        """End-to-end guard against the double-count regression: with the
        hardcoded half-of-research addition gone, collect_external_requirements
        (the schema-driven "vassals"/modifier_prefix:"overlord" pathway) must
        still deliver exactly +1 research_production per Provincial vassal —
        regardless of how much research that vassal actually produces — and
        nothing more."""
        from calculations.field_calculations import collect_external_requirements
        from app_core import category_data as _category_data

        client = mongomock.MongoClient()
        db = client["test"]
        overlord_id = ObjectId()
        db["nations"].insert_one({
            "_id": overlord_id, "name": "Overlord",
            "vassal_type": "Provincial", "resource_production": {"research": 500},
        })
        db["nations"].insert_one({
            "_id": ObjectId(), "name": "Vassal One", "overlord": str(overlord_id),
            "vassal_type": "Provincial", "resource_production": {"research": 3},
        })
        db["nations"].insert_one({
            "_id": ObjectId(), "name": "Vassal Two", "overlord": str(overlord_id),
            "vassal_type": "Provincial", "resource_production": {"research": 47},
        })

        overlord = db["nations"].find_one({"_id": overlord_id})
        schema = _category_data["nations"]["schema"]
        fake_mongo = type("FakeMongo", (), {"db": db})()
        with patch("calculations.field_calculations.mongo", fake_mongo):
            result = collect_external_requirements(overlord, schema, "nation")
        total = {}
        for entry in result:
            for k, v in entry.items():
                total[k] = total.get(k, 0) + v

        assert total.get("research_production") == 2  # 1 per vassal, regardless of their output
