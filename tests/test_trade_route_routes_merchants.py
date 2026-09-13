"""
Tests for extending the trade route propose/accept/reject/cancel routes to
support a merchant company on either side of a route (routes/trade_route_routes.py):

  - Ownership checks (_can_act_on_party) resolve merchant ownership via a
    character ruling it (character.ruling_nation_org == merchant._id — the
    same polymorphic field nations use), delegating to
    helpers.visibility_helpers.is_item_owner so both stay in lockstep.
  - The AI-approval gate (_is_ai_party) treats a merchant with no
    player-controlled ruling character as AI, same concept as an AI nation.
  - propose_trade_route records nation_a_type/nation_b_type on the route doc
    so accept/reject/cancel know which collection to resolve each side from.
"""
import importlib
from unittest.mock import patch, MagicMock

from bson import ObjectId

# routes/__init__.py does `from .trade_route_routes import trade_route_routes`,
# which rebinds the `trade_route_routes` attribute on the `routes` package to
# the Blueprint object — pulling from sys.modules avoids that shadowing (same
# issue documented in tests/test_toggle_tech_researched.py).
trr = importlib.import_module("routes.trade_route_routes")


def _patch_mongo(test_db):
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    return (
        patch.object(trr, "mongo", fake_mongo),
        patch("helpers.visibility_helpers.mongo", fake_mongo),
    )


class TestIsAiParty:
    def test_merchant_with_no_id_is_ai(self):
        assert trr._is_ai_party("merchant", {}) is True

    def test_merchant_with_no_doc_is_ai(self):
        assert trr._is_ai_party("merchant", None) is True

    def test_merchant_with_player_controlled_ruler_is_not_ai(self, test_db):
        merchant_id = ObjectId()
        test_db["characters"].insert_one({"ruling_nation_org": str(merchant_id), "player": "player-1"})
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = trr._is_ai_party("merchant", {"_id": merchant_id})
        assert result is False

    def test_merchant_with_no_ruler_is_ai(self, test_db):
        merchant_id = ObjectId()
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = trr._is_ai_party("merchant", {"_id": merchant_id})
        assert result is True

    def test_merchant_with_unassigned_ruler_is_ai(self, test_db):
        """A ruling character exists but has no player attached — matches
        this codebase's convention of "" (not None) for an unset player
        reference (see get_viewer_nations' ruling_nation_org comment)."""
        merchant_id = ObjectId()
        test_db["characters"].insert_one({"ruling_nation_org": str(merchant_id), "player": ""})
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = trr._is_ai_party("merchant", {"_id": merchant_id})
        assert result is True


class TestCanActOnPartyForMerchants:
    def test_ruling_characters_player_can_act(self, flask_app, test_db):
        player_id = ObjectId()
        merchant_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({"player": str(player_id), "ruling_nation_org": str(merchant_id)})
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Traders Inc"})

        p1, p2 = _patch_mongo(test_db)
        with flask_app.test_request_context():
            with p1, p2, patch.object(trr, "g") as mock_g:
                mock_g.user = {"id": "user-1", "is_admin": False}
                result = trr._can_act_on_party("merchant", "Traders Inc")
        assert result is True

    def test_unrelated_player_cannot_act(self, flask_app, test_db):
        player_id = ObjectId()
        other_player_id = ObjectId()
        merchant_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["players"].insert_one({"_id": other_player_id, "id": "user-2"})
        test_db["characters"].insert_one({"player": str(other_player_id), "ruling_nation_org": str(merchant_id)})
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Traders Inc"})

        p1, p2 = _patch_mongo(test_db)
        with flask_app.test_request_context():
            with p1, p2, patch.object(trr, "g") as mock_g:
                mock_g.user = {"id": "user-1", "is_admin": False}
                result = trr._can_act_on_party("merchant", "Traders Inc")
        assert result is False

    def test_admin_can_always_act(self, flask_app, test_db):
        p1, p2 = _patch_mongo(test_db)
        with flask_app.test_request_context():
            with p1, p2, patch.object(trr, "g") as mock_g:
                mock_g.user = {"id": "admin-1", "is_admin": True}
                result = trr._can_act_on_party("merchant", "Anyone's Merchant")
        assert result is True


class TestProposeTradeRouteRecordsPartyTypes:
    def _propose(self, flask_app, test_db, form_data, user):
        p1, p2 = _patch_mongo(test_db)
        with flask_app.test_request_context(
            "/trade_routes/propose", method="POST", data=form_data,
            headers={"Referer": "http://testserver/nations/edit/NationA"},
        ):
            with p1, p2, patch.object(trr, "g") as mock_g, \
                 patch.object(trr, "get_road_path_distance", return_value=(2, True)), \
                 patch.object(trr, "count_route_slots", return_value=(0, 0)), \
                 patch("helpers.trade_route_helpers._nations_share_market", return_value=False):
                mock_g.user = user
                return trr.propose_trade_route()

    def test_nation_proposing_to_a_merchant_records_types(self, flask_app, test_db):
        player_id = ObjectId()
        other_player_id = ObjectId()
        nation_id = ObjectId()
        merchant_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["players"].insert_one({"_id": other_player_id, "id": "user-2"})
        test_db["characters"].insert_many([
            {"player": str(player_id), "ruling_nation_org": str(nation_id)},
            {"player": str(other_player_id), "ruling_nation_org": str(merchant_id)},
        ])
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "NationA", "trade_speed": 7,
            "export_slots": 10, "import_slots": 10,
        })
        test_db["merchants"].insert_one({"_id": merchant_id, "name": "Traders Inc", "trade_speed": 20})

        self._propose(
            flask_app, test_db,
            {
                "proposer_nation": "NationA", "acceptor_nation": "Traders Inc",
                "proposer_type": "nation", "acceptor_type": "merchant",
                "resources_a_to_b": '[{"resource": "food", "quantity": 5}]',
                "resources_b_to_a": "[]",
                "duration_ticks": "0",
            },
            {"id": "user-1", "is_admin": False},
        )

        route = test_db["trade_routes"].find_one({"nation_a": "NationA", "nation_b": "Traders Inc"})
        assert route is not None
        assert route["nation_a_type"] == "nation"
        assert route["nation_b_type"] == "merchant"
        assert route["status"] == "pending"

    def test_route_involving_ai_merchant_goes_to_moderation(self, flask_app, test_db):
        """A merchant with no player-controlled ruling character is AI — the
        route should NOT be inserted directly; it should go through
        request_change instead (mirrors the existing AI-nation gate)."""
        player_id = ObjectId()
        nation_id = ObjectId()
        test_db["players"].insert_one({"_id": player_id, "id": "user-1"})
        test_db["characters"].insert_one({"player": str(player_id), "ruling_nation_org": str(nation_id)})
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "NationA", "trade_speed": 7,
            "export_slots": 10, "import_slots": 10,
        })
        # AI merchant: no ruling character at all
        test_db["merchants"].insert_one({"name": "AI Traders", "trade_speed": 20})

        with patch("helpers.change_helpers.request_change", return_value="42") as mock_request_change:
            self._propose(
                flask_app, test_db,
                {
                    "proposer_nation": "NationA", "acceptor_nation": "AI Traders",
                    "proposer_type": "nation", "acceptor_type": "merchant",
                    "resources_a_to_b": '[{"resource": "food", "quantity": 5}]',
                    "resources_b_to_a": "[]",
                    "duration_ticks": "0",
                },
                {"id": "user-1", "is_admin": False},
            )
            mock_request_change.assert_called_once()

        # No direct insert happened — only a change request.
        assert test_db["trade_routes"].find_one({"nation_a": "NationA", "nation_b": "AI Traders"}) is None
