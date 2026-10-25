"""Tests for the Discord @mention resolver helpers in helpers/tick_helpers.py
(2026-10-24 request: each line of the Discord-posted tick summary should
ping whichever real player it's relevant to). A player's Discord snowflake
lives on their players document's "id" field, set from the OAuth session
at login — never on the character/nation/org itself.
"""
from bson import ObjectId

import helpers.tick_helpers as th


class TestDiscordMentionForPlayerId:
    def test_resolves_to_the_players_discord_id(self, test_db):
        player_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "snowflake-123"})
        assert th._discord_mention_for_player_id(str(player_id), test_db["players"]) == "<@snowflake-123> "

    def test_blank_player_id_returns_empty_string(self, test_db):
        assert th._discord_mention_for_player_id("", test_db["players"]) == ""
        assert th._discord_mention_for_player_id(None, test_db["players"]) == ""

    def test_player_with_no_discord_id_on_file_returns_empty_string(self, test_db):
        player_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "name": "NoDiscordLinked"})
        assert th._discord_mention_for_player_id(str(player_id), test_db["players"]) == ""

    def test_unresolvable_player_id_returns_empty_string_not_raise(self, test_db):
        assert th._discord_mention_for_player_id("not-an-object-id", test_db["players"]) == ""
        assert th._discord_mention_for_player_id(str(ObjectId()), test_db["players"]) == ""


class TestDiscordMentionForCharacter:
    def test_uses_the_characters_own_player(self, test_db):
        player_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "snowflake-456"})
        character = {"player": str(player_id)}
        assert th._discord_mention_for_character(character, test_db["players"]) == "<@snowflake-456> "

    def test_character_with_no_player_returns_empty_string(self, test_db):
        assert th._discord_mention_for_character({"player": ""}, test_db["players"]) == ""
        assert th._discord_mention_for_character({}, test_db["players"]) == ""
        assert th._discord_mention_for_character(None, test_db["players"]) == ""


class TestDiscordMentionsForNation:
    def test_mentions_every_owning_player(self, test_db):
        p1, p2 = ObjectId(), ObjectId()
        test_db["players"].insert_many([
            {"_id": p1, "id": "snowflake-a"},
            {"_id": p2, "id": "snowflake-b"},
        ])
        nation = {"players": [str(p1), str(p2)]}
        mentions = th._discord_mentions_for_nation(nation, test_db["players"])
        assert "<@snowflake-a>" in mentions
        assert "<@snowflake-b>" in mentions

    def test_no_owners_returns_empty_string(self, test_db):
        assert th._discord_mentions_for_nation({"players": []}, test_db["players"]) == ""
        assert th._discord_mentions_for_nation({}, test_db["players"]) == ""
        assert th._discord_mentions_for_nation(None, test_db["players"]) == ""


class TestOrgLeaderPlayerId:
    def test_returns_the_leaders_player_id(self, test_db):
        player_id = ObjectId()
        org_id = str(ObjectId())
        test_db["characters"].insert_one({"ruling_nation_org": org_id, "player": str(player_id)})
        assert th._org_leader_player_id(org_id, test_db["characters"]) == str(player_id)

    def test_no_leader_returns_none(self, test_db):
        assert th._org_leader_player_id(str(ObjectId()), test_db["characters"]) is None

    def test_blank_org_id_returns_none(self, test_db):
        assert th._org_leader_player_id("", test_db["characters"]) is None

    def test_org_leader_has_real_player_still_a_bool(self, test_db):
        """Existing callers/tests depend on this staying a plain bool --
        _org_leader_player_id is additive, not a replacement."""
        org_id = str(ObjectId())
        test_db["characters"].insert_one({"ruling_nation_org": org_id, "player": str(ObjectId())})
        assert th._org_leader_has_real_player(org_id, test_db["characters"]) is True
        assert th._org_leader_has_real_player(str(ObjectId()), test_db["characters"]) is False
