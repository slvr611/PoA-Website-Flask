from flask import Blueprint, render_template, jsonify, request, redirect, flash
from helpers.map_helpers import (
    get_all_tiles_for_map, get_tile_by_coordinates, 
    serialize_tile_for_frontend, HexCoordinate
)
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.auth_helpers import admin_required
from app_core import category_data
from bson import ObjectId
import json

map_routes = Blueprint("map_routes", __name__)


@map_routes.route("/map")
def map_view():
    """Main map page with hexagonal tile system."""
    return render_template("map.html")


@map_routes.route("/api/map/tiles")
def api_get_all_tiles():
    """API endpoint to get all map tiles for frontend rendering."""
    try:
        tiles = get_all_tiles_for_map()
        return jsonify({
            "success": True,
            "tiles": tiles
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@map_routes.route("/api/map/tile/<int:q>/<int:r>")
@map_routes.route("/api/map/tile/<int:q>/<int:r>/<int:s>")
def api_get_tile(q, r, s=None):
    """API endpoint to get details for a specific tile."""
    try:
        if s is None:
            s = -q - r
        
        tile = get_tile_by_coordinates(q, r, s)
        if not tile:
            return jsonify({
                "success": False,
                "error": "Tile not found"
            }), 404
        
        serialized_tile = serialize_tile_for_frontend(tile)
        return jsonify({
            "success": True,
            "tile": serialized_tile
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@map_routes.route("/api/map/nations")
def api_get_nations_with_colors():
    """API endpoint to get all nations with their map colors."""
    try:
        nations_db = category_data["nations"]["database"]
        nations = list(nations_db.find({}, {"name": 1, "map_color": 1}))
        
        nation_data = []
        for nation in nations:
            nation_data.append({
                "id": str(nation["_id"]),
                "name": nation.get("name", "Unknown"),
                "color": nation.get("map_color", "#888888")
            })
        
        return jsonify({
            "success": True,
            "nations": nation_data
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@map_routes.route("/admin/map/tiles")
@admin_required
def admin_tiles_list():
    """Admin page to manage map tiles."""
    tiles_db = category_data["map_tiles"]["database"]
    tiles = list(tiles_db.find({}).sort([("q", 1), ("r", 1)]))
    
    # Get nations for dropdown
    nations_db = category_data["nations"]["database"]
    nations = list(nations_db.find({}, {"name": 1}).sort("name", 1))
    
    # Get nodes for dropdown
    nodes_db = category_data["map_nodes"]["database"]
    nodes = list(nodes_db.find({}, {"name": 1}).sort("name", 1))
    
    return render_template("admin/map_tiles.html", 
                         tiles=tiles, 
                         nations=nations, 
                         nodes=nodes)


@map_routes.route("/admin/map/tile/new", methods=["GET", "POST"])
@admin_required
def admin_create_tile():
    """Admin page to create a new map tile."""
    if request.method == "POST":
        try:
            data = request.form.to_dict()
            
            # Convert coordinates to integers
            q = int(data.get("q", 0))
            r = int(data.get("r", 0))
            s = int(data.get("s", -q - r))
            
            # Validate hex coordinate constraint
            if q + r + s != 0:
                flash("Invalid coordinates: q + r + s must equal 0", "error")
                return redirect(request.url)
            
            # Check if tile already exists
            tiles_db = category_data["map_tiles"]["database"]
            existing_tile = tiles_db.find_one({"q": q, "r": r, "s": s})
            if existing_tile:
                flash("A tile already exists at these coordinates", "error")
                return redirect(request.url)
            
            # Prepare tile data
            tile_data = {
                "q": q,
                "r": r,
                "s": s,
                "terrain_type": data.get("terrain_type", "plains")
            }
            
            # Add optional fields
            if data.get("nation_owner"):
                tile_data["nation_owner"] = ObjectId(data["nation_owner"])
            
            if data.get("background_x"):
                tile_data["background_x"] = float(data["background_x"])
            
            if data.get("background_y"):
                tile_data["background_y"] = float(data["background_y"])
            
            # Handle nodes (multiple selection)
            nodes = request.form.getlist("nodes")
            if nodes:
                tile_data["nodes"] = [ObjectId(node_id) for node_id in nodes if node_id]
            
            if data.get("district"):
                tile_data["district"] = ObjectId(data["district"])
            
            # Insert the tile
            result = tiles_db.insert_one(tile_data)
            
            flash(f"Tile created successfully at ({q}, {r}, {s})", "success")
            return redirect("/admin/map/tiles")
            
        except Exception as e:
            flash(f"Error creating tile: {str(e)}", "error")
            return redirect(request.url)
    
    # GET request - show form
    nations_db = category_data["nations"]["database"]
    nations = list(nations_db.find({}, {"name": 1}).sort("name", 1))
    
    nodes_db = category_data["map_nodes"]["database"]
    nodes = list(nodes_db.find({}, {"name": 1}).sort("name", 1))
    
    terrain_types = ["plains", "forest", "hills", "mountains", "desert", "swamp", "coast", "ocean", "river"]
    
    return render_template("admin/map_tile_form.html", 
                         nations=nations, 
                         nodes=nodes, 
                         terrain_types=terrain_types,
                         tile=None)


@map_routes.route("/admin/map/tile/<tile_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_tile(tile_id):
    """Admin page to edit an existing map tile."""
    tiles_db = category_data["map_tiles"]["database"]
    tile = tiles_db.find_one({"_id": ObjectId(tile_id)})
    
    if not tile:
        flash("Tile not found", "error")
        return redirect("/admin/map/tiles")
    
    if request.method == "POST":
        try:
            data = request.form.to_dict()
            
            # Prepare update data
            update_data = {
                "terrain_type": data.get("terrain_type", "plains")
            }
            
            # Add optional fields
            if data.get("nation_owner"):
                update_data["nation_owner"] = ObjectId(data["nation_owner"])
            else:
                update_data["nation_owner"] = None
            
            if data.get("background_x"):
                update_data["background_x"] = float(data["background_x"])
            
            if data.get("background_y"):
                update_data["background_y"] = float(data["background_y"])
            
            # Handle nodes (multiple selection)
            nodes = request.form.getlist("nodes")
            if nodes:
                update_data["nodes"] = [ObjectId(node_id) for node_id in nodes if node_id]
            else:
                update_data["nodes"] = []
            
            if data.get("district"):
                update_data["district"] = ObjectId(data["district"])
            else:
                update_data["district"] = None
            
            # Update the tile
            tiles_db.update_one({"_id": ObjectId(tile_id)}, {"$set": update_data})
            
            flash(f"Tile updated successfully", "success")
            return redirect("/admin/map/tiles")
            
        except Exception as e:
            flash(f"Error updating tile: {str(e)}", "error")
            return redirect(request.url)
    
    # GET request - show form
    nations_db = category_data["nations"]["database"]
    nations = list(nations_db.find({}, {"name": 1}).sort("name", 1))
    
    nodes_db = category_data["map_nodes"]["database"]
    nodes = list(nodes_db.find({}, {"name": 1}).sort("name", 1))
    
    terrain_types = ["plains", "forest", "hills", "mountains", "desert", "swamp", "coast", "ocean", "river"]
    
    return render_template("admin/map_tile_form.html", 
                         nations=nations, 
                         nodes=nodes, 
                         terrain_types=terrain_types,
                         tile=tile)


@map_routes.route("/admin/map/tile/<tile_id>/delete", methods=["POST"])
@admin_required
def admin_delete_tile(tile_id):
    """Admin endpoint to delete a map tile."""
    try:
        tiles_db = category_data["map_tiles"]["database"]
        result = tiles_db.delete_one({"_id": ObjectId(tile_id)})
        
        if result.deleted_count > 0:
            flash("Tile deleted successfully", "success")
        else:
            flash("Tile not found", "error")
            
    except Exception as e:
        flash(f"Error deleting tile: {str(e)}", "error")
    
    return redirect("/admin/map/tiles")
