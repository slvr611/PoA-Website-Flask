"""
Sample script to create nations with map colors for testing the hex map system.
Run this script to populate the database with sample nations.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_core import mongo

def create_sample_nations():
    """Create sample nations with map colors."""
    
    sample_nations = [
        {
            "name": "Aethermoor Empire",
            "map_color": "#FF4444",
            "territory_tiles": []
        },
        {
            "name": "Verdant Republic",
            "map_color": "#44FF44", 
            "territory_tiles": []
        },
        {
            "name": "Iron Clans",
            "map_color": "#4444FF",
            "territory_tiles": []
        },
        {
            "name": "Desert Sultanate",
            "map_color": "#FFAA44",
            "territory_tiles": []
        },
        {
            "name": "Frost Kingdom",
            "map_color": "#44AAFF",
            "territory_tiles": []
        },
        {
            "name": "Shadow Covenant",
            "map_color": "#AA44FF",
            "territory_tiles": []
        },
        {
            "name": "Golden Merchants",
            "map_color": "#FFD700",
            "territory_tiles": []
        },
        {
            "name": "Crimson Horde",
            "map_color": "#CC0000",
            "territory_tiles": []
        }
    ]
    
    nations_db = mongo.db.nations
    
    # Clear existing nations (optional - comment out if you want to keep existing data)
    # nations_db.delete_many({})
    
    # Insert sample nations
    for nation_data in sample_nations:
        # Check if nation already exists
        existing = nations_db.find_one({"name": nation_data["name"]})
        if not existing:
            result = nations_db.insert_one(nation_data)
            print(f"Created nation: {nation_data['name']} (ID: {result.inserted_id})")
        else:
            # Update existing nation with map color if it doesn't have one
            if not existing.get("map_color"):
                nations_db.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"map_color": nation_data["map_color"]}}
                )
                print(f"Updated nation: {nation_data['name']} with map color")
            else:
                print(f"Nation already exists: {nation_data['name']}")

def create_sample_nodes():
    """Create sample map nodes."""
    
    sample_nodes = [
        {
            "name": "Iron Mine",
            "node_type": "resource",
            "description": "A rich iron ore deposit providing steady metal resources",
            "resource_type": "iron",
            "resource_amount": 3,
            "modifiers": {"iron_production": 3},
            "requirements": {}
        },
        {
            "name": "Ancient Watchtower",
            "node_type": "strategic",
            "description": "An old watchtower providing defensive advantages",
            "modifiers": {"defense": 2, "vision_range": 1},
            "requirements": {}
        },
        {
            "name": "Trading Post",
            "node_type": "trade",
            "description": "A bustling trading post that generates commerce",
            "modifiers": {"money_income": 50, "trade_capacity": 2},
            "requirements": {}
        },
        {
            "name": "Ley Line Nexus",
            "node_type": "magical",
            "description": "A convergence of magical energy lines",
            "modifiers": {"magic_production": 2, "spell_power": 1},
            "requirements": {"tech": "magical_theory"}
        },
        {
            "name": "Sacred Grove",
            "node_type": "cultural",
            "description": "A sacred site important to local culture and religion",
            "modifiers": {"stability": 1, "culture_spread": 1},
            "requirements": {}
        },
        {
            "name": "Mountain Pass",
            "node_type": "strategic",
            "description": "A strategic mountain pass controlling regional movement",
            "modifiers": {"movement_control": 1, "trade_route_access": 1},
            "requirements": {}
        },
        {
            "name": "Gold Mine",
            "node_type": "resource",
            "description": "A productive gold mine generating wealth",
            "resource_type": "gold",
            "resource_amount": 2,
            "modifiers": {"money_income": 100},
            "requirements": {}
        },
        {
            "name": "Fortress Ruins",
            "node_type": "defensive",
            "description": "Ancient fortress ruins that can be restored for defense",
            "modifiers": {"fortification_bonus": 3},
            "requirements": {"tech": "masonry"}
        }
    ]
    
    nodes_db = mongo.db.map_nodes
    
    # Insert sample nodes
    for node_data in sample_nodes:
        # Check if node already exists
        existing = nodes_db.find_one({"name": node_data["name"]})
        if not existing:
            result = nodes_db.insert_one(node_data)
            print(f"Created node: {node_data['name']} (ID: {result.inserted_id})")
        else:
            print(f"Node already exists: {node_data['name']}")

def create_sample_tiles():
    """Create a small sample hex grid with some tiles."""
    
    # Create a small 7x7 hex grid centered at (0,0)
    tiles_to_create = []
    
    # Generate hex coordinates for a small area
    for q in range(-3, 4):
        for r in range(max(-3, -q-3), min(4, -q+4)):
            s = -q - r
            
            # Assign terrain based on position (just for variety)
            if abs(q) + abs(r) + abs(s) <= 2:  # Center area
                terrain = "plains"
            elif q < -1:
                terrain = "forest"
            elif q > 1:
                terrain = "hills"
            elif r < -1:
                terrain = "desert"
            elif r > 1:
                terrain = "swamp"
            else:
                terrain = "plains"
            
            tiles_to_create.append({
                "q": q,
                "r": r,
                "s": s,
                "terrain_type": terrain,
                "nodes": [],
                "background_x": 0.5 + (q * 0.05),  # Simple positioning
                "background_y": 0.5 + (r * 0.05)
            })
    
    tiles_db = mongo.db.map_tiles
    
    # Insert sample tiles
    for tile_data in tiles_to_create:
        # Check if tile already exists
        existing = tiles_db.find_one({
            "q": tile_data["q"], 
            "r": tile_data["r"], 
            "s": tile_data["s"]
        })
        if not existing:
            result = tiles_db.insert_one(tile_data)
            print(f"Created tile at ({tile_data['q']}, {tile_data['r']}, {tile_data['s']})")
        else:
            print(f"Tile already exists at ({tile_data['q']}, {tile_data['r']}, {tile_data['s']})")

if __name__ == "__main__":
    print("Creating sample data for hex map system...")
    
    print("\n1. Creating sample nations...")
    create_sample_nations()
    
    print("\n2. Creating sample nodes...")
    create_sample_nodes()
    
    print("\n3. Creating sample tiles...")
    create_sample_tiles()
    
    print("\nSample data creation complete!")
    print("You can now visit /map to see the hex map system in action.")
    print("Visit /admin/map/tiles to manage tiles (admin access required).")
