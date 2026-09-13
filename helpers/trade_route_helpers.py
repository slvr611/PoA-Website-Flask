import math
import heapq
from collections import deque
from bson import ObjectId
from app_core import mongo, category_data, json_data
from helpers.hex_map_helpers import AXIAL_DIRECTIONS

_TERRAIN_IMPASSABLE = 9999


def _terrain_move_costs():
    """Return {terrain_key: land_cost} from terrains.json, impassable for water."""
    out = {}
    for key, data in json_data.get("terrains", {}).items():
        sc = data.get("speed_cost")
        out[key] = int(sc) if sc else _TERRAIN_IMPASSABLE
    return out


def _nation_move_costs(nation_doc):
    """Return move_costs adjusted by a nation's terrain_trade_move_cost modifiers.

    Reads {terrain}_trade_move_cost keys from the nation's overall_total_modifiers
    and adds them to the base terrain costs. Impassable terrains are never made
    passable, and passable terrain costs are floored at 1.
    """
    base = _terrain_move_costs()
    if not nation_doc:
        return base
    otm = nation_doc.get("overall_total_modifiers") or {}
    if not otm:
        return base
    adjusted = dict(base)
    for terrain_key, base_cost in list(base.items()):
        delta = otm.get(f"{terrain_key}_trade_move_cost", 0)
        if delta and base_cost < _TERRAIN_IMPASSABLE:
            adjusted[terrain_key] = max(1, base_cost + delta)
    return adjusted


# ---------------------------------------------------------------------------
# Road-path Dijkstra (city-to-city, terrain-weighted)
# ---------------------------------------------------------------------------

def _build_portal_map():
    """Return a dict mapping each portal tile (q,r) to its paired portal tile.

    Portal tiles of the same color are paired: entering one lets you exit at the
    other. Only portals with exactly 2 tiles of the same color form a valid pair.
    """
    portal_tiles = list(mongo.db.hex_map_tiles.find(
        {"portal": {"$exists": True, "$ne": None}},
        {"q": 1, "r": 1, "portal": 1, "_id": 0},
    ))
    by_color = {}
    for t in portal_tiles:
        color = (t.get("portal") or {}).get("color", "")
        if color:
            by_color.setdefault(color, []).append((t["q"], t["r"]))

    portal_map = {}
    for color, positions in by_color.items():
        if len(positions) == 2:
            a, b = positions[0], positions[1]
            portal_map[a] = b
            portal_map[b] = a
    return portal_map


def _get_trade_source_wonder_ids():
    """Return the set of wonder IDs (as strings) that have acts_as_trade_source=True."""
    return {
        str(w["_id"])
        for w in mongo.db.wonders.find(
            {"acts_as_trade_source": True}, {"_id": 1}
        )
    }


def _load_trade_tiles(nation_names_list):
    """Fetch all tiles relevant to trade-distance computation: owned + roads + portals + cities/trade wonders."""
    return list(mongo.db.hex_map_tiles.find(
        {"$or": [
            {"owner": {"$in": nation_names_list}},
            {"route": {"$exists": True, "$ne": None}},
            {"portal": {"$exists": True, "$ne": None}},
            {"city": {"$exists": True, "$ne": None}},
            {"wonder": {"$exists": True, "$ne": None}},
        ]},
        {"q": 1, "r": 1, "owner": 1, "route": 1, "portal": 1, "terrain": 1, "city": 1, "wonder": 1, "_id": 0},
    ))


def _tile_step_cost(tile_data, move_costs, trade_wonder_ids=None):
    """Cost to enter a tile. Road tiles and cities always cost 1 (both make traversal easy),
    regardless of which nation owns them — a city's internal infrastructure connects out to
    the road network same as a road tile would."""
    if tile_data.get("route") or _is_trade_city(tile_data, trade_wonder_ids or set()):
        return 1
    terrain = tile_data.get("terrain", "plains")
    cost = move_costs.get(terrain, 1)
    return cost if cost < _TERRAIN_IMPASSABLE else None  # None = impassable


def _is_trade_city(tile, trade_wonder_ids):
    """Return True if the tile counts as a city for trade-distance purposes.

    A tile qualifies if it has a city building, or if it has a wonder whose
    ID is in trade_wonder_ids (i.e. the wonder has acts_as_trade_source=True).
    """
    if tile.get("city"):
        return True
    wonder = tile.get("wonder")
    if wonder and trade_wonder_ids:
        wid = str(wonder.get("id", ""))
        return bool(wid and wid in trade_wonder_ids)
    return False


def _merchant_home_nation_name(merchant_doc):
    """Return the name of the nation a merchant's `location` points to, or
    None if unset/unresolvable."""
    if not merchant_doc:
        return None
    location = merchant_doc.get("location")
    if not location:
        return None
    try:
        nation = mongo.db.nations.find_one({"_id": ObjectId(str(location))}, {"name": 1})
    except Exception:
        nation = None
    return nation.get("name") if nation else None


def _city_position_by_id(tile_map, city_id):
    """Look up a city's (q, r) position by its city.id within an already-
    loaded tile_map. Returns None if not found or not actually a city tile."""
    if not city_id:
        return None
    for pos, t in tile_map.items():
        city = t.get("city")
        if isinstance(city, dict) and city.get("id") == city_id:
            return pos
    return None


def _additional_trade_city_ids(merchant):
    """Return the list of city ids granted by this merchant's own
    "additional_trade_city" modifiers (see json-data/modifier_types.json) —
    lets a merchant be based in more than one city at once, including
    cities in other nations. Mirrors collect_visibility_modifiers' approach
    of reading a merchant's `modifiers` array directly rather than routing
    through sum_modifier_totals, since this is set-membership, not a total."""
    return [
        m.get("city") for m in (merchant or {}).get("modifiers", [])
        if m.get("modifier_type") == "additional_trade_city" and m.get("city")
    ]


def _party_position_set(party_type, party_name, tile_map, trade_wonder_ids):
    """Return (position_set, home_nation_name) for a trade party.

    Nations: their owned city tiles (fallback: all owned tiles); home_nation_name
    is just their own name.

    Merchants: the tile at current_city_id if set and it actually resolves to
    a city, UNIONED with every city granted by an "additional_trade_city"
    modifier (each independently resolved the same way — these can belong to
    any nation, not just the merchant's home one). If current_city_id isn't
    set/valid and there are no additional cities either, falls back to
    behaving like a plain member of the home nation (location) — same
    city-or-all-owned-tiles logic a nation gets. home_nation_name is always
    the merchant's home nation (used so a merchant's home territory stays
    freely traversable, the same benefit a nation's own owned tiles get —
    note this bonus applies only to the primary home nation, not to whatever
    nations own the merchant's additional cities), or None if unresolvable.
    """
    if party_type == "merchant":
        merchant = mongo.db.merchants.find_one(
            {"name": party_name}, {"current_city_id": 1, "location": 1, "modifiers": 1}
        )
        home_nation = _merchant_home_nation_name(merchant)

        positions = set()
        primary_pos = _city_position_by_id(tile_map, (merchant or {}).get("current_city_id"))
        if primary_pos is not None:
            positions.add(primary_pos)
        for extra_city_id in _additional_trade_city_ids(merchant):
            extra_pos = _city_position_by_id(tile_map, extra_city_id)
            if extra_pos is not None:
                positions.add(extra_pos)

        if positions:
            return positions, home_nation
        # No valid specific city at all — behave like a regular member of the home nation.
        if not home_nation:
            return set(), None
        party_type, party_name = "nation", home_nation

    cities = {pos for pos, t in tile_map.items() if t.get("owner") == party_name and _is_trade_city(t, trade_wonder_ids)}
    positions = cities or {pos for pos, t in tile_map.items() if t.get("owner") == party_name}
    return positions, party_name


def _dijkstra_from_parties(source_type, source_name, target_parties, tiles_raw, portal_map, move_costs, trade_wonder_ids=None):
    """Multi-source, multi-target Dijkstra generalizing city-to-city distance
    to support merchant companies (positioned at one specific city) alongside
    nations (positioned at any of their owned cities).

    target_parties: list of (party_type, party_name) tuples.
    Traversable: road tiles, portal tiles, city tiles, and any tile owned by
    the source's or a target's home nation (a merchant's home nation grants
    the same free-traversal benefit its own owned tiles would).

    Returns {(party_type, party_name): cost} for each reachable target.
    """
    if trade_wonder_ids is None:
        trade_wonder_ids = set()

    tile_map = {(t["q"], t["r"]): t for t in tiles_raw}

    src_pos, src_home = _party_position_set(source_type, source_name, tile_map, trade_wonder_ids)
    if not src_pos:
        return {}

    tgt_positions = {}
    home_nations = set()
    if src_home:
        home_nations.add(src_home)
    for party in target_parties:
        positions, home = _party_position_set(party[0], party[1], tile_map, trade_wonder_ids)
        tgt_positions[party] = positions
        if home:
            home_nations.add(home)

    traversable = {
        pos for pos, t in tile_map.items()
        if t.get("owner", "") in home_nations or t.get("route") or t.get("portal")
        or _is_trade_city(t, trade_wonder_ids)
    }

    dist = {}
    heap = []
    for pos in src_pos:
        if pos in traversable:
            dist[pos] = 0
            heapq.heappush(heap, (0, pos[0], pos[1]))

    reached = {}
    remaining = {party for party in target_parties if tgt_positions[party]}

    while heap and remaining:
        cur_cost, q, r = heapq.heappop(heap)
        pos = (q, r)
        if cur_cost > dist.get(pos, float("inf")):
            continue

        for party in list(remaining):
            if pos in tgt_positions[party]:
                reached[party] = cur_cost
                remaining.discard(party)

        paired = portal_map.get(pos)
        if paired and paired not in dist:
            dist[paired] = cur_cost
            heapq.heappush(heap, (cur_cost, paired[0], paired[1]))

        for dq, dr in AXIAL_DIRECTIONS:
            nb = (q + dq, r + dr)
            if nb not in traversable:
                continue
            nb_data = tile_map.get(nb)
            if nb_data is None:
                continue
            step = _tile_step_cost(nb_data, move_costs, trade_wonder_ids)
            if step is None:
                continue
            new_cost = cur_cost + step
            if new_cost < dist.get(nb, float("inf")):
                dist[nb] = new_cost
                heapq.heappush(heap, (new_cost, nb[0], nb[1]))

    return reached


def _dijkstra_from_cities(source_nation, target_nations, tiles_raw, portal_map, move_costs, trade_wonder_ids=None):
    """Nation-only convenience wrapper over _dijkstra_from_parties, kept for
    existing callers (get_road_path_distance, get_all_trade_distances,
    get_connectable_nations) that only ever deal with nations.

    Returns {nation_name: cost} for each reachable target — same shape as
    before this function delegated to the generalized implementation.
    """
    reached = _dijkstra_from_parties(
        "nation", source_nation,
        [("nation", n) for n in target_nations],
        tiles_raw, portal_map, move_costs, trade_wonder_ids,
    )
    return {name: cost for (_, name), cost in reached.items()}


def _trade_tile_load_names(parties):
    """Expand a list of (party_type, party_name) into every nation name whose
    owned tiles _load_trade_tiles needs to fetch — each nation party's own
    name, plus each merchant party's home nation (so its home territory is
    available for the free-traversal bonus _party_position_set grants it).
    City/route/portal/wonder tiles are always fetched unconditionally by
    _load_trade_tiles regardless of this list, so a merchant's own specific
    city tile is covered either way."""
    names = set()
    for party_type, party_name in parties:
        if party_type == "merchant":
            merchant = mongo.db.merchants.find_one({"name": party_name}, {"location": 1})
            home = _merchant_home_nation_name(merchant)
            if home:
                names.add(home)
        else:
            names.add(party_name)
    return list(names)


def _move_cost_basis_doc(party_type, party_name):
    """Return the document _nation_move_costs should read overall_total_modifiers
    from for this party. Merchants don't carry their own terrain-cost
    modifiers today, so this naturally falls back to unmodified base costs
    for them (_nation_move_costs handles a doc with no overall_total_modifiers
    the same as no doc at all)."""
    collection = mongo.db.merchants if party_type == "merchant" else mongo.db.nations
    return collection.find_one({"name": party_name}, {"overall_total_modifiers": 1})


def get_road_path_distance(nation_a_name, nation_b_name, party_a_type="nation", party_b_type="nation"):
    """Terrain-weighted Dijkstra from party_a's position(s) to party_b's.

    Road tiles always cost 1; other tiles use terrain speed_cost.
    Portal tiles provide free jumps to their paired portal.

    party_a_type/party_b_type: "nation" (default, full backward compatible) or
    "merchant" — a merchant party is positioned at its specific current city
    (see _party_position_set) rather than a whole nation's city network.

    Returns (cost, connected: bool).
    """
    if nation_a_name == nation_b_name and party_a_type == party_b_type:
        return 0, True

    move_cost_doc    = _move_cost_basis_doc(party_a_type, nation_a_name)
    tiles_raw        = _load_trade_tiles(_trade_tile_load_names(
        [(party_a_type, nation_a_name), (party_b_type, nation_b_name)]
    ))
    portal_map       = _build_portal_map()
    move_costs       = _nation_move_costs(move_cost_doc)
    trade_wonder_ids = _get_trade_source_wonder_ids()

    reached = _dijkstra_from_parties(
        party_a_type, nation_a_name, [(party_b_type, nation_b_name)],
        tiles_raw, portal_map, move_costs, trade_wonder_ids=trade_wonder_ids,
    )
    cost = reached.get((party_b_type, nation_b_name))
    return (cost, True) if cost is not None else (None, False)


def get_all_trade_distances(nation_name):
    """Return trade distances from nation_name to all other nations.

    Returns {other_nation_name: {cost, delay, connectable}} using the
    nation's trade_speed for delay computation.
    """
    source = mongo.db.nations.find_one({"name": nation_name}, {"trade_speed": 1, "overall_total_modifiers": 1})
    if not source:
        return {}
    src_speed = source.get("trade_speed") or 7

    all_nations = list(mongo.db.nations.find(
        {"name": {"$ne": nation_name}},
        {"name": 1, "trade_speed": 1, "_id": 0},
    ))
    if not all_nations:
        return {}

    target_names     = [n["name"] for n in all_nations]
    tiles_raw        = _load_trade_tiles([nation_name] + target_names)
    portal_map       = _build_portal_map()
    move_costs       = _nation_move_costs(source)
    trade_wonder_ids = _get_trade_source_wonder_ids()

    reached = _dijkstra_from_cities(
        nation_name, target_names, tiles_raw, portal_map, move_costs,
        trade_wonder_ids=trade_wonder_ids,
    )

    result = {}
    for n in all_nations:
        name  = n["name"]
        cost  = reached.get(name)
        if cost is not None:
            delay = compute_delay(cost, src_speed, n.get("trade_speed") or 7)
            result[name] = {"cost": cost, "delay": delay, "connectable": True}
        else:
            result[name] = {"cost": None, "delay": None, "connectable": False}
    return result


def compute_delay(road_distance, trade_speed_a, trade_speed_b):
    """Return the transit delay tier for a given road distance."""
    max_speed = max(trade_speed_a or 1, trade_speed_b or 1)
    return math.floor(road_distance / max_speed)


# ---------------------------------------------------------------------------
# Current-session helper
# ---------------------------------------------------------------------------

def _current_session():
    gm = mongo.db.global_modifiers.find_one(
        {"name": "global_modifiers"}, {"session_counter": 1}
    )
    return gm.get("session_counter", 0) if gm else 0


def first_delivery_session(route):
    acc = route.get("accepted_session")
    if acc is None:
        return None
    delay = route.get("delay", 0)
    # delay 0 and delay 1 both deliver on the very next tick (field calcs run at
    # session=acc before the session counter commits to acc+1); higher delays add
    # one session per tier above 1.
    return acc + max(0, delay - 1)


def last_delivery_session(route):
    """Last session on which resources should be delivered.

    Returns None if the route is indefinite and not yet cancelled.
    """
    fds = first_delivery_session(route)
    if fds is None:
        return None

    duration = route.get("duration_ticks")
    natural_last = (fds + duration - 1) if duration else None

    cancel_session = route.get("cancel_session")
    delay = route.get("delay", 0)
    cancel_last = (cancel_session + delay) if cancel_session is not None else None

    candidates = [x for x in (natural_last, cancel_last) if x is not None]
    return min(candidates) if candidates else None


def is_delivering(route, session):
    """True if the route should deliver resources this session."""
    if session in route.get("raided_sessions", []):
        # Lost to a bandit camp this session (tick_helpers.complex_trade_bandit_loss_tick) —
        # nets to zero for both sides, same as if the trade never happened.
        return False
    fds = first_delivery_session(route)
    if fds is None or session < fds:
        return False
    lds = last_delivery_session(route)
    if lds is not None and session > lds:
        return False
    return True


def _nations_in_stasis(names):
    """Return the subset of `names` currently carrying a stasis modifier.

    A route with either side in stasis stays active (not ended) but is treated
    as non-delivering — goods stop flowing without disturbing its lifecycle timing."""
    names = [n for n in names if n]
    if not names:
        return set()
    docs = mongo.db.nations.find(
        {"name": {"$in": names}, "modifiers.modifier_type": "stasis"},
        {"name": 1},
    )
    return {d["name"] for d in docs}


# ---------------------------------------------------------------------------
# Resource net for production/consumption calculations
# ---------------------------------------------------------------------------

def _get_cached_routes(target):
    """Return active+ending trade routes for this nation, cached on target._calc_cache."""
    cache = target.setdefault("_calc_cache", {})
    if "active_trade_routes" not in cache:
        name = target.get("name", "")
        if name:
            cache["active_trade_routes"] = list(mongo.db.trade_routes.find(
                {
                    "$or": [{"nation_a": name}, {"nation_b": name}],
                    "status": {"$in": ["active", "ending"]},
                }
            ))
        else:
            cache["active_trade_routes"] = []
    return cache["active_trade_routes"]


def get_trade_route_resource_net(nation_name, routes, session=None):
    """Compute {resource: net_qty} from a list of active/ending routes.

    Positive = net incoming (import), Negative = net outgoing (export).
    Only routes whose delivery window includes `session` are counted.
    """
    if session is None:
        session = _current_session()

    stasis_names = _nations_in_stasis(
        {r.get("nation_a") for r in routes} | {r.get("nation_b") for r in routes}
    )

    net = {}
    for route in routes:
        if not is_delivering(route, session):
            continue
        if route.get("nation_a") in stasis_names or route.get("nation_b") in stasis_names:
            continue  # route stays active but paused while either side is in stasis
        if route.get("nation_a") == nation_name:
            for entry in route.get("resources_a_to_b", []):
                r, q = entry["resource"], entry["quantity"]
                net[r] = net.get(r, 0) - q
            for entry in route.get("resources_b_to_a", []):
                r, q = entry["resource"], entry["quantity"]
                net[r] = net.get(r, 0) + q
        else:
            for entry in route.get("resources_b_to_a", []):
                r, q = entry["resource"], entry["quantity"]
                net[r] = net.get(r, 0) - q
            for entry in route.get("resources_a_to_b", []):
                r, q = entry["resource"], entry["quantity"]
                net[r] = net.get(r, 0) + q
    return net


# ---------------------------------------------------------------------------
# Breakdown SourceContributions for tooltips
# ---------------------------------------------------------------------------

def get_trade_route_source_contributions(nation_name, routes, session=None):
    """Return SourceContribution objects for trade route resource breakdowns.

    One contribution per (route, direction) pair.  Each carries the
    {resource_key}_production or {resource_key}_consumption modifiers
    that _resource_bd expects.
    """
    from calculations.source_contribution import SourceContribution

    if session is None:
        session = _current_session()

    stasis_names = _nations_in_stasis(
        {r.get("nation_a") for r in routes} | {r.get("nation_b") for r in routes}
    )

    contribs = []
    for route in routes:
        if not is_delivering(route, session):
            continue
        if route.get("nation_a") in stasis_names or route.get("nation_b") in stasis_names:
            continue  # route stays active but paused while either side is in stasis

        if route.get("nation_a") == nation_name:
            partner = route.get("nation_b", "?")
            outgoing = route.get("resources_a_to_b", [])
            incoming = route.get("resources_b_to_a", [])
        else:
            partner = route.get("nation_a", "?")
            outgoing = route.get("resources_b_to_a", [])
            incoming = route.get("resources_a_to_b", [])

        if outgoing:
            mods = {e["resource"] + "_consumption": e["quantity"] for e in outgoing}
            contribs.append(SourceContribution(
                label=f"Export to {partner}",
                source_type="trade_route",
                modifiers=mods,
            ))
        if incoming:
            mods = {e["resource"] + "_production": e["quantity"] for e in incoming}
            contribs.append(SourceContribution(
                label=f"Import from {partner}",
                source_type="trade_route",
                modifiers=mods,
            ))

    return contribs


# ---------------------------------------------------------------------------
# Slot counting
# ---------------------------------------------------------------------------

def _nations_share_market(nation_a, nation_b):
    """True if both nations are members of at least one common market.

    Trade routes store nation_a/nation_b as nation names, but market_links
    stores member as the nation's _id string. Resolve names → IDs first.
    """
    ml = category_data.get("market_links", {}).get("database")
    if ml is None:
        return False
    nations_db = category_data.get("nations", {}).get("database")
    if nations_db is None:
        return False

    def _nation_id(name):
        n = nations_db.find_one({"name": name}, {"_id": 1})
        return str(n["_id"]) if n else None

    a_id = _nation_id(nation_a)
    b_id = _nation_id(nation_b)
    if not a_id or not b_id:
        return False

    a_markets = {lnk["market"] for lnk in ml.find({"member": a_id}, {"market": 1})}
    if not a_markets:
        return False
    b_markets = {lnk["market"] for lnk in ml.find({"member": b_id}, {"market": 1})}
    return bool(a_markets & b_markets)


def _slot_cost_for_direction(resources, slot_capacity):
    """Total export/import slots consumed by a list of {resource, quantity} entries.

    Regular resources use slot_capacity units per slot.
    Money: each resource slot can carry $100 of money for free. Any remaining
    money beyond what resource slots cover costs $1000 per additional slot.
    Standalone money (no resources) also costs $1000 per slot.
    """
    resource_entries = [e for e in resources if e.get("quantity", 0) > 0 and e.get("resource") != "money"]
    money_entry = next((e for e in resources if e.get("resource") == "money" and e.get("quantity", 0) > 0), None)

    resource_slots = sum(math.ceil(e["quantity"] / slot_capacity) for e in resource_entries)

    if money_entry:
        money_amount = money_entry["quantity"]
        free_money = resource_slots * 100
        remaining_money = max(0, money_amount - free_money)
        money_slots = math.ceil(remaining_money / 1000) if remaining_money > 0 else 0
        return resource_slots + money_slots

    return resource_slots


def count_route_slots(nation_name, statuses=("active", "ending")):
    """Return (export_slots_used, import_slots_used) for this nation across all
    trade routes with the given statuses.

    Slot capacity: 4 per resource type if both nations share a market, else 2.
    """
    routes = list(mongo.db.trade_routes.find(
        {
            "$or": [{"nation_a": nation_name}, {"nation_b": nation_name}],
            "status": {"$in": list(statuses)},
        }
    ))

    export_used = 0
    import_used = 0
    market_cache = {}

    for route in routes:
        na, nb = route.get("nation_a", ""), route.get("nation_b", "")
        cache_key = (min(na, nb), max(na, nb))
        if cache_key not in market_cache:
            market_cache[cache_key] = _nations_share_market(na, nb)
        capacity = 4 if market_cache[cache_key] else 2

        if nation_name == na:
            export_used += _slot_cost_for_direction(route.get("resources_a_to_b", []), capacity)
            import_used += _slot_cost_for_direction(route.get("resources_b_to_a", []), capacity)
        else:
            export_used += _slot_cost_for_direction(route.get("resources_b_to_a", []), capacity)
            import_used += _slot_cost_for_direction(route.get("resources_a_to_b", []), capacity)

    return export_used, import_used


# ---------------------------------------------------------------------------
# Connectable nations (for propose form)
# ---------------------------------------------------------------------------

def get_connectable_nations(nation_name, nation_trade_speed):
    """Return list of nation dicts reachable by terrain-weighted Dijkstra from
    nation_name's cities, respecting road tile costs and portal jumps.
    """
    nation_doc = mongo.db.nations.find_one({"name": nation_name}, {"overall_total_modifiers": 1})

    candidate_nations = list(mongo.db.nations.find(
        {"name": {"$ne": nation_name}},
        {"name": 1, "trade_speed": 1, "_id": 0},
    ))
    if not candidate_nations:
        return []

    target_names     = [n["name"] for n in candidate_nations]
    tiles_raw        = _load_trade_tiles([nation_name] + target_names)
    portal_map       = _build_portal_map()
    move_costs       = _nation_move_costs(nation_doc)
    trade_wonder_ids = _get_trade_source_wonder_ids()

    reached = _dijkstra_from_cities(
        nation_name, target_names, tiles_raw, portal_map, move_costs,
        trade_wonder_ids=trade_wonder_ids,
    )

    results = []
    for n in candidate_nations:
        name  = n["name"]
        cost  = reached.get(name)
        if cost is not None:
            cspeed = n.get("trade_speed") or 7
            delay  = compute_delay(cost, nation_trade_speed, cspeed)
            results.append({"name": name, "road_distance": cost, "delay": delay})

    results.sort(key=lambda x: x["road_distance"])
    return results


def get_connectable_parties(party_type, party_name, party_trade_speed):
    """Like get_connectable_nations, but the candidate list includes every
    OTHER nation and every merchant company (positioned at its specific
    current city — see _party_position_set — or its home nation's city
    network if it has none set), and the source itself may be a merchant.

    Returns a list of {name, type, road_distance, delay} dicts, sorted by
    distance — `type` is "nation" or "merchant", for the caller to record on
    the trade route (nation_a_type/nation_b_type) and to route acceptance/
    ownership checks to the right collection.
    """
    move_cost_doc = _move_cost_basis_doc(party_type, party_name)

    candidate_nations = list(mongo.db.nations.find(
        {} if party_type != "nation" else {"name": {"$ne": party_name}},
        {"name": 1, "trade_speed": 1, "_id": 0},
    ))
    candidate_merchants = list(mongo.db.merchants.find(
        {} if party_type != "merchant" else {"name": {"$ne": party_name}},
        {"name": 1, "trade_speed": 1, "_id": 0},
    ))
    if not candidate_nations and not candidate_merchants:
        return []

    target_parties = (
        [("nation", n["name"]) for n in candidate_nations]
        + [("merchant", m["name"]) for m in candidate_merchants]
    )
    tiles_raw = _load_trade_tiles(_trade_tile_load_names(
        [(party_type, party_name)] + target_parties
    ))
    portal_map       = _build_portal_map()
    move_costs       = _nation_move_costs(move_cost_doc)
    trade_wonder_ids = _get_trade_source_wonder_ids()

    reached = _dijkstra_from_parties(
        party_type, party_name, target_parties, tiles_raw, portal_map, move_costs,
        trade_wonder_ids=trade_wonder_ids,
    )

    results = []
    for candidates, ptype in ((candidate_nations, "nation"), (candidate_merchants, "merchant")):
        for c in candidates:
            name = c["name"]
            cost = reached.get((ptype, name))
            if cost is not None:
                cspeed = c.get("trade_speed") or 7
                delay = compute_delay(cost, party_trade_speed, cspeed)
                results.append({"name": name, "type": ptype, "road_distance": cost, "delay": delay})

    results.sort(key=lambda x: x["road_distance"])
    return results


# ---------------------------------------------------------------------------
# Lifecycle tick helper
# ---------------------------------------------------------------------------

def run_trade_route_lifecycle(current_session):
    """Advance route statuses based on current session.

    - pending routes that have accepted_session set → already active (set by accept action)
    - active/ending routes past their last_delivery_session → mark ended
    Returns a log string.
    """
    log = []
    routes = list(mongo.db.trade_routes.find(
        {"status": {"$in": ["active", "ending"]}}
    ))

    for route in routes:
        lds = last_delivery_session(route)
        if lds is not None and current_session > lds:
            mongo.db.trade_routes.update_one(
                {"_id": route["_id"]},
                {"$set": {"status": "ended", "ended_session": current_session}},
            )
            log.append(
                f"Route {route['_id']} ({route.get('nation_a')} ↔ {route.get('nation_b')}) ended"
            )

    return "\n".join(log)
