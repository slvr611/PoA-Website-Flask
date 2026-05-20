from app_core import mongo, category_data
from bson import ObjectId
import datetime
from collections import deque

AXIAL_DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]


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


def get_all_tiles():
    return list(mongo.db.hex_map_tiles.find({}, {"_id": 0}))


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


def get_nations_within_distance(nation_name, max_distance=10):
    """Return a list of nation names whose territory contains at least one tile
    within max_distance hexes of any tile owned by nation_name.

    Excludes nation_name itself. Returns an empty list if the nation owns no tiles.
    """
    own_tiles = [
        (t["q"], t["r"])
        for t in mongo.db.hex_map_tiles.find(
            {"owner": nation_name}, {"q": 1, "r": 1, "_id": 0}
        )
    ]
    if not own_tiles:
        return []

    other_tiles = mongo.db.hex_map_tiles.find(
        {"owner": {"$nin": [None, "", nation_name]}},
        {"q": 1, "r": 1, "owner": 1, "_id": 0},
    )

    nearby_nations = set()
    for tile in other_tiles:
        owner = tile.get("owner")
        if not owner or owner in nearby_nations:
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
