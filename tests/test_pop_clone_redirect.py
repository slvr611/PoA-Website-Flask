"""
Regression test for a bug where requesting a pop clone from the nation edit
page redirected via /go_back, which relies on session-tracked previous URLs.
That tracking fires on every non-static request, including the browser's
/api/s3-image requests to load the nation's banner image — so /go_back could
land on the raw banner image URL instead of navigating back to a real page.

data_item_clone_request/data_item_clone_approve now special-case pops and
redirect straight to the nation's edit page instead of trusting /go_back.
"""
from unittest.mock import MagicMock, patch

from bson import ObjectId

from routes.data_item_routes import _clone_redirect


def test_pop_clone_redirects_to_nation_edit_page():
    nation_id = ObjectId()
    pop = {"_id": ObjectId(), "name": "Copy of Some Pop", "nation": str(nation_id)}

    fake_mongo = MagicMock()
    fake_mongo.db.nations.find_one.return_value = {"_id": nation_id, "name": "Ashfall Dominion"}

    with patch("routes.data_item_routes.mongo", fake_mongo):
        response = _clone_redirect("pops", pop)

    assert response.status_code == 302
    assert response.location == "/nations/edit/Ashfall Dominion"


def test_pop_clone_falls_back_to_go_back_when_nation_not_found():
    pop = {"_id": ObjectId(), "name": "Copy of Some Pop", "nation": str(ObjectId())}

    fake_mongo = MagicMock()
    fake_mongo.db.nations.find_one.return_value = None

    with patch("routes.data_item_routes.mongo", fake_mongo):
        response = _clone_redirect("pops", pop)

    assert response.status_code == 302
    assert response.location == "/go_back"


def test_pop_clone_falls_back_to_go_back_on_missing_nation_field():
    pop = {"_id": ObjectId(), "name": "Copy of Some Pop"}

    fake_mongo = MagicMock()

    with patch("routes.data_item_routes.mongo", fake_mongo):
        response = _clone_redirect("pops", pop)

    assert response.status_code == 302
    assert response.location == "/go_back"


def test_non_pop_clone_uses_go_back():
    item = {"_id": ObjectId(), "name": "Copy of Some District"}

    response = _clone_redirect("districts", item)

    assert response.status_code == 302
    assert response.location == "/go_back"
