"""
Regression test for the nation view page's military preview (the summary
card above the unit tables): it previously only showed unit counts,
Conscription, Funding, and the two Doctrines. Added Magic Stance, the army
size granted by the current Military Funding law, the ruler(s)' Strategy
stat, Mercenary Stance (mercenary_law), and the primary race's positive/
negative traits — with traits that affect units in any way (combat stats,
civilian units, unit movement/terrain cost, locking out mundane units, etc.)
bolded/colored (race_trait_is_war_relevant in routes/nation_routes.py) so a
reader can tell at a glance which traits matter for the military.
"""
import importlib

from bson import ObjectId
from flask import g

from app_core import category_data

nr = importlib.import_module("routes.nation_routes")


def _render(test_db, monkeypatch, flask_app, nation_overrides, race_doc, ruler_strategy=None):
    from app_core import mongo as real_mongo, category_data
    from calculations.field_calculations import calculate_all_fields

    # helpers/render_helpers.py (which resolves linked_objects like "rulers"
    # and "primary_race") imports its own `mongo` name bound at module load
    # and always queries via `mongo.db[collection]` dynamically — rebinding
    # nation_routes.py's own `mongo` name (as test_nation_pending_preview_
    # cache.py's fixture does) doesn't reach it. Patching the shared real
    # `mongo` object's `.db` attribute in place does, since every module
    # holds a reference to that same object.
    monkeypatch.setattr(real_mongo, "db", test_db)
    monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
    monkeypatch.setitem(category_data["races"], "database", test_db["races"])
    monkeypatch.setitem(category_data["characters"], "database", test_db["characters"])
    schema = category_data["nations"]["schema"]

    race_id = ObjectId()
    test_db["races"].insert_one({**race_doc, "_id": race_id, "name": "Testkin"})

    nation_id = ObjectId()
    base = {"_id": nation_id, "name": "Testland", "modifiers": [], "primary_race": race_id}
    base.update(nation_overrides)
    calculated, breakdowns = calculate_all_fields(dict(base), schema, "nation", return_breakdowns=True)
    base.update(calculated)
    base["breakdowns"] = breakdowns
    test_db["nations"].insert_one(base)

    if ruler_strategy is not None:
        test_db["characters"].insert_one({
            "_id": ObjectId(), "name": "Ruler Rex",
            "ruling_nation_org": str(nation_id), "strategy": ruler_strategy,
        })

    with flask_app.test_request_context("/nations/item/Testland"):
        g.user = None
        g.is_non_player_admin = True
        return nr.nation_item("Testland")


class TestMilitaryPreviewShowsNewFields:
    def test_magic_stance_shown(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {"magic_stance": "Destruction"}, {"positive_trait": "None", "negative_trait": "None"},
        )
        assert "Magic Stance" in html
        assert "Destruction" in html

    def test_mercenary_stance_shown(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {"mercenary_law": "Mercenary Reliance"}, {"positive_trait": "None", "negative_trait": "None"},
        )
        assert "Mercenary Stance" in html
        assert "Mercenary Reliance" in html

    def test_army_size_from_funding_shown(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {"military_funding": "Substantial"}, {"positive_trait": "None", "negative_trait": "None"},
        )
        assert "5 army size" in html  # Substantial -> army_size: 5 in nations.json's military_funding laws

    def test_ruler_strategy_shown(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "None", "negative_trait": "None"}, ruler_strategy=7,
        )
        assert "Ruler Strategy" in html
        assert "Ruler Rex (7)" in html

    def test_no_rulers_omits_ruler_strategy_line(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "None", "negative_trait": "None"},
        )
        assert "Ruler Strategy" not in html


class TestMilitaryPreviewRacialTraitHighlighting:
    def test_war_relevant_positive_trait_is_bolded_and_colored(self, test_db, monkeypatch, flask_app):
        """Sturdy grants nation_defense/armor_cannot_be_ignored — a real
        combat bonus, so it must render highlighted."""
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "Sturdy", "negative_trait": "None"},
        )
        assert "Racial Traits" in html
        pos = html.index("Sturdy")
        tag_start = html.rindex("<span", 0, pos)
        tag = html[tag_start:pos]
        assert "font-weight:700" in tag
        assert "var(--pos)" in tag

    def test_non_war_relevant_positive_trait_is_not_highlighted(self, test_db, monkeypatch, flask_app):
        """Curious only reduces technology_cost_modifier — no combat effect."""
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "Curious", "negative_trait": "None"},
        )
        pos = html.index("Curious")
        tag_start = html.rindex("<span", 0, pos)
        tag = html[tag_start:pos]
        assert "font-weight:700" not in tag

    def test_war_relevant_negative_trait_is_bolded_and_colored(self, test_db, monkeypatch, flask_app):
        """Craven grants nation_unit_morale: -1 — a real combat malus."""
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "None", "negative_trait": "Craven"},
        )
        neg = html.index("Craven")
        tag_start = html.rindex("<span", 0, neg)
        tag = html[tag_start:neg]
        assert "font-weight:700" in tag
        assert "var(--crit)" in tag

    def test_non_war_relevant_negative_trait_is_not_highlighted(self, test_db, monkeypatch, flask_app):
        """Dim only reduces research_production — no combat effect."""
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "None", "negative_trait": "Dim"},
        )
        neg = html.index("Dim")
        tag_start = html.rindex("<span", 0, neg)
        tag = html[tag_start:neg]
        assert "font-weight:700" not in tag

    def test_trait_affecting_only_civilian_units_is_still_highlighted(self, test_db, monkeypatch, flask_app):
        """Courageous affects civilian_unit_hp/speed — any effect on units at
        all counts as war-relevant, not just direct combat stats."""
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "Courageous", "negative_trait": "None"},
        )
        pos = html.index("Courageous")
        tag_start = html.rindex("<span", 0, pos)
        tag = html[tag_start:pos]
        assert "font-weight:700" in tag

    def test_no_traits_omits_racial_traits_line(self, test_db, monkeypatch, flask_app):
        html = _render(
            test_db, monkeypatch, flask_app,
            {}, {"positive_trait": "None", "negative_trait": "None"},
        )
        assert "Racial Traits" not in html


class TestRaceTraitIsWarRelevantHelper:
    def test_sturdy_is_war_relevant(self):
        assert nr.race_trait_is_war_relevant("positive_trait", "Sturdy") is True

    def test_curious_is_not_war_relevant(self):
        assert nr.race_trait_is_war_relevant("positive_trait", "Curious") is False

    def test_craven_is_war_relevant(self):
        assert nr.race_trait_is_war_relevant("negative_trait", "Craven") is True

    def test_slow_unit_speed_is_war_relevant(self):
        assert nr.race_trait_is_war_relevant("negative_trait", "Slow") is True

    def test_civilian_unit_stats_still_count_as_unit_relevant(self):
        """Courageous only touches civilian_unit_hp/speed — any effect on
        units at all counts, not just combat-specific stats."""
        assert nr.race_trait_is_war_relevant("positive_trait", "Courageous") is True

    def test_airborne_terrain_speed_cost_counts_as_unit_relevant(self):
        """mundane_unit_terrain_speed_cost affects units' movement, so it
        counts too even though it's not a combat stat."""
        assert nr.race_trait_is_war_relevant("positive_trait", "Airborne") is True

    def test_none_trait_is_not_war_relevant(self):
        assert nr.race_trait_is_war_relevant("positive_trait", "None") is False
        assert nr.race_trait_is_war_relevant("positive_trait", None) is False

    def test_rowdy_at_united_is_not_a_false_positive_for_unit(self):
        """Regression: Rowdy's only modifier is
        nation_stability_loss_chance_at_united — a naive substring search
        for "unit" wrongly matches "...at_UNITed". Must not be flagged."""
        assert nr.race_trait_is_war_relevant("negative_trait", "Rowdy") is False

    def test_swift_diplomatic_range_is_not_a_false_positive_for_range(self):
        """Regression guard: "range" is deliberately excluded from the
        keyword list because it would wrongly match Swift's unrelated
        nation_diplomatic_range. Swift must still be True overall, but via
        its genuine nation_unit_speed modifier, not diplomatic_range."""
        races_schema = category_data["races"]["schema"]
        swift_law = races_schema["properties"]["positive_trait"]["laws"]["Swift"]
        assert "nation_diplomatic_range" in swift_law  # sanity: the trap is real
        assert nr.race_trait_is_war_relevant("positive_trait", "Swift") is True
