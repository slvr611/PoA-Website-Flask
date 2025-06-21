"""
Script to generate a full set of hex tiles to cover the world map.
This script analyzes the background image to determine terrain types based on pixel colors.
"""

import sys
import os
import math
from PIL import Image
import numpy as np
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_core import mongo
from bson import ObjectId

def generate_world_tiles_from_image(image_path, width=100, height=60, center_q=0, center_r=0):
    """
    Generate a large hex grid to cover the world map, using an image to determine terrain.
    
    Args:
        image_path: Path to the background image
        width: Width of the grid in tiles
        height: Height of the grid in tiles
        center_q: Q coordinate of the center tile
        center_r: R coordinate of the center tile
    """
    print(f"Generating world tiles from image: {image_path}")
    print(f"Grid size: {width}x{height} centered at ({center_q}, {center_r})")
    
    # Load the image
    try:
        img = Image.open(image_path)
        print(f"Loaded image: {img.width}x{img.height} pixels")
    except Exception as e:
        print(f"Error loading image: {e}")
        return
    
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
            
            # Calculate background position (normalized 0-1)
            bg_x = normalize_coordinate(q, min_q, max_q)
            bg_y = normalize_coordinate(r, min_r, max_r)
            
            # Determine terrain type based on image pixel
            terrain = determine_terrain_from_image(img, bg_x, bg_y)
            
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

def determine_terrain_from_image(img, bg_x, bg_y):
    """
    Determine terrain type based on pixel color in the image.
    
    Args:
        img: PIL Image object
        bg_x, bg_y: Normalized coordinates (0-1)
    
    Returns:
        String representing the terrain type
    """
    # Convert normalized coordinates to pixel coordinates
    pixel_x = int(bg_x * img.width)
    pixel_y = int(bg_y * img.height)
    
    # Ensure coordinates are within image bounds
    pixel_x = max(0, min(pixel_x, img.width - 1))
    pixel_y = max(0, min(pixel_y, img.height - 1))
    
    # Get pixel color
    pixel = img.getpixel((pixel_x, pixel_y))
    
    # Convert to RGB if image has alpha channel
    if len(pixel) == 4:  # RGBA
        r, g, b, _ = pixel
    else:  # RGB
        r, g, b = pixel
    
    # Determine terrain based on color
    # These thresholds should be adjusted based on your specific map
    
    # Deep blue for ocean
    if b > 150 and r < 100 and g < 100:
        return "ocean"
    
    # Light blue for coast
    if b > 150 and r > 100 and g > 100:
        return "coast"
    
    # Brown/tan for desert
    if r > 180 and g > 150 and b < 120:
        return "desert"
    
    # Dark green for forest
    if g > 100 and r < 100 and b < 100:
        return "forest"
    
    # Light green for plains
    if g > 150 and r > 100 and b < 100:
        return "plains"
    
    # Gray for mountains
    if r > 100 and g > 100 and b > 100 and abs(r - g) < 30 and abs(r - b) < 30:
        return "mountains"
    
    # Brown for hills
    if r > 120 and g > 80 and g < 120 and b < 80:
        return "hills"
    
    # Dark green/brown for swamp
    if g > 80 and g < 150 and r > 60 and r < 120 and b < 80:
        return "swamp"
    
    # Default to plains if no match
    return "plains"

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

if __name__ == "__main__":
    print("World Tile Generator from Image")
    print("===============================")
    
    # Default image path
    default_image_path = "static/images/maps/PoA_Terrain_Map.webp"
    
    # Get parameters from command line arguments or use defaults
    image_path = sys.argv[1] if len(sys.argv) > 1 else default_image_path
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    
    # Generate the tiles
    generate_world_tiles_from_image(image_path, width, height)
    
    print("Done!")