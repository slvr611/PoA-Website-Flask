"""Integration test for tick()'s player_tick_summary_sections tracking
(helpers/tick_helpers.py): confirms the real tick() function builds one
section per TICK FUNCTION (2026-10-24 request: chunk by function, not by
entity category — e.g. "Nation Stability Tick" must never share a message
with "Nation Passive Expansion Tick"), with each player-relevant line
prefixed by a Discord @mention of the real player it's relevant to, and
passes those sections through to give_tick_summary for posting (see
helpers/discord_post_helpers.py).

Runs the real tick() end to end (mongomock-backed, no real network/DB)
rather than just unit-testing the mention/section logic in isolation, so a
future refactor of tick()'s loop structure can't silently break this
without a test catching it.
"""
from unittest.mock import patch
from bson import ObjectId

import helpers.tick_helpers as th
from app_core import category_data


class TestPlayerTickSummarySectionsEndToEnd:
    def test_faction_income_produces_a_section_labeled_by_function_with_a_mention(self, test_db, monkeypatch):
        """Faction Income Tick is the simplest player-relevant loop to
        exercise end to end. A no-op character tick is also enabled purely
        so tick()'s character_db local (referenced by the faction loop's
        _org_leader_player_id lookup) gets assigned — it's only ever set
        inside the "if collect_character_data" block.
        """
        monkeypatch.setitem(category_data["factions"], "database", test_db["factions"])
        monkeypatch.setitem(category_data["characters"], "database", test_db["characters"])

        player_doc_id = ObjectId()
        test_db["players"].insert_one({"_id": player_doc_id, "name": "RealPlayer", "id": "discord-snowflake-1"})

        faction_id = ObjectId()
        test_db["factions"].insert_one({
            "_id": faction_id, "name": "Testguild", "influence": 100, "influence_income": 5,
        })
        test_db["characters"].insert_one({
            "_id": ObjectId(), "name": "Testlord", "ruling_nation_org": str(faction_id), "player": str(player_doc_id),
        })

        captured = {}

        def _fake_give_tick_summary(player_summary, full_summary, sections=None):
            captured["player_summary"] = player_summary
            captured["sections"] = sections
            return "fake_path.txt"

        with patch.object(th, "mongo") as fake_mongo, \
             patch.object(th, "give_tick_summary", side_effect=_fake_give_tick_summary):
            fake_mongo.db = test_db
            th.tick({
                "run_Character Modifier Decay Tick": "on",
                "run_Faction Income Tick": "on",
                "run_Give Tick Summary": "on",
            })

        assert "sections" in captured
        labels = [label for label, _ in captured["sections"]]
        assert labels == ["Faction Income Tick"]
        section_text = captured["sections"][0][1]
        assert "<@discord-snowflake-1>" in section_text
        assert "Testguild" in section_text
        # The plain file-saved player_tick_summary must NOT carry mention
        # codes -- those are only for what gets posted to Discord.
        assert "<@discord-snowflake-1>" not in captured["player_summary"]
        assert "Testguild" in captured["player_summary"]

    def test_excluded_label_contributes_to_full_summary_but_no_section(self, test_db, monkeypatch):
        """Mercenary Upkeep Tick is in _EXCLUDED_FROM_PLAYER_SUMMARY_TICK_LABELS
        (2026-10-24 request) -- its text must still reach full_tick_summary,
        but must never surface as (or inside) a player-summary section."""
        monkeypatch.setitem(category_data["mercenaries"], "database", test_db["mercenaries"])
        monkeypatch.setitem(category_data["characters"], "database", test_db["characters"])

        player_doc_id = ObjectId()
        test_db["players"].insert_one({"_id": player_doc_id, "name": "RealPlayer", "id": "discord-snowflake-1"})

        merc_id = ObjectId()
        test_db["mercenaries"].insert_one({
            "_id": merc_id, "name": "Testband", "treasury": 1000, "upkeep": 50,
        })
        test_db["characters"].insert_one({
            "_id": ObjectId(), "name": "Testlord", "ruling_nation_org": str(merc_id), "player": str(player_doc_id),
        })

        captured = {}

        def _fake_give_tick_summary(player_summary, full_summary, sections=None):
            captured["player_summary"] = player_summary
            captured["full_summary"] = full_summary
            captured["sections"] = sections
            return "fake_path.txt"

        with patch.object(th, "mongo") as fake_mongo, \
             patch.object(th, "give_tick_summary", side_effect=_fake_give_tick_summary):
            fake_mongo.db = test_db
            th.tick({
                "run_Character Modifier Decay Tick": "on",
                "run_Mercenary Upkeep Tick": "on",
                "run_Give Tick Summary": "on",
            })

        assert captured["sections"] == []
        assert "Testband" not in captured["player_summary"]
        assert "Testband" in captured["full_summary"]

    def test_two_nation_tick_functions_produce_two_separate_sections_and_mention_the_owner(self, test_db, monkeypatch):
        """The core of this request: results from two different NATION tick
        functions must never land in the same section/message, even though
        both ran against the very same player nation in the very same
        tick() run -- and each one's line is prefixed with that nation's
        owning player's mention.

        Uses two trivial stand-in functions registered in place of
        NATION_TICK_FUNCTIONS rather than two real tick functions, so this
        test exercises tick()'s section-boundary/mention mechanics directly
        without needing to satisfy any real tick function's game-state
        preconditions (war state, territory, etc.)."""
        monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
        monkeypatch.setitem(category_data["characters"], "database", test_db["characters"])

        player_doc_id = ObjectId()
        test_db["players"].insert_one({"_id": player_doc_id, "name": "RealPlayer", "id": "discord-snowflake-9"})

        nation_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "Testland", "temperament": "Player", "players": [str(player_doc_id)],
        })

        def _stub_stability(old_nation, new_nation, schema, **kwargs):
            return f"{old_nation['name']} gained a level of stability.\n"

        def _stub_expansion(old_nation, new_nation, schema, **kwargs):
            return f"{old_nation['name']} expanded into 1 tile(s).\n"

        captured = {}

        def _fake_give_tick_summary(player_summary, full_summary, sections=None):
            captured["sections"] = sections
            return "fake_path.txt"

        with patch.object(th, "mongo") as fake_mongo, \
             patch.object(th, "give_tick_summary", side_effect=_fake_give_tick_summary), \
             patch.object(th, "NATION_TICK_FUNCTIONS", {
                 "Nation Stability Tick": _stub_stability,
                 "Nation Passive Expansion Tick": _stub_expansion,
             }):
            fake_mongo.db = test_db
            th.tick({
                "run_Nation Stability Tick": "on",
                "run_Nation Passive Expansion Tick": "on",
                "run_Give Tick Summary": "on",
            })

        sections = dict(captured["sections"])
        assert set(sections.keys()) == {"Nation Stability Tick", "Nation Passive Expansion Tick"}
        assert "gained a level of stability" in sections["Nation Stability Tick"]
        assert "expanded into" not in sections["Nation Stability Tick"]
        assert "expanded into" in sections["Nation Passive Expansion Tick"]
        assert "gained a level of stability" not in sections["Nation Passive Expansion Tick"]
        for text in sections.values():
            assert "<@discord-snowflake-9>" in text
