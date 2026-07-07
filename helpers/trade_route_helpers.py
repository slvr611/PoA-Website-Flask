import math
import heapq
from collections import deque
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
    """Fetch all tiles relevant to trade-distance computation: owned + roads + portals."""
    return list(mongo.db.hex_map_tiles.find(
        {"$or": [
            {"owner": {"$in": nation_names_list}},
            {"route": {"$exists": True, "$ne": None}},
            {"portal": {"$exists": True, "$ne": None}},
        ]},
        {"q": 1, "r": 1, "owner": 1, "route": 1, "portal": 1, "terrain": 1, "city": 1, "wonder": 1, "_id": 0},
    ))


def _tile_step_cost(tile_data, move_costs):
    """Cost to enter a tile. Road tiles always cost 1 (roads make traversal easy)."""
    if tile_data.get("route"):
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


def _dijkstra_from_cities(source_nation, target_nations, tiles_raw, portal_map, move_costs, trade_wonder_ids=None):
    """Multi-source Dijkstra from source_nation's cities toward target cities.

    Sources: city tiles of source_nation (fallback: all owned tiles).
    Targets: city tiles of each target nation (fallback: all owned tiles).
    Traversable: road tiles, tiles owned by source/targets, portal tiles.

    trade_wonder_ids: set of wonder ID strings whose tiles count as city positions.

    Returns {nation_name: cost} for each reachable target.
    """
    if trade_wonder_ids is None:
        trade_wonder_ids = set()
    target_set = set(target_nations) | {source_nation}

    tile_map = {(t["q"], t["r"]): t for t in tiles_raw}

    src_cities  = {pos for pos, t in tile_map.items() if t.get("owner") == source_nation and _is_trade_city(t, trade_wonder_ids)}
    src_pos     = src_cities or {pos for pos, t in tile_map.items() if t.get("owner") == source_nation}

    tgt_cities, tgt_all = {}, {}
    for nation in target_nations:
        tgt_cities[nation] = {pos for pos, t in tile_map.items() if t.get("owner") == nation and _is_trade_city(t, trade_wonder_ids)}
        tgt_all[nation]    = {pos for pos, t in tile_map.items() if t.get("owner") == nation}

    def targets_for(n):
        return tgt_cities[n] or tgt_all[n]

    if not src_pos:
        return {}

    traversable = {
        pos for pos, t in tile_map.items()
        if t.get("owner", "") in target_set or t.get("route") or t.get("portal")
    }

    dist = {}
    heap = []
    for pos in src_pos:
        if pos in traversable:
            dist[pos] = 0
            heapq.heappush(heap, (0, pos[0], pos[1]))

    reached = {}
    remaining = set(target_nations)

    while heap and remaining:
        cur_cost, q, r = heapq.heappop(heap)
        pos = (q, r)
        if cur_cost > dist.get(pos, float("inf")):
            continue

        for nation in list(remaining):
            if pos in targets_for(nation):
                reached[nation] = cur_cost
                remaining.discard(nation)

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
            step = _tile_step_cost(nb_data, move_costs)
            if step is None:
                continue
            new_cost = cur_cost + step
            if new_cost < dist.get(nb, float("inf")):
                dist[nb] = new_cost
                heapq.heappush(heap, (new_cost, nb[0], nb[1]))

    return reached


def get_road_path_distance(nation_a_name, nation_b_name):
    """Terrain-weighted Dijkstra from nation_a's cities to nation_b's cities.

    Road tiles always cost 1; other tiles use terrain speed_cost.
    Portal tiles provide free jumps to their paired portal.

    Returns (cost, connected: bool).
    """
    if nation_a_name == nation_b_name:
        return 0, True

    nation_a_doc     = mongo.db.nations.find_one({"name": nation_a_name}, {"overall_total_modifiers": 1})
    tiles_raw        = _load_trade_tiles([nation_a_name, nation_b_name])
    portal_map       = _build_portal_map()
    move_costs       = _nation_move_costs(nation_a_doc)
    trade_wonder_ids = _get_trade_source_wonder_ids()

    reached = _dijkstra_from_cities(
        nation_a_name, [nation_b_name], tiles_raw, portal_map, move_costs,
        trade_wonder_ids=trade_wonder_ids,
    )
    cost = reached.get(nation_b_name)
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
    fds = first_delivery_session(route)
    if fds is None or session < fds:
        return False
    lds = last_delivery_session(route)
    if lds is not None and session > lds:
        return False
    return True


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

    net = {}
    for route in routes:
        if not is_delivering(route, session):
            continue
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

    contribs = []
    for route in routes:
        if not is_delivering(route, session):
            continue

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
