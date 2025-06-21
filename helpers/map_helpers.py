"""
Map helper functions for hexagonal coordinate system and map operations.
"""
import math
from typing import Tuple, List, Dict, Any
from app_core import category_data


class HexCoordinate:
    """Represents a hexagonal coordinate using the cube coordinate system (q, r, s)."""
    
    def __init__(self, q: int, r: int, s: int = None):
        if s is None:
            s = -q - r
        
        # Validate that q + r + s = 0 (cube coordinate constraint)
        if q + r + s != 0:
            raise ValueError(f"Invalid hex coordinate: q={q}, r={r}, s={s}. Must satisfy q + r + s = 0")
        
        self.q = q
        self.r = r
        self.s = s
    
    def __eq__(self, other):
        if not isinstance(other, HexCoordinate):
            return False
        return self.q == other.q and self.r == other.r and self.s == other.s
    
    def __hash__(self):
        return hash((self.q, self.r, self.s))
    
    def __str__(self):
        return f"Hex({self.q}, {self.r}, {self.s})"
    
    def to_dict(self):
        """Convert to dictionary for database storage."""
        return {"q": self.q, "r": self.r, "s": self.s}
    
    @classmethod
    def from_dict(cls, data: Dict[str, int]):
        """Create from dictionary (e.g., from database)."""
        return cls(data["q"], data["r"], data["s"])


def hex_distance(hex1: HexCoordinate, hex2: HexCoordinate) -> int:
    """Calculate the distance between two hexagonal coordinates."""
    return (abs(hex1.q - hex2.q) + abs(hex1.r - hex2.r) + abs(hex1.s - hex2.s)) // 2


def hex_neighbors(hex_coord: HexCoordinate) -> List[HexCoordinate]:
    """Get all 6 neighboring hexagonal coordinates."""
    directions = [
        (1, 0, -1), (1, -1, 0), (0, -1, 1),
        (-1, 0, 1), (-1, 1, 0), (0, 1, -1)
    ]
    
    neighbors = []
    for dq, dr, ds in directions:
        neighbors.append(HexCoordinate(
            hex_coord.q + dq,
            hex_coord.r + dr,
            hex_coord.s + ds
        ))
    
    return neighbors


def hex_to_pixel(hex_coord: HexCoordinate, size: float) -> Tuple[float, float]:
    """Convert hexagonal coordinates to pixel coordinates for rendering."""
    x = size * (3/2 * hex_coord.q)
    y = size * (math.sqrt(3)/2 * hex_coord.q + math.sqrt(3) * hex_coord.r)
    return x, y


def pixel_to_hex(x: float, y: float, size: float) -> HexCoordinate:
    """Convert pixel coordinates to hexagonal coordinates."""
    q = (2/3 * x) / size
    r = (-1/3 * x + math.sqrt(3)/3 * y) / size
    
    # Round to nearest hex
    return hex_round(q, r)


def hex_round(q: float, r: float) -> HexCoordinate:
    """Round fractional hex coordinates to the nearest hex."""
    s = -q - r
    
    rq = round(q)
    rr = round(r)
    rs = round(s)
    
    q_diff = abs(rq - q)
    r_diff = abs(rr - r)
    s_diff = abs(rs - s)
    
    if q_diff > r_diff and q_diff > s_diff:
        rq = -rr - rs
    elif r_diff > s_diff:
        rr = -rq - rs
    else:
        rs = -rq - rr
    
    return HexCoordinate(rq, rr, rs)


def get_tile_by_coordinates(q: int, r: int, s: int = None) -> Dict[str, Any]:
    """Get a map tile by its hexagonal coordinates."""
    if s is None:
        s = -q - r
    
    tiles_db = category_data["map_tiles"]["database"]
    return tiles_db.find_one({"q": q, "r": r, "s": s})


def get_tiles_in_radius(center: HexCoordinate, radius: int) -> List[Dict[str, Any]]:
    """Get all tiles within a given radius of a center coordinate."""
    tiles = []
    tiles_db = category_data["map_tiles"]["database"]
    
    for q in range(-radius, radius + 1):
        r1 = max(-radius, -q - radius)
        r2 = min(radius, -q + radius)
        for r in range(r1, r2 + 1):
            s = -q - r
            tile = tiles_db.find_one({"q": q + center.q, "r": r + center.r, "s": s + center.s})
            if tile:
                tiles.append(tile)
    
    return tiles


def get_nation_territories(nation_id: str) -> List[Dict[str, Any]]:
    """Get all tiles owned by a specific nation."""
    tiles_db = category_data["map_tiles"]["database"]
    return list(tiles_db.find({"nation_owner": nation_id}))


def get_tiles_with_nodes() -> List[Dict[str, Any]]:
    """Get all tiles that have nodes on them."""
    tiles_db = category_data["map_tiles"]["database"]
    return list(tiles_db.find({"nodes": {"$exists": True, "$not": {"$size": 0}}}))


def get_tiles_with_districts() -> List[Dict[str, Any]]:
    """Get all tiles that have districts built on them."""
    tiles_db = category_data["map_tiles"]["database"]
    return list(tiles_db.find({"district": {"$exists": True, "$ne": None}}))


def serialize_tile_for_frontend(tile: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a tile for frontend consumption."""
    if not tile:
        return None
    
    # Get nation info if tile is owned
    nation_info = None
    if tile.get("nation_owner"):
        nations_db = category_data["nations"]["database"]
        nation = nations_db.find_one({"_id": tile["nation_owner"]})
        if nation:
            nation_info = {
                "name": nation.get("name", "Unknown"),
                "color": nation.get("map_color", "#888888")
            }
    
    # Get node info
    node_info = []
    if tile.get("nodes"):
        nodes_db = category_data["map_nodes"]["database"]
        for node_id in tile["nodes"]:
            node = nodes_db.find_one({"_id": node_id})
            if node:
                node_info.append({
                    "name": node.get("name", "Unknown Node"),
                    "type": node.get("node_type", "unknown"),
                    "description": node.get("description", "")
                })
    
    # Get district info
    district_info = None
    if tile.get("district"):
        # This would need to be implemented based on your district system
        # For now, just include the district ID
        district_info = {"id": tile["district"]}
    
    return {
        "coordinates": {
            "q": tile["q"],
            "r": tile["r"],
            "s": tile["s"]
        },
        "terrain_type": tile.get("terrain_type", "unknown"),
        "nation": nation_info,
        "nodes": node_info,
        "district": district_info,
        "background_position": {
            "x": tile.get("background_x"),
            "y": tile.get("background_y")
        }
    }


def get_all_tiles_for_map() -> List[Dict[str, Any]]:
    """Get all tiles serialized for the frontend map."""
    tiles_db = category_data["map_tiles"]["database"]
    tiles = list(tiles_db.find({}))
    
    return [serialize_tile_for_frontend(tile) for tile in tiles]


def create_hex_grid(width: int, height: int, center_q: int = 0, center_r: int = 0) -> List[HexCoordinate]:
    """Create a hexagonal grid of specified dimensions centered at given coordinates."""
    coordinates = []
    
    for q in range(center_q - width//2, center_q + width//2 + 1):
        for r in range(center_r - height//2, center_r + height//2 + 1):
            s = -q - r
            coordinates.append(HexCoordinate(q, r, s))
    
    return coordinates
