"""
Script to generate a full set of hex tiles to cover the world map.
This will create tiles with appropriate terrain types based on position.
"""

import sys
import os
import math
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_core import mongo
from bson import ObjectId

def generate_world_tiles(width=100, height=60, center_q=0, center_r=0):
    """
    Generate a large hex grid to cover the world map.
    
    Args:
        width: Width of the grid in tiles
        height: Height of the grid in tiles
        center_q: Q coordinate of the center tile
        center_r: R coordinate of the center tile
    """
    print(f"Generating world tiles: {width}x{height} grid centered at ({center_q}, {center_r})")
    
    # Calculate the boundaries
    min_q = center_q - width // 2
    max_q = center_q + width // 2
    min_r = center_r - height // 2
    max_r = center_r + height // 2
    
    # Get database collections
    tiles_db = mongo.db.map_tiles
    
    # Count existing tiles
    existing_count = tiles_db.count_documents({})
    print(f"Found {existing_count} existing tiles in database")
    
    # Track statistics
    created_count = 0
    skipped_count = 0
    
    # Generate tiles
    for q in range(min_q, max_q + 1):
        for r in range(min_r, max_r + 1):
            # Calculate s coordinate (q + r + s = 0)
            s = -q - r
            
            # Check if tile already exists
            existing = tiles_db.find_one({"q": q, "r": r, "s": s})
            if existing:
                skipped_count += 1
                continue
            
            # Determine terrain type based on position
            terrain = determine_terrain_type(q, r, s, width, height)
            
            # Calculate background position (normalized 0-1)
            # This maps the hex coordinates to the background image position
            bg_x = normalize_coordinate(q, min_q, max_q)
            bg_y = normalize_coordinate(r, min_r, max_r)
            
            # Create tile data
            tile_data = {
                "q": q,
                "r": r,
                "s": s,
                "terrain_type": terrain,
                "nodes": [],
                "background_x": bg_x,
                "background_y": bg_y
            }
            
            # Insert the tile
            result = tiles_db.insert_one(tile_data)
            created_count += 1
            
            # Print progress every 100 tiles
            if created_count % 100 == 0:
                print(f"Created {created_count} tiles...")
    
    print(f"Tile generation complete!")
    print(f"Created {created_count} new tiles")
    print(f"Skipped {skipped_count} existing tiles")
    print(f"Total tiles in database: {existing_count + created_count}")

def determine_terrain_type(q, r, s, width, height):
    """
    Determine the terrain type based on the tile's position.
    This is a simple algorithm that can be customized based on your world map.
    
    Args:
        q, r, s: Hex coordinates
        width, height: Grid dimensions
    
    Returns:
        String representing the terrain type
    """
    # Distance from center (0,0)
    distance = (abs(q) + abs(r) + abs(s)) / 2
    
    # Edge of map is ocean
    edge_threshold = min(width, height) / 3
    if distance > edge_threshold:
        return "ocean"
    
    # Create some terrain patterns
    # This is a simple algorithm - you can make it more complex
    
    # Use noise-like pattern based on coordinates
    noise_value = math.sin(q * 0.1) * math.cos(r * 0.1) * math.sin(s * 0.05)
    
    if noise_value > 0.8:
        return "mountains"
    elif noise_value > 0.6:
        return "hills"
    elif noise_value > 0.3:
        return "forest"
    elif noise_value > 0:
        return "plains"
    elif noise_value > -0.3:
        return "desert"
    elif noise_value > -0.6:
        return "swamp"
    else:
        return "coast"

def normalize_coordinate(value, min_val, max_val):
    """
    Normalize a coordinate to a 0-1 range.
    
    Args:
        value: The coordinate value
        min_val: Minimum value in the range
        max_val: Maximum value in the range
    
    Returns:
        Normalized value between 0 and 1
    """
    range_size = max_val - min_val
    if range_size == 0:
        return 0.5
    
    normalized = (value - min_val) / range_size
    return normalized

def assign_nation_territories():
    """
    Assign territories to nations based on regions.
    This is a simple implementation that can be customized.
    """
    # Get database collections
    tiles_db = mongo.db.map_tiles
    nations_db = mongo.db.nations
    
    # Get all nations
    nations = list(nations_db.find({}))
    if not nations:
        print("No nations found in database")
        return
    
    print(f"Found {len(nations)} nations")
    
    # Define regions for each nation (customize these based on your map)
    nation_regions = {}
    
    # Example: Assign regions based on coordinate ranges
    # Aethermoor Empire: Northeast
    nation_regions[nations[0]["_id"]] = lambda q, r, s: q > 10 and r < -5
    
    # Verdant Republic: Central
    nation_regions[nations[1]["_id"]] = lambda q, r, s: abs(q) < 10 and abs(r) < 10
    
    # Iron Clans: Northwest
    nation_regions[nations[2]["_id"]] = lambda q, r, s: q < -10 and r > 5
    
    # Desert Sultanate: Southeast
    nation_regions[nations[3]["_id"]] = lambda q, r, s: q > 5 and r > 10
    
    # Frost Kingdom: Far North
    nation_regions[nations[4]["_id"]] = lambda q, r, s: r < -15
    
    # Shadow Covenant: Southwest
    nation_regions[nations[5]["_id"]] = lambda q, r, s: q < -15 and r < 0
    
    # Golden Merchants: East
    nation_regions[nations[6]["_id"]] = lambda q, r, s: q > 15 and abs(r) < 10
    
    # Crimson Horde: South
    nation_regions[nations[7]["_id"]] = lambda q, r, s: r > 15
    
    # Process all tiles
    tiles = list(tiles_db.find({}))
    print(f"Processing {len(tiles)} tiles for nation assignment")
    
    # Track statistics
    assigned_count = 0
    
    # Assign nations to tiles
    for tile in tiles:
        q, r, s = tile["q"], tile["r"], tile["s"]
        
        # Skip ocean tiles
        if tile["terrain_type"] == "ocean":
            continue
        
        # Check each nation's region
        for nation_id, region_check in nation_regions.items():
            if region_check(q, r, s):
                # Assign tile to nation
                tiles_db.update_one(
                    {"_id": tile["_id"]},
                    {"$set": {"nation_owner": nation_id}}
                )
                assigned_count += 1
                break
    
    print(f"Assigned {assigned_count} tiles to nations")
    
    # Update nation territory lists
    for nation in nations:
        nation_tiles = list(tiles_db.find({"nation_owner": nation["_id"]}, {"_id": 1}))
        nation_tile_ids = [tile["_id"] for tile in nation_tiles]
        
        nations_db.update_one(
            {"_id": nation["_id"]},
            {"$set": {"territory_tiles": nation_tile_ids}}
        )
        
        print(f"Updated {nation['name']} with {len(nation_tile_ids)} territory tiles")

if __name__ == "__main__":
    print("World Tile Generator")
    print("====================")
    
    # Get grid dimensions from command line arguments or use defaults
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    
    # Generate the tiles
    generate_world_tiles(width, height)
    
    # Ask if user wants to assign nation territories
    assign_territories = input("Do you want to assign nation territories? (y/n): ")
    if assign_territories.lower() == 'y':
        assign_nation_territories()
    
    print("Done!")