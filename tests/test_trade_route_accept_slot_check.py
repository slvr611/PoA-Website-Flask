"""
Regression test for a real report: Lusariyya proposed a trade route to
Khanya (Lusariyya sends Khanya $1000/session — Khanya's IMPORT direction
only, resources_b_to_a empty). Accepting it failed with "Khanya does not
have enough export slots", even though the route doesn't use export slots
at all.

Root cause: routes/trade_route_routes.py's accept_trade_route ran BOTH the
import and export slot checks whenever either resources_a_to_b or
resources_b_to_a was non-empty, instead of gating each check independently
on its own direction (the way propose_trade_route's equivalent checks
already do). So when the acceptor's TOTAL export usage was already over
its export_slots cap from other, unrelated active routes, accepting a
route that adds ZERO new export cost still failed the export check —
nothing about the route being accepted was actually over capacity.

Fixed by gating the import check on resources_a_to_b and the export check
on resources_b_to_a independently, matching propose_trade_route.
"""
import importlib
from unittest.mock import patch, MagicMock

from bson import ObjectId

trr = importlib.import_module("routes.trade_route_routes")


def _patch_mongo(test_db):
    """count_route_slots/_slot_cost_for_direction/_nations_share_market all
    live in helpers.trade_route_helpers and use ITS OWN separately-imported
    `mongo` reference — patching only trade_route_routes.mongo leaves those
    calls hitting the real, unpatched (production) connection. Both must be
    patched, same pattern as test_trade_route_routes_merchants.py's own
    _patch_mongo."""
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    return (
        patch.object(trr, "mongo", fake_mongo),
        patch("helpers.trade_route_helpers.mongo", fake_mongo),
    )


def _accept(flask_app, test_db, route_id):
    with flask_app.test_request_context(
        f"/trade_routes/{route_id}/accept", method="POST",
        headers={"Referer": "http://testserver/nations/edit/Khanya"},
    ):
        p1, p2 = _patch_mongo(test_db)
        with p1, p2, patch.object(trr, "g") as mock_g:
            mock_g.user = {"id": "admin-1", "is_admin": True}
            return trr.accept_trade_route(route_id)


class TestAcceptTradeRouteSlotChecksAreDirectionSpecific:
    def test_import_only_route_accepted_even_when_acceptor_is_over_export_cap(self, flask_app, test_db):
        """Reproduces the exact "Khanya doesn't have enough export slots"
        report: Khanya is already over its export cap from OTHER active
        routes, but THIS route only asks for an import slot."""
        test_db["nations"].insert_many([
            {"name": "Lusariyya", "trade_speed": 7},
            {"name": "Khanya", "trade_speed": 7, "export_slots": 2, "import_slots": 2},
        ])
        # Three pre-existing active routes where Khanya is the exporter,
        # each costing 1 export slot — pushes Khanya's export_used to 3,
        # already over its export_slots cap of 2, unrelated to the new route.
        for i in range(3):
            test_db["trade_routes"].insert_one({
                "nation_a": "Khanya", "nation_b": f"Other{i}",
                "nation_a_type": "nation", "nation_b_type": "nation",
                "status": "active",
                "resources_a_to_b": [{"resource": "money", "quantity": 1000}],
                "resources_b_to_a": [],
            })

        route_id = test_db["trade_routes"].insert_one({
            "nation_a": "Lusariyya", "nation_b": "Khanya",
            "nation_a_type": "nation", "nation_b_type": "nation",
            "status": "pending",
            "resources_a_to_b": [{"resource": "money", "quantity": 1000}],
            "resources_b_to_a": [],
        }).inserted_id

        _accept(flask_app, test_db, str(route_id))

        route = test_db["trade_routes"].find_one({"_id": route_id})
        assert route["status"] == "active", (
            "import-only route was blocked by an export-slot check that "
            "has nothing to do with this route"
        )

    def test_export_only_route_still_blocked_when_acceptor_is_over_import_cap(self, flask_app, test_db):
        """Sanity check in the other direction: a route that only uses
        IMPORT slots must still correctly block when the acceptor really
        is over its import cap for THIS route's own direction."""
        test_db["nations"].insert_many([
            {"name": "Lusariyya", "trade_speed": 7},
            {"name": "Khanya", "trade_speed": 7, "export_slots": 2, "import_slots": 2},
        ])
        for i in range(3):
            test_db["trade_routes"].insert_one({
                "nation_a": "Khanya", "nation_b": f"Other{i}",
                "nation_a_type": "nation", "nation_b_type": "nation",
                "status": "active",
                "resources_a_to_b": [],
                "resources_b_to_a": [{"resource": "money", "quantity": 1000}],
            })

        route_id = test_db["trade_routes"].insert_one({
            "nation_a": "Lusariyya", "nation_b": "Khanya",
            "nation_a_type": "nation", "nation_b_type": "nation",
            "status": "pending",
            "resources_a_to_b": [{"resource": "money", "quantity": 1000}],
            "resources_b_to_a": [],
        }).inserted_id

        _accept(flask_app, test_db, str(route_id))

        route = test_db["trade_routes"].find_one({"_id": route_id})
        assert route["status"] == "pending", (
            "route should have been blocked: it needs 1 import slot but "
            "Khanya's import usage is already at/over cap"
        )
