import math
from collections import deque
from app_core import mongo, category_data
from helpers.hex_map_helpers import AXIAL_DIRECTIONS


# ---------------------------------------------------------------------------
# Road-path BFS
# ---------------------------------------------------------------------------

def get_road_path_distance(nation_a_name, nation_b_name):
    """BFS through road tiles to find the shortest path between two nations.

    Traversable: any tile with a route{} object, plus all tiles owned by either
    nation.  Source: tiles owned by nation_a.  Target: tiles owned by nation_b.

    Returns (distance_in_tiles, connected: bool).
    """
    if nation_a_name == nation_b_name:
        return 0, True

    tiles_raw = list(mongo.db.hex_map_tiles.find(
        {"$or": [
            {"owner": nation_a_name},
            {"owner": nation_b_name},
            {"route": {"$exists": True, "$ne": None}},
        ]},
        {"q": 1, "r": 1, "owner": 1, "route": 1, "_id": 0},
    ))

    a_tiles = set()
    b_tiles = set()
    traversable = set()

    for t in tiles_raw:
        pos = (t["q"], t["r"])
        owner = t.get("owner", "")
        if owner == nation_a_name:
            a_tiles.add(pos)
            traversable.add(pos)
        if owner == nation_b_name:
            b_tiles.add(pos)
            traversable.add(pos)
        if t.get("route"):
            traversable.add(pos)

    if not a_tiles or not b_tiles:
        return None, False

    visited = {}
    queue = deque()
    for pos in a_tiles:
        if pos not in visited:
            visited[pos] = 0
            queue.append((pos, 0))

    while queue:
        (q, r), dist = queue.popleft()
        if (q, r) in b_tiles:
            return dist, True
        for dq, dr in AXIAL_DIRECTIONS:
            nb = (q + dq, r + dr)
            if nb in traversable and nb not in visited:
                visited[nb] = dist + 1
                queue.append((nb, dist + 1))

    return None, False


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
    return acc + max(1, delay)


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
    """True if both nations are members of at least one common market."""
    ml = category_data.get("market_links", {}).get("database")
    if ml is None:
        return False
    a_markets = {lnk["market"] for lnk in ml.find({"member": nation_a}, {"market": 1})}
    b_markets = {lnk["market"] for lnk in ml.find({"member": nation_b}, {"market": 1})}
    return bool(a_markets & b_markets)


def _slot_cost_for_direction(resources, slot_capacity):
    """Total export/import slots consumed by a list of {resource, quantity} entries."""
    return sum(math.ceil(e["quantity"] / slot_capacity) for e in resources if e.get("quantity", 0) > 0)


def count_route_slots(nation_name, statuses=("active", "ending", "pending")):
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
    """Return list of nation dicts that have a road path to this nation.

    Limits candidates by hex proximity first (3 × trade_speed) before BFS.
    """
    from helpers.hex_map_helpers import hex_distance

    my_tiles = list(mongo.db.hex_map_tiles.find(
        {"owner": nation_name}, {"q": 1, "r": 1, "_id": 0}
    ))
    if not my_tiles:
        return []

    # Rough bounding-box centre of this nation's territory
    avg_q = sum(t["q"] for t in my_tiles) // max(len(my_tiles), 1)
    avg_r = sum(t["r"] for t in my_tiles) // max(len(my_tiles), 1)

    max_hex_radius = max(3 * (nation_trade_speed or 7), 30)

    candidate_nations = list(mongo.db.nations.find(
        {"name": {"$ne": nation_name}},
        {"name": 1, "trade_speed": 1, "_id": 0},
    ))

    results = []
    for candidate in candidate_nations:
        cname = candidate["name"]
        ctiles = list(mongo.db.hex_map_tiles.find(
            {"owner": cname}, {"q": 1, "r": 1, "_id": 0}
        ))
        if not ctiles:
            continue
        # Quick proximity check against centroid
        cavg_q = sum(t["q"] for t in ctiles) // max(len(ctiles), 1)
        cavg_r = sum(t["r"] for t in ctiles) // max(len(ctiles), 1)
        if hex_distance(avg_q, avg_r, cavg_q, cavg_r) > max_hex_radius:
            continue

        dist, connected = get_road_path_distance(nation_name, cname)
        if not connected:
            continue

        cspeed = candidate.get("trade_speed") or 7
        delay = compute_delay(dist, nation_trade_speed, cspeed)
        results.append({
            "name": cname,
            "road_distance": dist,
            "delay": delay,
        })

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
