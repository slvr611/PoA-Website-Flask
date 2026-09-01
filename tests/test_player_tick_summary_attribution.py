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
