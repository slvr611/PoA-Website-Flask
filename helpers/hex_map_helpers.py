from app_core import mongo, category_data, json_data
from bson import ObjectId
import datetime
import heapq
import threading
from collections import deque

AXIAL_DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]

# Fallback cache for compute_admin_range_out_of_range's full-tile-table fetch
# when running outside a Flask request context (e.g. the session tick, which
# runs in a plain background Thread — flask.g raises RuntimeError there, so
# the request-scoped cache below silently never engages and re-queries the
# entire map on every nation). Each tick spawns a fresh Thread, so this is
# naturally scoped to one tick run with no stale-data risk across ticks.
_admin_tile_cache_local = threading.local()


def _get_cached_all_tiles():
    """Full hex_map_tiles snapshot, cached per Flask request when available
    and per background thread otherwise. See compute_admin_range_out_of_range.

    Reads via get_all_tiles_from_chunks's ~100-document hex_map_tile_chunks
    mirror rather than scanning all ~11,500 tile documents directly — a
    direct scan measured at ~8.6s (per-document/round-trip overhead, not
    payload size — cutting the projection to 2 fields still only got it to
    ~3s). The mirror self-heals on any tile_version mismatch, so this stays
    correct even if some write path's chunk-sync was missed.
    """
    try:
        from flask import g as _g
        cached = getattr(_g, '_hex_admin_tile_cache', None)
        if cached is not None:
            return cached
        tiles = get_all_tiles_from_chunks()
        _g._hex_admin_tile_cache = tiles
        return tiles
    except RuntimeError:
        pass  # no Flask app/request context — fall through to thread-local cache

    cached = getattr(_admin_tile_cache_local, 'tiles', None)
    if cached is not None:
        return cached
    tiles = get_all_tiles_from_chunks()
    _admin_tile_cache_local.tiles = tiles
    return tiles


def hex_distance(q1, r1, q2, r2):
    """Axial hex grid distance (equivalent to cube coordinate Chebyshev distance)."""
    dq, dr = q2 - q1, r2 - r1
    return max(abs(dq), abs(dr), abs(dq + dr))


def get_node_resource_positions(resource_type):
    """Return list of (q, r) for all tiles whose node produces resource_type."""
    tiles = mongo.db.hex_map_tiles.find(
        {"node.resource_type": resource_type}, {"q": 1, "r": 1, "_id": 0}
    )
    return [(t["q"], t["r"]) for t in tiles]


def is_within_distance_of_node_resource(q, r, resource_type, max_distance, node_positions=None):
    """Return True if (q, r) is within max_distance hexes of any node producing resource_type.

    If node_positions is pre-fetched pass it to avoid a redundant DB query.
    Returns True (unrestricted) when no nodes of that type exist on the map.
    """
    if node_positions is None:
        node_positions = get_node_resource_positions(resource_type)
    if not node_positions:
        return True
    return any(hex_distance(q, r, nq, nr) <= max_distance for nq, nr in node_positions)


def get_nation_node_proximity_restrictions(nation_name):
    """Return [(resource_type, max_distance)] territory restrictions for the nation's race trait.

    Reads the race's negative_trait laws from the JSON schema looking for keys of the
    form nation_territory_max_{resource}_node_distance.
    """
    restrictions = []
    nation = mongo.db.nations.find_one({"name": nation_name}, {"primary_race": 1, "_id": 0})
    if not nation or not nation.get("primary_race"):
        return restrictions
    try:
        race = mongo.db.races.find_one(
            {"_id": ObjectId(nation["primary_race"])}, {"negative_trait": 1, "_id": 0}
        )
    except Exception:
        return restrictions
    if not race or not race.get("negative_trait"):
        return restrictions
    trait = race["negative_trait"]
    race_schema = category_data["races"]["schema"]
    trait_laws = race_schema.get("properties", {}).get("negative_trait", {}).get("laws", {})
    laws = trait_laws.get(trait, {})
    prefix = "nation_territory_max_"
    suffix = "_node_distance"
    for key, value in laws.items():
        if key.startswith(prefix) and key.endswith(suffix):
            resource = key[len(prefix):-len(suffix)]
            try:
                restrictions.append((resource, int(value)))
            except (ValueError, TypeError):
                pass
    return restrictions


def name_to_color(name):
    """Deterministically generate a map-suitable hex color from a name string."""
    hue_hash = 0
    for c in name:
        hue_hash = (hue_hash * 31 + ord(c)) & 0xFFFF
    hue = hue_hash % 360
    s, l = 0.60, 0.52
    c_val = (1 - abs(2 * l - 1)) * s
    x = c_val * (1 - abs((hue / 60.0) % 2 - 1))
    m = l - c_val / 2
    if hue < 60:   r, gv, b = c_val, x,     0
    elif hue < 120: r, gv, b = x,     c_val, 0
    elif hue < 180: r, gv, b = 0,     c_val, x
    elif hue < 240: r, gv, b = 0,     x,     c_val
    elif hue < 300: r, gv, b = x,     0,     c_val
    else:           r, gv, b = c_val, 0,     x
    return f"#{int((r+m)*255):02x}{int((gv+m)*255):02x}{int((b+m)*255):02x}"


def axial_neighbors(q, r):
    return [(q + dq, r + dr) for dq, dr in AXIAL_DIRECTIONS]


def _coerce_tile(tile):
    """Recursively convert any ObjectId values to strings so tiles are always JSON-safe."""
    from bson import ObjectId
    if isinstance(tile, dict):
        return {k: _coerce_tile(v) for k, v in tile.items()}
    if isinstance(tile, list):
        return [_coerce_tile(v) for v in tile]
    if isinstance(tile, ObjectId):
        return str(tile)
    return tile


_MAP_TILE_PROJECTION = {
    "_id": 0, "q": 1, "r": 1, "terrain": 1, "owner": 1,
    "city": 1, "district": 1, "wonder": 1, "capital": 1,
    "node": 1, "portal": 1, "route": 1, "region": 1,
    "bandit_camp": 1,
}

# ---------------------------------------------------------------------------
# Tile chunk mirror — a read-optimized copy of hex_map_tiles for "give me
# every tile" consumers (_get_cached_all_tiles/get_all_tiles), which used to
# each pay a direct ~11,500-document scan (~8.6s, dominated by per-document/
# round-trip overhead — see _get_cached_all_tiles's docstring). Grouping
# tiles into ~100 chunk documents cuts that to ~100 document fetches.
#
# hex_map_tiles itself remains the single source of truth; every targeted
# read (owner-scoped, single (q, r), city.id/district.id lookups, etc.) and
# every write continues to go straight to it, completely unchanged. Only
# the handful of full-map-read call sites go through this mirror instead.
#
# Freshness: a write that wants the mirror to stay warm calls
# notify_tile_changed(tile_id) (or _sync_tile_chunk directly) right after
# writing — this covers the high-frequency paths (tick tile claims, the
# admin single-tile editor, AI decision writes). Everything else (bulk
# renames, admin maintenance tools, one-off scripts) only needs to keep
# calling bump_tile_version() as it already does: get_all_tiles_from_chunks
# self-heals via a full rebuild whenever the chunk mirror's recorded
# version doesn't match the live tile_version, so a missed sync hook can
# only ever cost one extra rebuild, never stale data.
# ---------------------------------------------------------------------------

_TILE_CHUNK_SIZE = 8
_HEX_MAP_CONFIG_NAME = "hex_map_config"


def _chunk_id(q, r):
    """Deterministic spatial chunk key — groups nearby tiles together so a
    full-map read becomes ~100 document fetches instead of ~11,500."""
    return f"{q // _TILE_CHUNK_SIZE}_{r // _TILE_CHUNK_SIZE}"


def rebuild_tile_chunks():
    """Rebuild hex_map_tile_chunks from scratch off the live hex_map_tiles
    collection. This is both the one-time backfill and the self-heal path
    get_all_tiles_from_chunks falls back to on any version mismatch — pays
    the full ~11,500-document scan once; every full-map read after that
    (until the next tile write) is a ~100-document chunk fetch instead.

    Returns the tile_version the rebuild was performed at.
    """
    tiles = list(mongo.db.hex_map_tiles.find({}, _MAP_TILE_PROJECTION).batch_size(20000))
    by_chunk = {}
    for t in tiles:
        cid = _chunk_id(t["q"], t["r"])
        by_chunk.setdefault(cid, []).append(t)

    config = mongo.db.global_modifiers.find_one({"name": _HEX_MAP_CONFIG_NAME}, {"tile_version": 1}) or {}
    current_version = config.get("tile_version", 0)

    for cid, chunk_tiles in by_chunk.items():
        mongo.db.hex_map_tile_chunks.update_one(
            {"_id": cid},
            {"$set": {"tiles": chunk_tiles}},
            upsert=True,
        )
    # Drop any chunk that no longer has tiles (e.g. a coordinate
    # normalization/cleanup shrank the map) so stale chunks don't linger.
    mongo.db.hex_map_tile_chunks.delete_many({"_id": {"$nin": list(by_chunk.keys())}})

    mongo.db.global_modifiers.update_one(
        {"name": _HEX_MAP_CONFIG_NAME},
        {"$set": {"chunk_synced_version": current_version}},
        upsert=True,
    )
    return current_version


def _upsert_tile_into_chunk(tile):
    """Shared by _sync_tile_chunk/_sync_tile_chunk_by_coord: upsert one
    already-fetched tile dict into its chunk's tiles array, replacing any
    existing entry for that (q, r)."""
    cid = _chunk_id(tile["q"], tile["r"])
    result = mongo.db.hex_map_tile_chunks.update_one(
        {"_id": cid, "tiles.q": tile["q"], "tiles.r": tile["r"]},
        {"$set": {"tiles.$": tile}},
    )
    if result.matched_count == 0:
        # No existing array entry for this (q, r) — either a brand new
        # tile, or the chunk doesn't exist yet.
        mongo.db.hex_map_tile_chunks.update_one(
            {"_id": cid},
            {"$push": {"tiles": tile}},
            upsert=True,
        )


def _sync_tile_chunk(tile_id):
    """Re-fetch one tile by its _id and upsert it into its chunk's tiles
    array, so a single-tile write keeps the chunk mirror warm without a
    full rebuild. Re-fetches rather than diffing so it's correct regardless
    of which fields changed."""
    tile = mongo.db.hex_map_tiles.find_one({"_id": tile_id}, _MAP_TILE_PROJECTION)
    if not tile or "q" not in tile or "r" not in tile:
        return
    _upsert_tile_into_chunk(tile)


def _sync_tile_chunk_by_coord(q, r):
    """Same as _sync_tile_chunk, for write call sites that key directly off
    (q, r) rather than a tile's _id (e.g. several AI decision writes)."""
    tile = mongo.db.hex_map_tiles.find_one(
        {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}, _MAP_TILE_PROJECTION
    )
    if not tile:
        return
    _upsert_tile_into_chunk(tile)


def notify_tile_changed(tile_id):
    """Convenience wrapper for write call sites that need both: bump the
    frontend's map-cache version and keep the tile-chunk mirror in sync."""
    bump_tile_version()
    _sync_tile_chunk(tile_id)


def notify_tile_changed_by_coord(q, r):
    """Same as notify_tile_changed, for call sites keying off (q, r)."""
    bump_tile_version()
    _sync_tile_chunk_by_coord(q, r)



# Process-level cache of the fully-flattened tile list, keyed by the
# tile_version it was built at — sits in front of the Mongo-persisted chunk
# mirror. Measured live: reading either the ~11,500-document flat
# hex_map_tiles collection OR the ~200-document chunk mirror both cost
# several seconds on this connection (the bottleneck is aggregate data
# transfer/server processing for "every tile", not round-trip count —
# splitting it into fewer documents doesn't shrink that). The per-request
# g/thread-local cache in _get_cached_all_tiles already avoids repeating
# that cost within a single request/tick thread, but a brand new request
# (e.g. every separate nation-save HTTP request) starts a fresh `g` and
# would otherwise pay the full cost again even when not a single tile
# changed since the last request. This process-wide cache is what actually
# eliminates that: as long as this worker process has already read a given
# tile_version once, every later request checks only one cheap ~50ms
# version doc before reusing the same in-memory list — no per-tile data
# ever needs to move again until a real tile write bumps the version.
_process_tile_cache_lock = threading.Lock()
# Keyed by id(mongo) as well as version — not just version — so a test (or
# any future reconnect) that swaps in a different `mongo`/database object
# can never accidentally reuse another connection's cached tiles just
# because both happen to be at the same tile_version number (e.g. two
# fresh mongomock databases both starting at version 0). In production
# `mongo` is one long-lived object for the life of the process, so this
# adds no real invalidation there.
_process_tile_cache = {"mongo_id": None, "version": None, "tiles": None}


def get_all_tiles_from_chunks():
    """Return every tile (the same flat list shape a direct hex_map_tiles
    scan produces). See the module comment above and this file's
    "Process-level cache" comment for the two layers this reads through.

    Self-heals via a full rebuild whenever the chunk mirror's recorded
    chunk_synced_version doesn't match the live tile_version (a missed
    sync hook, or chunks that were never built), so a stale mirror never
    silently serves wrong data — the worst case is paying one full rebuild
    instead of hitting the fast path.
    """
    mongo_id = id(mongo)
    config = mongo.db.global_modifiers.find_one(
        {"name": _HEX_MAP_CONFIG_NAME}, {"tile_version": 1, "chunk_synced_version": 1}
    ) or {}
    current_version = config.get("tile_version", 0)

    with _process_tile_cache_lock:
        if (
            _process_tile_cache["mongo_id"] == mongo_id
            and _process_tile_cache["version"] == current_version
            and _process_tile_cache["tiles"] is not None
        ):
            return _process_tile_cache["tiles"]

    synced_version = config.get("chunk_synced_version")
    if synced_version != current_version:
        current_version = rebuild_tile_chunks()

    tiles = []
    for chunk in mongo.db.hex_map_tile_chunks.find({}, {"_id": 0, "tiles": 1}):
        tiles.extend(chunk.get("tiles", []))

    with _process_tile_cache_lock:
        _process_tile_cache["mongo_id"] = mongo_id
        _process_tile_cache["version"] = current_version
        _process_tile_cache["tiles"] = tiles
    return tiles


def get_all_tiles():
    return [_coerce_tile(t) for t in get_all_tiles_from_chunks()]


def get_tile_ids_by_coords(coords):
    """Look up hex_map_tiles _id for a small, specific set of (q, r)
    coordinates.

    get_all_tiles()/get_all_tiles_from_chunks() deliberately don't carry
    _id (kept out of the chunk mirror), so a caller that reads the map
    through them but still needs a real _id for a few tiles it's about to
    write to (e.g. to defer a claim via _queue_tile_write) looks it up
    here instead. `coords` should be the small handful of tiles actually
    being written this call, not the whole map — this issues one query
    with one $or clause per coordinate, not a full scan.
    """
    if not coords:
        return {}
    or_clauses = [
        {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}
        for q, r in coords
    ]
    result = {}
    for t in mongo.db.hex_map_tiles.find({"$or": or_clauses}, {"q": 1, "r": 1, "_id": 1}):
        result[(t["q"], t["r"])] = t["_id"]
    return result


def bump_tile_version():
    """Increment the map tile version counter used for ETag-based HTTP caching.

    Call after any operation that modifies hex_map_tiles or nation colors so that
    browsers know to re-fetch instead of serving stale cached tile data.

    Also the freshness signal get_all_tiles_from_chunks compares against —
    a write that doesn't also call notify_tile_changed/_sync_tile_chunk
    directly still keeps the chunk mirror eventually correct: the next
    full-map read sees a version mismatch and self-heals via a full rebuild.
    """
    try:
        mongo.db.global_modifiers.update_one(
            {"name": _HEX_MAP_CONFIG_NAME},
            {"$inc": {"tile_version": 1}},
            upsert=True,
        )
    except Exception:
        pass


def get_session_tiles(session_num):
    snapshot = mongo.db.hex_map_history.find_one({"session": session_num})
    if snapshot:
        return snapshot.get("tiles", [])
    return get_all_tiles()


def get_neighbor_tiles(q, r):
    result = []
    for nq, nr in axial_neighbors(q, r):
        t = mongo.db.hex_map_tiles.find_one({"q": nq, "r": nr}, {"_id": 0})
        if t:
            result.append(t)
    return result


def is_tile_adjacent_to_nation(q, r, nation_name):
    """Returns True if any neighbor of (q, r) is owned by nation_name."""
    for nq, nr in axial_neighbors(q, r):
        t = mongo.db.hex_map_tiles.find_one({"q": nq, "r": nr}, {"_id": 0, "owner": 1})
        if t and t.get("owner") == nation_name:
            return True
    return False


def get_nation_connected_tiles(nation_name):
    """BFS returning the set of (q,r) reachable through connected tiles owned by nation_name."""
    owned = {
        (t["q"], t["r"])
        for t in mongo.db.hex_map_tiles.find(
            {"owner": nation_name}, {"q": 1, "r": 1, "_id": 0}
        )
    }
    if not owned:
        return set()
    visited = set()
    queue = deque([next(iter(owned))])
    while queue:
        pos = queue.popleft()
        if pos in visited:
            continue
        visited.add(pos)
        q, r = pos
        for nq, nr in axial_neighbors(q, r):
            if (nq, nr) in owned and (nq, nr) not in visited:
                queue.append((nq, nr))
    return visited


def route_cost_reduction(tile):
    """Movement-cost reduction granted by a route on this tile.
    Tier 3 routes reduce entry cost by 2; tier 2 routes by 1 (minimum cost 1).

    Mirrored (not shared — see compute_admin_range_out_of_range's docstring)
    in static/js/hex-map.js's _computeAllAdminRanges, which inlines the same
    tier-3/-2 reduction for its own client-side admin-range Dijkstra. Keep
    both in sync; tests/test_admin_range_parity.py asserts they match.
    """
    tier = ((tile or {}).get("route") or {}).get("tier")
    if tier == 3:
        return 2
    if tier == 2:
        return 1
    return 0


def compute_admin_range_out_of_range(nation_name, admin, nomadic=False, all_tiles=None):
    """Return a set of (q, r) owned by nation_name that exceed admin movement range.

    Non-nomadic: limit = admin * 4, measured from building tiles (city/district/wonder);
    falls back to capital tile if no buildings exist.
    Nomadic: limit = admin * 8, always measured from capital tile only (no buildings).
    If no source tile is found, all owned tiles are returned as out-of-range.

    "Entering a tile is sufficient" rule: a tile T is in-range when the cost to
    reach T's entrance — dist[T] minus T's own movement cost — is ≤ limit.
    Building tiles themselves are always in range.

    This is the real calculation engine's source of truth — field_calculations.py
    calls this (via effective_territory_types) to determine which tiles actually
    produce resources. static/js/hex-map.js's _computeAllAdminRanges is a second,
    independent implementation of this exact algorithm (building/capital source
    selection, portal jumps, route-tier reduction, the same in-range boundary
    condition) used for the map's live out-of-range preview while painting — it
    can't literally share this function since it runs client-side for
    responsiveness, but it must stay behaviorally identical. Both already read
    terrain movement costs from terrains.json (this function directly; the JS
    side via /api/hex-map/config, which serves the same file).
    tests/test_admin_range_parity.py guards against the two drifting apart.
    """
    IMPASSABLE = 9999
    limit = admin * (8 if nomadic else 4)

    terrain_data = json_data.get("terrains", {})
    move_cost = {
        k: v.get("speed_cost") or v.get("naval_speed_cost") or IMPASSABLE
        for k, v in terrain_data.items()
    }

    if all_tiles is None:
        # Cached so the full tile fetch only happens once per Flask request,
        # or once per background thread (e.g. the session tick) — saves ~7s
        # on every subsequent nation in the same tick.
        all_tiles = _get_cached_all_tiles()

    tile_map = {(t["q"], t["r"]): t for t in all_tiles}

    # Build portal map: color → [(q, r)] for all portal tiles on the map.
    portal_map = {}
    for t in all_tiles:
        portal = t.get("portal")
        if portal and portal.get("color"):
            color = portal["color"]
            if color not in portal_map:
                portal_map[color] = []
            portal_map[color].append((t["q"], t["r"]))

    owned_coords = frozenset(
        (t["q"], t["r"]) for t in all_tiles if t.get("owner") == nation_name
    )
    if not owned_coords:
        return set()

    if nomadic:
        # Nomadic nations always measure from capital only
        capital_coords = frozenset(
            (t["q"], t["r"]) for t in all_tiles
            if t.get("owner") == nation_name and t.get("capital")
        )
        sources = capital_coords
    else:
        building_coords = frozenset(
            (t["q"], t["r"]) for t in all_tiles
            if t.get("owner") == nation_name and (t.get("city") or t.get("district") or t.get("wonder"))
        )
        if building_coords:
            sources = building_coords
        else:
            # Fall back to capital tile when no buildings exist
            capital_coords = frozenset(
                (t["q"], t["r"]) for t in all_tiles
                if t.get("owner") == nation_name and t.get("capital")
            )
            sources = capital_coords

    if not sources:
        return set(owned_coords)

    INF = float("inf")
    dist = {coord: 0.0 for coord in sources}
    heap = [(0, q, r) for q, r in sources]
    heapq.heapify(heap)

    while heap:
        d, q, r = heapq.heappop(heap)
        if d > dist.get((q, r), INF):
            continue
        # Normal hex neighbors
        for nq, nr in axial_neighbors(q, r):
            tile = tile_map.get((nq, nr))
            terrain = tile.get("terrain", "disconnected") if tile else "disconnected"
            cost = move_cost.get(terrain, IMPASSABLE)
            if cost >= IMPASSABLE:
                continue
            reduction = route_cost_reduction(tile)
            if reduction:
                cost = max(1, cost - reduction)
            new_dist = d + cost
            if new_dist < dist.get((nq, nr), INF):
                dist[(nq, nr)] = new_dist
                heapq.heappush(heap, (new_dist, nq, nr))
        # Portal virtual neighbors — same-color portal pairs are treated as adjacent
        cur_tile = tile_map.get((q, r))
        if cur_tile:
            portal = cur_tile.get("portal")
            if portal and portal.get("color"):
                for pq, pr in portal_map.get(portal["color"], []):
                    if (pq, pr) == (q, r):
                        continue
                    p_tile = tile_map.get((pq, pr))
                    terrain = p_tile.get("terrain", "disconnected") if p_tile else "disconnected"
                    cost = move_cost.get(terrain, IMPASSABLE)
                    if cost >= IMPASSABLE:
                        continue
                    new_dist = d + cost
                    if new_dist < dist.get((pq, pr), INF):
                        dist[(pq, pr)] = new_dist
                        heapq.heappush(heap, (new_dist, pq, pr))

    out_of_range = set()
    for coord in owned_coords:
        if coord in sources:
            continue
        q, r = coord
        tile = tile_map.get(coord)
        terrain = tile.get("terrain", "disconnected") if tile else "disconnected"
        cost = move_cost.get(terrain, IMPASSABLE)
        reduction = route_cost_reduction(tile)
        if reduction:
            cost = max(1, cost - reduction)
        d = dist.get(coord, INF)
        if d == INF or (d - cost) >= limit:
            out_of_range.add(coord)

    return out_of_range



def is_tile_legally_controllable(q, r, nation_name):
    """Return True if nation_name can legally claim tile (q, r).

    Checks node proximity restrictions from the nation's race trait only
    (e.g. Ethereal races can only own tiles within 1 hex of a magic node).
    Adjacency is not enforced — nations can claim disconnected tiles via naval routes.
    """
    for resource_type, max_distance in get_nation_node_proximity_restrictions(nation_name):
        node_positions = get_node_resource_positions(resource_type)
        if not is_within_distance_of_node_resource(q, r, resource_type, max_distance, node_positions):
            return False
    return True


def _dijkstra_from_sources(sources, tile_map, portal_map, terrain_move_costs):
    """Multi-source Dijkstra across all tiles. Returns {(q, r): distance} for every reachable tile.

    Impassable terrain (cost >= 9999) is never entered.  Tier-3 route tiles reduce
    their entry cost by 2 and tier-2 by 1 (minimum 1).  Same-colour portal pairs are
    treated as zero-extra-cost virtual neighbours.
    """
    IMPASSABLE = 9999
    INF = float("inf")
    dist = {coord: 0.0 for coord in sources}
    heap = [(0.0, q, r) for q, r in sources]
    heapq.heapify(heap)
    while heap:
        d, q, r = heapq.heappop(heap)
        if d > dist.get((q, r), INF):
            continue
        # Normal hex neighbours
        for nq, nr in axial_neighbors(q, r):
            tile = tile_map.get((nq, nr))
            if tile is None:
                continue
            cost = terrain_move_costs.get(tile.get("terrain", ""), IMPASSABLE)
            if cost >= IMPASSABLE:
                continue
            reduction = route_cost_reduction(tile)
            if reduction:
                cost = max(1, cost - reduction)
            nd = d + cost
            if nd < dist.get((nq, nr), INF):
                dist[(nq, nr)] = nd
                heapq.heappush(heap, (nd, nq, nr))
        # Portal virtual neighbours
        cur = tile_map.get((q, r))
        if cur:
            portal = cur.get("portal")
            if portal and portal.get("color"):
                for pq, pr in portal_map.get(portal["color"], []):
                    if (pq, pr) == (q, r):
                        continue
                    ptile = tile_map.get((pq, pr))
                    if not ptile:
                        continue
                    cost = terrain_move_costs.get(ptile.get("terrain", ""), IMPASSABLE)
                    if cost >= IMPASSABLE:
                        continue
                    nd = d + cost
                    if nd < dist.get((pq, pr), INF):
                        dist[(pq, pr)] = nd
                        heapq.heappush(heap, (nd, pq, pr))
    return dist


def select_passive_expansion_tiles(nation, all_tiles=None):
    """Return an ordered list of (q, r) coordinates for the nation to passively expand into.

    Selection rules (applied in priority order — lower number = claimed first):
      1. Adjacent to a capital tile
      2. Adjacent to a wonder tile
      3. Adjacent to a city tile
      4. Adjacent to a district tile
      5. Closest admin distance from building tiles (cities / wonders / districts)

    Within each priority tier ties are broken by ascending admin distance.

    Count = max(1, floor((effective_territory - current_territory) / 10)).
    Only unowned tiles that pass the nation's racial legality checks are considered.
    Tiles need not be within admin range — the spec allows expanding beyond it.
    """
    from app_core import json_data as _json_data

    nation_name = nation.get("name")
    if not nation_name:
        return []

    if all_tiles is None:
        all_tiles = list(mongo.db.hex_map_tiles.find(
            {},
            {"q": 1, "r": 1, "terrain": 1, "owner": 1,
             "city": 1, "district": 1, "wonder": 1, "capital": 1,
             "portal": 1, "route": 1, "_id": 0},
        ))

    tile_map = {(t["q"], t["r"]): t for t in all_tiles}
    owned = {(t["q"], t["r"]) for t in all_tiles if t.get("owner") == nation_name}
    if not owned:
        return []

    # Number of tiles to claim this expansion event
    eff_territory = int(nation.get("effective_territory", 0))
    cur_territory = int(nation.get("current_territory", 0))
    remaining     = max(0, eff_territory - cur_territory)
    n_claim       = max(1, remaining // 10)

    # Categorise owned tiles by building type for adjacency priority checks
    capital_tiles  = {pos for pos in owned if tile_map[pos].get("capital")}
    wonder_tiles   = {pos for pos in owned if tile_map[pos].get("wonder")}
    city_tiles     = {pos for pos in owned if tile_map[pos].get("city")}
    district_tiles = {pos for pos in owned if tile_map[pos].get("district")}

    # Build terrain cost and portal maps for Dijkstra
    terrain_data       = _json_data.get("terrains", {})
    terrain_move_costs = {
        k: v.get("speed_cost") or v.get("naval_speed_cost") or 9999
        for k, v in terrain_data.items()
    }
    portal_map = {}
    for t in all_tiles:
        p = t.get("portal")
        if p and p.get("color"):
            portal_map.setdefault(p["color"], []).append((t["q"], t["r"]))

    # Admin distance from building tiles (cities/wonders/districts; fallback: capital)
    building_sources = wonder_tiles | city_tiles | district_tiles
    if not building_sources:
        building_sources = capital_tiles
    admin_dist = (
        _dijkstra_from_sources(building_sources, tile_map, portal_map, terrain_move_costs)
        if building_sources else {}
    )

    # Collect candidate tiles: adjacent to owned territory, currently unowned
    candidates = set()
    for (q, r) in owned:
        for nq, nr in axial_neighbors(q, r):
            if (nq, nr) not in owned and (nq, nr) in tile_map:
                if not tile_map[(nq, nr)].get("owner"):
                    candidates.add((nq, nr))

    if not candidates:
        return []

    # Filter by racial legality — cache node positions per resource type
    restrictions = get_nation_node_proximity_restrictions(nation_name)
    node_cache   = {}
    legal = []
    for pos in candidates:
        ok = True
        for res_type, max_d in restrictions:
            if res_type not in node_cache:
                node_cache[res_type] = get_node_resource_positions(res_type)
            if not is_within_distance_of_node_resource(
                pos[0], pos[1], res_type, max_d, node_cache[res_type]
            ):
                ok = False
                break
        if ok:
            legal.append(pos)

    if not legal:
        return []

    # Score: (priority_tier, admin_distance)
    INF = float("inf")

    def _score(pos):
        nbs = set(axial_neighbors(pos[0], pos[1]))
        if nbs & capital_tiles:
            tier = 1
        elif nbs & wonder_tiles:
            tier = 2
        elif nbs & city_tiles:
            tier = 3
        elif nbs & district_tiles:
            tier = 4
        else:
            tier = 5
        return (tier, admin_dist.get(pos, INF))

    legal.sort(key=_score)
    return legal[:n_claim]


def get_nations_within_distance(nation_name, max_distance=10):
    """Return a list of nation names whose territory contains at least one tile
    within max_distance hexes of any tile owned by nation_name.

    Excludes nation_name itself. Returns an empty list if the nation owns no tiles.

    Reads through get_all_tiles_from_chunks (the chunk-backed, process-wide
    cache) instead of two separate hex_map_tiles queries — one filtered to
    this nation's own tiles, one to nearly every OTHER tile on the map.
    Both cost the same ~several seconds on this cluster regardless of
    projection (aggregate transfer/server processing, not round-trip count
    — see this module's cache comment), and this function can be called
    once per nation per mech RP attempt needing a foreign conversion
    target, so that cost was being paid twice, repeatedly. Read-only, so
    the shared (uncoerced) tile references from the cache are used
    directly — no copy needed since nothing here mutates a tile.
    """
    all_tiles = get_all_tiles_from_chunks()
    own_tiles = [(t["q"], t["r"]) for t in all_tiles if t.get("owner") == nation_name]
    if not own_tiles:
        return []

    nearby_nations = set()
    for tile in all_tiles:
        owner = tile.get("owner")
        if not owner or owner == nation_name or owner in nearby_nations:
            continue
        tq, tr = tile["q"], tile["r"]
        if any(hex_distance(tq, tr, oq, or_) <= max_distance for oq, or_ in own_tiles):
            nearby_nations.add(owner)

    return sorted(nearby_nations)


def get_nation_tile_stats(nation_name):
    """Returns terrain and node type counts for all tiles owned by a nation."""
    tiles = list(mongo.db.hex_map_tiles.find({"owner": nation_name}, {"_id": 0}))
    terrain_types = {}
    node_types = {}
    for tile in tiles:
        terrain = tile.get("terrain", "unknown")
        terrain_types[terrain] = terrain_types.get(terrain, 0) + 1
        node = tile.get("node")
        if node:
            nt = node.get("type", "unknown")
            node_types[nt] = node_types.get(nt, 0) + 1
    return {
        "terrain_types": terrain_types,
        "node_types": node_types,
        "total_tiles": len(tiles),
    }


def _get_nation_colors_snapshot():
    """Return {name: color} for all nations — saved into snapshots for historical accuracy."""
    nations = list(mongo.db.nations.find({}, {"name": 1, "accent_color": 1, "_id": 0}))
    return {
        n["name"]: n.get("accent_color") or name_to_color(n["name"])
        for n in nations if n.get("name")
    }


def snapshot_current_map(session_num):
    """Copies all current hex_map_tiles into hex_map_history for the given session.

    Saves grid config and nation colors alongside tiles so historical views
    render correctly even after nations are renamed or deleted.
    """
    tiles = get_all_tiles()
    cfg   = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    doc   = {
        "session":       session_num,
        "tiles":         tiles,
        "cols":          cfg.get("cols",     20),
        "rows":          cfg.get("rows",     15),
        "hex_size":      cfg.get("hex_size", 40),
        "nation_colors": _get_nation_colors_snapshot(),
    }
    existing = mongo.db.hex_map_history.find_one({"session": session_num})
    if existing:
        doc["updated_at"] = datetime.datetime.utcnow()
        mongo.db.hex_map_history.update_one({"session": session_num}, {"$set": doc})
    else:
        doc["created_at"] = datetime.datetime.utcnow()
        mongo.db.hex_map_history.insert_one(doc)
    return f"Hex map snapshot taken for session {session_num} ({len(tiles)} tiles)."
