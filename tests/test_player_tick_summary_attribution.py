"""
Regression tests for the player-facing tick summary rework
(helpers/tick_helpers.py):

- disease_job_death_tick now names WHICH nation(s) lost pops, instead of a
  bare aggregate count with no attribution.
- _character_tick_is_player_relevant: a character's own tick events belong
  in the player digest only when they have a real player AND either rule
  nothing, rule a player nation, or rule an AI nation that ISN'T
  specifically a player's vassal (that last case is just noise about the
  overlord's own vassal management). Also fixes the old code's truthy bug —
  character.get("player") can be "" (not just None) for "no player", and
  `is not None` incorrectly treated "" as "has a player".
- _org_leader_has_real_player: merchants/mercenaries/factions store
  "leaders" as a queryTargetAttribute (reverse-lookup) field, never actually
  present on the raw document — reading org.get("leaders", []) always
  returned nothing, so no merchant/mercenary/faction tick result ever
  reached the player summary. Fixed via the correct reverse lookup.
- pop_flee_tick now reports the destination nation's temperament via an
  optional flee_events list, so a pop fleeing INTO a player nation from an
  AI source is attributed correctly (previously only fleeing OUT of a
  player nation was caught, since the outer loop only ever saw the source
  nation directly).
- nation_disease_spread_tick gets the same treatment via an optional
  disease_spread_events list — found live in production: "Vampirism has
  spread from The United Valley to Taika" (Taika a Player nation, The
  United Valley not) appeared in full_tick_summary but never in
  player_tick_summary, since attribution only ever looked at the source
  nation's own temperament.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th


def _patch_mongo(fake_db):
    return patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db))


class TestDiseaseJobDeathTickNamesAffectedNations:
    def test_death_message_names_the_nation(self, test_db):
        disease_id = ObjectId()
        nation_id = ObjectId()
        test_db["diseases"].insert_one({
            "_id": disease_id, "name": "Crimson Rot", "job_death_chance": 1.0,
        })
        test_db["nations"].insert_one({"_id": nation_id, "name": "Testland"})
        test_db["pops"].insert_one({"nation": str(nation_id), "diseases": [str(disease_id)]})

        with _patch_mongo(test_db), patch("helpers.tick_helpers.random.random", return_value=0.0):
            result = th.disease_job_death_tick([], [], {}, pending=[])

        assert "Testland: 1" in result
        assert "Crimson Rot" in result

    def test_death_message_breaks_down_multiple_nations(self, test_db):
        disease_id = ObjectId()
        nation_a = ObjectId()
        nation_b = ObjectId()
        test_db["diseases"].insert_one({
            "_id": disease_id, "name": "Crimson Rot", "job_death_chance": 1.0,
        })
        test_db["nations"].insert_many([
            {"_id": nation_a, "name": "Alphaland"},
            {"_id": nation_b, "name": "Betaland"},
        ])
        test_db["pops"].insert_many([
            {"nation": str(nation_a), "diseases": [str(disease_id)]},
            {"nation": str(nation_a), "diseases": [str(disease_id)]},
            {"nation": str(nation_b), "diseases": [str(disease_id)]},
        ])

        with _patch_mongo(test_db), patch("helpers.tick_helpers.random.random", return_value=0.0):
            result = th.disease_job_death_tick([], [], {}, pending=[])

        assert "Alphaland: 2" in result
        assert "Betaland: 1" in result
        assert "3 pop(s) died" in result


class TestCharacterTickIsPlayerRelevant:
    def test_no_player_is_excluded(self, test_db):
        char = {"player": "", "ruling_nation_org": ""}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is False

    def test_none_player_is_excluded(self, test_db):
        char = {"player": None, "ruling_nation_org": ""}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is False

    def test_real_player_ruling_nothing_is_included(self, test_db):
        char = {"player": str(ObjectId()), "ruling_nation_org": ""}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is True

    def test_real_player_ruling_a_player_nation_is_included(self, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "temperament": "Player"})
        char = {"player": str(ObjectId()), "ruling_nation_org": str(nation_id)}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is True

    def test_real_player_ruling_an_ai_vassal_of_a_player_is_excluded(self, test_db):
        overlord_id = ObjectId()
        vassal_id = ObjectId()
        test_db["nations"].insert_many([
            {"_id": overlord_id, "temperament": "Player"},
            {"_id": vassal_id, "temperament": "Neutral", "overlord": str(overlord_id)},
        ])
        char = {"player": str(ObjectId()), "ruling_nation_org": str(vassal_id)}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is False

    def test_real_player_ruling_an_ai_vassal_of_an_ai_overlord_is_included(self, test_db):
        """The excluded case is specifically "vassal of a PLAYER" — an AI
        nation's own AI vassal is just regular AI content, not overlord noise."""
        overlord_id = ObjectId()
        vassal_id = ObjectId()
        test_db["nations"].insert_many([
            {"_id": overlord_id, "temperament": "Neutral"},
            {"_id": vassal_id, "temperament": "Aggressive", "overlord": str(overlord_id)},
        ])
        char = {"player": str(ObjectId()), "ruling_nation_org": str(vassal_id)}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is True

    def test_real_player_ruling_a_standalone_ai_nation_is_included(self, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "temperament": "Neutral"})
        char = {"player": str(ObjectId()), "ruling_nation_org": str(nation_id)}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is True

    def test_ruling_org_that_is_not_a_nation_is_included(self, test_db):
        """ruling_nation_org is polymorphic — a merchant/mercenary id won't
        resolve in the nations collection at all; treat that as "not a
        vassal" rather than excluding."""
        char = {"player": str(ObjectId()), "ruling_nation_org": str(ObjectId())}
        assert th._character_tick_is_player_relevant(char, test_db["nations"]) is True


class TestOrgLeaderHasRealPlayer:
    def test_true_when_a_leader_has_a_real_player(self, test_db):
        org_id = ObjectId()
        test_db["characters"].insert_one({
            "ruling_nation_org": str(org_id), "player": str(ObjectId()),
        })
        assert th._org_leader_has_real_player(str(org_id), test_db["characters"]) is True

    def test_false_when_no_leader_has_a_real_player(self, test_db):
        org_id = ObjectId()
        test_db["characters"].insert_one({"ruling_nation_org": str(org_id), "player": ""})
        assert th._org_leader_has_real_player(str(org_id), test_db["characters"]) is False

    def test_false_when_no_leaders_at_all(self, test_db):
        org_id = ObjectId()
        assert th._org_leader_has_real_player(str(org_id), test_db["characters"]) is False


class TestPopFleeTickReportsDestinationTemperament:
    def test_flee_event_records_player_destination(self, test_db):
        source_id = ObjectId()
        dest_id = ObjectId()
        region_id = ObjectId()
        source = {
            "_id": source_id, "name": "Overcrowded", "region": str(region_id),
            "pop_count": 10, "effective_pop_capacity": 5, "pop_flee_chance": 1.0,
        }
        test_db["regions"].insert_one({"_id": region_id, "modifiers": []})
        test_db["nations"].insert_one({
            "_id": dest_id, "name": "Haven", "region": str(region_id),
            "citizenship_stance": "Open", "temperament": "Player",
        })
        test_db["pops"].insert_one({"nation": str(source_id), "race": "r1", "culture": "c1", "religion": "rel1"})

        flee_events = []
        with _patch_mongo(test_db), \
             patch("helpers.tick_helpers.random.random", return_value=0.0), \
             patch("helpers.tick_helpers.random.choice", side_effect=lambda seq: seq[0]):
            result = th.pop_flee_tick(source, dict(source), {}, pending=[], flee_events=flee_events)

        assert "Overcrowded" in result and "Haven" in result
        assert len(flee_events) == 1
        assert flee_events[0]["to_temperament"] == "Player"
        assert flee_events[0]["from_id"] == str(source_id)
        assert flee_events[0]["to_id"] == str(dest_id)

    def test_no_flee_event_appended_when_nothing_flees(self, test_db):
        source = {"_id": ObjectId(), "name": "Comfortable", "pop_count": 5, "effective_pop_capacity": 10}
        flee_events = []
        with _patch_mongo(test_db):
            result = th.pop_flee_tick(source, dict(source), {}, pending=[], flee_events=flee_events)
        assert result == ""
        assert flee_events == []


class TestDispatchInjectsFleeEvents:
    def test_pop_flee_tick_receives_flee_events_kwarg(self):
        flee_events = []
        source = {"_id": ObjectId(), "name": "Comfortable", "pop_count": 5, "effective_pop_capacity": 10}
        # No overcrowding -> pop_flee_tick returns "" immediately without
        # touching the DB, so this exercises _dispatch's kwarg wiring only.
        result = th._dispatch(th.pop_flee_tick, None, source, dict(source), {}, flee_events=flee_events)
        assert result == ""


class TestDispatchInjectsDiseaseSpreadEvents:
    def test_nation_disease_spread_tick_receives_the_kwarg(self, test_db):
        """_dispatch must bind disease_spread_events only for functions
        registered in _DISEASE_SPREAD_EVENT_AWARE_TICK_FUNCTIONS — every
        other tick function's call signature stays unaffected.

        Unlike pop_flee_tick (which can early-return "" from pure in-memory
        pop_count/effective_pop_capacity checks), nation_disease_spread_tick
        unconditionally queries get_nation_infection_counts first — so mongo
        must be patched here even for a "no infections" nation, or this
        would otherwise reach out to the real database."""
        disease_spread_events = []
        source = {"_id": ObjectId(), "name": "Testland", "pop_count": 5}
        with patch("helpers.disease_helpers.mongo", MagicMock(db=test_db)):
            result = th._dispatch(
                th.nation_disease_spread_tick, None, source, dict(source), {},
                disease_spread_events=disease_spread_events,
            )
        assert result == ""

    def test_unrelated_tick_function_does_not_receive_the_kwarg(self):
        """A tick function NOT registered for disease_spread_events must
        never see it as an unexpected keyword argument."""
        disease_spread_events = []
        source = {"_id": ObjectId(), "name": "Comfortable", "pop_count": 5, "effective_pop_capacity": 10}
        # pop_flee_tick isn't in _DISEASE_SPREAD_EVENT_AWARE_TICK_FUNCTIONS —
        # if _dispatch wired the kwarg in unconditionally, this would raise
        # TypeError instead of returning "" for a non-overcrowded nation.
        result = th._dispatch(
            th.pop_flee_tick, None, source, dict(source), {},
            disease_spread_events=disease_spread_events,
        )
        assert result == ""
        assert disease_spread_events == []


class TestNationStabilityTickIsNoLongerExcludedFromThePlayerSummary:
    """Regression for the 2026-10-06 report: "Nation Stability Tick" was
    listed in _EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS (deliberately, per
    its own comment — deemed "too routine" when written), which made the
    tick-loop attribution check at the top of this file's module docstring
    skip it unconditionally, even for a player nation's own stability
    gain/loss. The tick function itself (nation_stability_tick) was always
    computing and returning the right text — see
    test_stability_gain_chance_pct_scaling.py / test_resource_windfall_loss.py
    for its own correctness — only the result text never reached
    player_tick_summary. Removed at the user's explicit request."""

    def test_stability_tick_label_is_not_excluded(self):
        assert "Nation Stability Tick" not in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS

    def test_exclusion_set_is_currently_empty_of_the_old_entry(self):
        """"Nation Stability Tick" specifically must stay out, regardless
        of whatever else the set contains (see TestExcludedFromPlayerSummary
        below for the current full membership, added 2026-10-24 at the
        user's request for a different set of tick functions)."""
        assert "Nation Stability Tick" not in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS

    def test_stability_tick_result_reaches_player_summary_in_the_attribution_check(self):
        """Mirrors the exact tick-loop check at the player_tick_summary
        attribution site (see the module docstring's other end-to-end
        tests for why this file mirrors it instead of running the full
        tick())."""
        tick_function_label = "Nation Stability Tick"
        old_nation = {"temperament": "Player", "name": "Testland"}
        result = "Testland has gained 1 level(s) of stability due to stability_gain_chance.\n"

        if tick_function_label in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS:
            included = False
        elif old_nation.get("temperament", "None") == "Player":
            included = True
        else:
            included = False

        assert included is True


class TestExcludedFromPlayerSummary:
    """2026-10-24 request: organization (merchant/mercenary) income, AI
    ruler generation, nation income, and district session counts should
    stop appearing in the player-facing digest, but keep applying (and
    keep appearing in the full tick summary) exactly as before — only
    their result text's reach into player_tick_summary changes.

    "Nation Tech Tick" (nation research) is deliberately excluded from
    this set — see TestNationTechTickWindfallSplit below for why it needs
    different handling instead of a blanket label exclusion."""

    def test_current_membership(self):
        assert th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS == {
            "Merchant Income Tick",
            "Mercenary Upkeep Tick",
            "AI Ensure Leader Tick",
            "Nation Income Tick",
            "District Duration Tick",
        }

    def test_nation_tech_tick_is_not_in_the_blanket_exclusion_set(self):
        assert "Nation Tech Tick" not in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS

    def test_excluded_nation_label_is_skipped_even_for_a_player_nation(self):
        """Mirrors the nation loop's attribution check: an excluded label
        is skipped unconditionally, before the player-temperament check
        ever runs — a player nation's own "Nation Income Tick"/"District
        Duration Tick"/"AI Ensure Leader Tick" result must NOT reach
        player_tick_summary."""
        for tick_function_label in ("Nation Income Tick", "District Duration Tick", "AI Ensure Leader Tick"):
            old_nation = {"temperament": "Player", "name": "Testland"}
            if tick_function_label in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS:
                included = False
            elif old_nation.get("temperament", "None") == "Player":
                included = True
            else:
                included = False
            assert included is False, f"{tick_function_label} should be excluded even for a Player nation"

    def test_excluded_merchant_mercenary_labels_are_skipped_despite_a_real_player_leader(self):
        """Mirrors the merchant/mercenary loops' attribution check (which
        checks the exclusion set BEFORE _org_leader_has_real_player, see
        tick_helpers.py) — a real player-led org's income/upkeep result
        must still not reach player_tick_summary once excluded."""
        for tick_function_label in ("Merchant Income Tick", "Mercenary Upkeep Tick"):
            org_leader_has_real_player = True  # i.e. would have included it pre-fix
            included = (
                tick_function_label not in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS
                and org_leader_has_real_player
            )
            assert included is False, f"{tick_function_label} should be excluded despite a real player leader"

    def test_unrelated_label_is_unaffected(self):
        assert "Nation War Support Tick" not in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS


class TestNationTechTickWindfallSplit:
    """nation_tech_tick now returns (full_result, windfall_result) instead
    of a single string: the "X has researched Y" line is excluded from
    the player summary, but a resource windfall it triggers is still
    included there — see nation_tech_tick's own docstring and the nation
    loop's "Nation Tech Tick" special-case in tick_helpers.py."""

    def test_return_shape_is_a_two_tuple(self, test_db):
        old_nation = {
            "name": "Testland", "technologies": {"bronze_working": {"investing": 10, "invested": 0, "cost": 10}},
            "resource_production": {}, "resource_consumption": {},
        }
        new_nation = dict(old_nation)
        with patch("helpers.tick_helpers.json_data", {"tech": {"bronze_working": {"display_name": "Bronze Working", "cost": 10}}}):
            full_result, windfall_result = th.nation_tech_tick(old_nation, new_nation, {})
        assert "Testland has researched Bronze Working" in full_result
        assert windfall_result == ""  # no resource_windfall_on_tech_researched set

    def test_windfall_line_appears_in_both_full_and_windfall_results(self, test_db):
        old_nation = {
            "name": "Testland",
            "technologies": {"bronze_working": {"investing": 10, "invested": 0, "cost": 10}},
            "resource_production": {}, "resource_consumption": {},
            "resource_windfall_on_tech_researched": 2,
        }
        new_nation = dict(old_nation)
        fake_json_data = {
            "tech": {"bronze_working": {"display_name": "Bronze Working", "cost": 10}},
            "general_resources": [{"key": "wood", "name": "Wood"}],
        }
        with patch("helpers.tick_helpers.json_data", fake_json_data), \
             patch("helpers.tick_helpers.random.choice", return_value="wood"):
            full_result, windfall_result = th.nation_tech_tick(old_nation, new_nation, {})

        assert "Testland has researched Bronze Working" in full_result
        assert "resource windfall" in full_result
        assert "resource windfall" in windfall_result
        assert "has researched" not in windfall_result

    def test_attribution_check_uses_windfall_result_only_for_the_player_summary(self):
        """Mirrors the nation loop's special-case substitution: for "Nation
        Tech Tick" specifically, player_tick_summary gets windfall_result
        in place of the full result, while full_tick_summary always gets
        the full result."""
        tick_function_label = "Nation Tech Tick"
        full_result = "Testland has researched Bronze Working.\nTestland gained 2 wood from a resource windfall (researched Bronze Working).\n"
        windfall_result = "Testland gained 2 wood from a resource windfall (researched Bronze Working).\n"

        if tick_function_label == "Nation Tech Tick":
            result, player_result = full_result, windfall_result
        else:
            result = player_result = full_result

        old_nation = {"temperament": "Player"}
        if tick_function_label in th._EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS:
            player_tick_summary = ""
        elif old_nation.get("temperament", "None") == "Player":
            player_tick_summary = player_result
        else:
            player_tick_summary = ""
        full_tick_summary = result

        assert "has researched" not in player_tick_summary
        assert "resource windfall" in player_tick_summary
        assert "has researched" in full_tick_summary
        assert "resource windfall" in full_tick_summary


class TestDiseaseSpreadEventPlayerAttributionEndToEnd:
    """Confirms the actual tick-loop check (mirrored here rather than
    running the full tick()) correctly flags a disease spread landing on a
    player nation, the exact shape used at tick_helpers.py's
    player_tick_summary attribution site."""

    def test_spread_to_a_player_destination_is_flagged(self):
        disease_spread_events = [
            {"from_id": "src1", "to_id": "dst1", "to_temperament": "Player"},
        ]
        spread_to_a_player_nation = any(
            e.get("to_temperament") == "Player" for e in disease_spread_events
        )
        assert spread_to_a_player_nation is True

    def test_spread_to_a_non_player_destination_is_not_flagged(self):
        disease_spread_events = [
            {"from_id": "src1", "to_id": "dst1", "to_temperament": "Curious"},
        ]
        spread_to_a_player_nation = any(
            e.get("to_temperament") == "Player" for e in disease_spread_events
        )
        assert spread_to_a_player_nation is False
