"""
Regression tests for the nation view page's "Active Wars" display, added to
the military tab's war-section (id="war-section" in templates/nation_owner.
html): previously no war info was shown there at all. The nations schema's
"wars" field has always been excluded from this page's linked_objects for
performance reasons (get_linked_objects_parallel's generic linkCollection
resolution is unbatched — one query per war_link plus a separate find_one
per linked war). _fetch_active_wars_for_nation (routes/nation_routes.py) is
a small, fully-batched, nation-scoped query instead: one war_links lookup,
one batched wars fetch, one batched war_links-for-those-wars fetch, one
batched nations fetch for the opponents — four queries regardless of how
many wars/participants are involved.

"Active" matches nation_war_support_tick/nation_infamy_decay_tick's own
definition (helpers/tick_helpers.py): session_declared <= current session
<= session_ended (or session_ended is still unset).
"""
import importlib

from bson import ObjectId
from flask import g

nr = importlib.import_module("routes.nation_routes")


def _render(test_db, monkeypatch, flask_app, nation_id, nation_overrides=None, session_counter=5):
    from app_core import mongo as real_mongo, category_data
    from calculations.field_calculations import calculate_all_fields

    # See test_nation_view_military_preview.py's _render docstring: patching
    # the shared real `mongo` object's `.db` in place is what reaches
    # helpers/render_helpers.py's own independently-bound `mongo` name too.
    monkeypatch.setattr(real_mongo, "db", test_db)
    monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
    schema = category_data["nations"]["schema"]

    test_db["global_modifiers"].insert_one({"name": "global_modifiers", "session_counter": session_counter})

    base = {"_id": nation_id, "name": "Testland", "modifiers": []}
    base.update(nation_overrides or {})
    calculated, breakdowns = calculate_all_fields(dict(base), schema, "nation", return_breakdowns=True)
    base.update(calculated)
    base["breakdowns"] = breakdowns
    test_db["nations"].insert_one(base)

    with flask_app.test_request_context("/nations/item/Testland"):
        g.user = None
        g.is_non_player_admin = True
        return nr.nation_item("Testland")


def _make_war(test_db, name, war_type="conquest", session_declared=1, session_ended=None):
    war_id = test_db["wars"].insert_one({
        "name": name, "war_type": war_type,
        "session_declared": session_declared, "session_ended": session_ended,
    }).inserted_id
    return str(war_id)


def _add_participant(test_db, war_id_str, nation_id_str, stance):
    test_db["war_links"].insert_one({"war": war_id_str, "participant": nation_id_str, "stance": stance})


class TestActiveWarsShownOnNationPage:
    def test_active_war_with_opponent_is_shown(self, test_db, monkeypatch, flask_app):
        nation_id = ObjectId()
        opponent_id = test_db["nations"].insert_one({"name": "Enemyland"}).inserted_id

        war_id = _make_war(test_db, "The Border Conflict", session_declared=1)
        _add_participant(test_db, war_id, str(nation_id), "Attacker")
        _add_participant(test_db, war_id, str(opponent_id), "Defender")

        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "Active Wars" in html
        assert "The Border Conflict" in html
        assert "Enemyland" in html
        assert "Attacker" in html
        assert "Defender" in html
        assert "/wars/item/The%20Border%20Conflict" in html or "/wars/item/The Border Conflict" in html

    def test_ended_war_is_not_shown(self, test_db, monkeypatch, flask_app):
        nation_id = ObjectId()
        war_id = _make_war(test_db, "The Old War", session_declared=1, session_ended=3)
        _add_participant(test_db, war_id, str(nation_id), "Attacker")

        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "The Old War" not in html

    def test_not_yet_declared_war_is_not_shown(self, test_db, monkeypatch, flask_app):
        """session_declared in the future (e.g. a scheduling edge case) —
        not yet live, must not show as active."""
        nation_id = ObjectId()
        war_id = _make_war(test_db, "The Future War", session_declared=10, session_ended=None)
        _add_participant(test_db, war_id, str(nation_id), "Attacker")

        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "The Future War" not in html

    def test_war_ending_this_session_still_shows(self, test_db, monkeypatch, flask_app):
        nation_id = ObjectId()
        war_id = _make_war(test_db, "The Final Push", session_declared=1, session_ended=5)
        _add_participant(test_db, war_id, str(nation_id), "Defender")

        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "The Final Push" in html

    def test_no_active_wars_omits_the_section_entirely(self, test_db, monkeypatch, flask_app):
        nation_id = ObjectId()
        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "Active Wars" not in html

    def test_multiple_opponents_all_listed(self, test_db, monkeypatch, flask_app):
        nation_id = ObjectId()
        opp1 = test_db["nations"].insert_one({"name": "Alpha Foe"}).inserted_id
        opp2 = test_db["nations"].insert_one({"name": "Beta Foe"}).inserted_id

        war_id = _make_war(test_db, "The Grand Coalition War", session_declared=1)
        _add_participant(test_db, war_id, str(nation_id), "Defender")
        _add_participant(test_db, war_id, str(opp1), "Attacker")
        _add_participant(test_db, war_id, str(opp2), "Attacker")

        html = _render(test_db, monkeypatch, flask_app, nation_id, session_counter=5)

        assert "Alpha Foe" in html
        assert "Beta Foe" in html

    def test_shown_in_editable_mode_too(self, test_db, monkeypatch, flask_app):
        """The war section (and the active-wars block inside it) isn't
        split by editable/view mode — an owner editing their own nation
        should see their active wars just like a viewer does."""
        from app_core import mongo as real_mongo, category_data
        from calculations.field_calculations import calculate_all_fields

        monkeypatch.setattr(real_mongo, "db", test_db)
        monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
        schema = category_data["nations"]["schema"]

        test_db["global_modifiers"].insert_one({"name": "global_modifiers", "session_counter": 5})

        nation_id = ObjectId()
        opponent_id = test_db["nations"].insert_one({"name": "Enemyland"}).inserted_id
        base = {"_id": nation_id, "name": "Testland", "modifiers": []}
        calculated, breakdowns = calculate_all_fields(dict(base), schema, "nation", return_breakdowns=True)
        base.update(calculated)
        base["breakdowns"] = breakdowns
        test_db["nations"].insert_one(base)

        war_id = _make_war(test_db, "The Border Conflict", session_declared=1)
        _add_participant(test_db, war_id, str(nation_id), "Attacker")
        _add_participant(test_db, war_id, str(opponent_id), "Defender")

        with flask_app.test_request_context("/nations/edit/Testland"):
            g.user = {"id": "admin-1", "is_admin": True}
            g.is_non_player_admin = True
            g.view_access_level = 10
            html = nr._render_nation_edit("Testland")

        assert "Active Wars" in html
        assert "The Border Conflict" in html


class TestFetchActiveWarsForNationHelper:
    def test_returns_empty_list_for_nation_with_no_wars(self, test_db, monkeypatch):
        from app_core import mongo as real_mongo
        monkeypatch.setattr(real_mongo, "db", test_db)
        assert nr._fetch_active_wars_for_nation(str(ObjectId())) == []

    def test_four_queries_regardless_of_participant_count(self, test_db, monkeypatch):
        """Confirms the batching claim: war_links, wars, war_links-for-wars,
        nations are each queried exactly once, not once per link/war."""
        from app_core import mongo as real_mongo
        monkeypatch.setattr(real_mongo, "db", test_db)
        test_db["global_modifiers"].insert_one({"name": "global_modifiers", "session_counter": 5})

        nation_id = ObjectId()
        war_ids = []
        for i in range(3):
            opp = test_db["nations"].insert_one({"name": f"Foe{i}"}).inserted_id
            wid = _make_war(test_db, f"War {i}", session_declared=1)
            _add_participant(test_db, wid, str(nation_id), "Attacker")
            _add_participant(test_db, wid, str(opp), "Defender")
            war_ids.append(wid)

        import mongomock
        call_counts = {"war_links": 0, "wars": 0, "nations": 0, "global_modifiers": 0}
        real_find = {
            "war_links": test_db["war_links"].find,
            "wars": test_db["wars"].find,
            "nations": test_db["nations"].find,
        }
        real_find_one = test_db["global_modifiers"].find_one

        def _counting_find(name):
            def _f(*a, **kw):
                call_counts[name] += 1
                return real_find[name](*a, **kw)
            return _f

        def _counting_find_one(*a, **kw):
            call_counts["global_modifiers"] += 1
            return real_find_one(*a, **kw)

        monkeypatch.setattr(test_db["war_links"], "find", _counting_find("war_links"))
        monkeypatch.setattr(test_db["wars"], "find", _counting_find("wars"))
        monkeypatch.setattr(test_db["nations"], "find", _counting_find("nations"))
        monkeypatch.setattr(test_db["global_modifiers"], "find_one", _counting_find_one)

        result = nr._fetch_active_wars_for_nation(str(nation_id))

        assert len(result) == 3
        assert call_counts["war_links"] == 2   # own links, then all-links-for-active-wars
        assert call_counts["wars"] == 1
        assert call_counts["nations"] == 1
        assert call_counts["global_modifiers"] == 1
