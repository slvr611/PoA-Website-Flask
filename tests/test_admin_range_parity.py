"""
Parity guard between the two independent admin-range implementations:

  - helpers/hex_map_helpers.py's compute_admin_range_out_of_range() / route_cost_reduction()
    — the real calculation engine's source of truth (used by field_calculations.py to
    determine each nation's effective, in-range territory for resource production).
  - static/js/hex-map.js's client-side Dijkstra (TERRAIN_MOVE_COST + the route-tier
    reduction inlined in _computeAllAdminRanges) — used for the map's live
    out-of-range preview while painting.

Both already read terrain movement costs from json-data/terrains.json (Python
directly; JS via /api/hex-map/config, which serves the same file) and are
algorithmically identical by design, but they're two separate implementations
in two different languages — nothing stops them drifting apart the next time
someone edits one without the other. This suite parses hex-map.js's source
directly and cross-checks it against terrains.json / route_cost_reduction(),
so a future edit to either side that breaks parity fails CI immediately
instead of silently producing wrong in-game numbers (e.g. a nation's resource
production disagreeing with what the map visually shows as in/out of range).
"""
import re
from pathlib import Path

import pytest

from app_core import json_data
from helpers.hex_map_helpers import route_cost_reduction

_HEX_MAP_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "hex-map.js"


@pytest.fixture(scope="module")
def hex_map_js_source():
    return _HEX_MAP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_terrain_move_cost(hex_map_js_source):
    """Extract the TERRAIN_MOVE_COST seed object's {terrain: cost} pairs."""
    m = re.search(
        r"const TERRAIN_MOVE_COST\s*=\s*\{(.*?)\};",
        hex_map_js_source, re.DOTALL,
    )
    assert m, "Could not find `const TERRAIN_MOVE_COST = {...};` in hex-map.js — did it get renamed/restructured?"
    body = m.group(1)
    pairs = re.findall(r"(\w+)\s*:\s*(\d+)", body)
    assert pairs, "TERRAIN_MOVE_COST block matched but no key: number pairs were parsed out of it"
    return {key: int(value) for key, value in pairs}


def _expected_move_cost(terrain_data):
    """Mirrors both compute_admin_range_out_of_range()'s move_cost dict comprehension
    and loadConfig()'s `v.speed_cost || v.naval_speed_cost` rule: land cost if set,
    else naval cost, else impassable (terrain excluded from the JS table entirely)."""
    return terrain_data.get("speed_cost") or terrain_data.get("naval_speed_cost")


class TestTerrainMoveCostParity:
    def test_js_seed_matches_terrains_json_for_every_passable_terrain(self, js_terrain_move_cost):
        """Every terrain with a real (non-impassable) movement cost in terrains.json
        must have the exact same cost hardcoded as hex-map.js's TERRAIN_MOVE_COST seed —
        this is only a fallback ahead of loadConfig() overwriting it, but if it silently
        drifts, anyone loading the map before that fetch resolves briefly sees wrong data,
        and a mismatch here is a strong signal terrains.json changed without hex-map.js's
        comment-documented seed being updated to match."""
        mismatches = []
        for terrain, data in json_data.get("terrains", {}).items():
            expected = _expected_move_cost(data)
            if not expected:
                continue  # impassable terrain (e.g. disconnected) — JS correctly omits it
            actual = js_terrain_move_cost.get(terrain)
            if actual != expected:
                mismatches.append((terrain, expected, actual))
        assert not mismatches, (
            "hex-map.js's TERRAIN_MOVE_COST seed is out of sync with terrains.json "
            f"(terrain, expected_from_terrains_json, actual_in_js): {mismatches}"
        )

    def test_no_stale_js_entries_for_terrains_removed_or_made_impassable(self, js_terrain_move_cost):
        """Catches the opposite drift direction: a terrain hardcoded in JS that no
        longer exists in terrains.json, or that lost its speed_cost/naval_speed_cost
        and should now be impassable there too."""
        stale = [
            terrain for terrain in js_terrain_move_cost
            if not _expected_move_cost(json_data.get("terrains", {}).get(terrain, {}))
        ]
        assert not stale, (
            f"hex-map.js's TERRAIN_MOVE_COST hardcodes a cost for terrain(s) {stale}, "
            "but terrains.json no longer gives them a speed_cost/naval_speed_cost"
        )


class TestRouteCostReductionParity:
    def test_js_route_tier_reduction_matches_python(self, hex_map_js_source):
        """route_cost_reduction() (Python) says tier 3 → -2, tier 2 → -1, min cost 1.
        hex-map.js inlines the same rule (twice — once for traversal, once for the
        destination tile's own entry cost) rather than calling a shared function, so
        assert its literal constants still match."""
        js_reduction_lines = re.findall(
            r"routeTier\s*===\s*(\d)\)\s*(?:cost|tileCost)\s*=\s*Math\.max\(1,\s*(?:cost|tileCost)\s*-\s*(\d)\)",
            hex_map_js_source,
        )
        assert js_reduction_lines, (
            "Could not find the `routeTier === N) cost = Math.max(1, cost - M)` pattern "
            "in hex-map.js — did the route-tier reduction logic get rewritten?"
        )
        js_reductions = {int(tier): int(amount) for tier, amount in js_reduction_lines}

        py_reductions = {
            tier: route_cost_reduction({"route": {"tier": tier}})
            for tier in (2, 3)
        }

        for tier, py_amount in py_reductions.items():
            assert js_reductions.get(tier) == py_amount, (
                f"Route tier {tier} reduction mismatch: Python route_cost_reduction() "
                f"gives {py_amount}, hex-map.js hardcodes {js_reductions.get(tier)}"
            )

    def test_no_reduction_for_tier_0_or_1(self):
        assert route_cost_reduction({"route": {"tier": 0}}) == 0
        assert route_cost_reduction({"route": {"tier": 1}}) == 0
        assert route_cost_reduction({}) == 0
        assert route_cost_reduction(None) == 0


class TestAdminRangeLimitFormulaParity:
    def test_js_limit_formula_matches_python(self, hex_map_js_source):
        """Both sides compute the movement-cost budget as admin * 4 (non-nomadic)
        or admin * 8 (nomadic) — this pins the two literal multipliers so they
        can't drift independently of compute_admin_range_out_of_range()'s
        `limit = admin * (8 if nomadic else 4)`."""
        m = re.search(r"admin\s*\*\s*\(isNomadic\s*\?\s*(\d+)\s*:\s*(\d+)\)", hex_map_js_source)
        assert m, "Could not find the `admin * (isNomadic ? N : M)` limit formula in hex-map.js"
        nomadic_mult, non_nomadic_mult = int(m.group(1)), int(m.group(2))
        assert (non_nomadic_mult, nomadic_mult) == (4, 8), (
            f"hex-map.js's admin-range limit multipliers are ({non_nomadic_mult}, {nomadic_mult}) "
            "(non-nomadic, nomadic) — expected (4, 8) to match "
            "compute_admin_range_out_of_range()'s `admin * (8 if nomadic else 4)`"
        )
