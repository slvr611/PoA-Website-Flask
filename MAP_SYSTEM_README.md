# Hexagonal Map System Implementation

This document describes the comprehensive hexagonal map system implemented for the Path of Ages website.

## Overview

The new map system replaces the simple image-based map with a fully interactive hexagonal tile-based system that integrates with the game's database. Each tile on the map can have:

- **Nation ownership** with color-coded territory overlays
- **Multiple nodes** (resource, strategic, trade, magical, cultural, defensive)
- **Districts** (placeholder for future implementation)
- **Terrain types** with visual representation
- **Hexagonal coordinates** for precise positioning

## Key Features

### 🗺️ Interactive Map Interface
- **Canvas-based rendering** for smooth performance
- **Multiple view modes**: Terrain, Political, and Nodes
- **Zoom and pan controls** with mouse/touch support
- **Tile selection** with detailed information panel
- **Responsive design** that works on desktop and mobile

### 🎯 Hexagonal Coordinate System
- Uses **cube coordinates** (q, r, s) where q + r + s = 0
- Proper **distance calculations** between tiles
- **Neighbor finding** algorithms
- **Pixel-to-hex** and **hex-to-pixel** conversion utilities

### 🏛️ Database Integration
- **map_tiles collection**: Stores all tile data
- **map_nodes collection**: Stores node definitions
- **Updated nations schema**: Includes map colors and territory
- **API endpoints** for frontend data consumption

### 👑 Nation Territory System
- Each nation has a **unique map color**
- Tiles show **light color overlays** when controlled by nations
- **Political view mode** emphasizes territorial boundaries
- **Territory management** through admin interface

### 🏗️ Admin Tools
- **Tile management interface** for creating/editing tiles
- **Coordinate validation** ensures proper hex positioning
- **Bulk operations** support for efficient map creation
- **Node assignment** to tiles through checkboxes

## File Structure

```
├── helpers/
│   └── map_helpers.py              # Hexagonal coordinate utilities
├── routes/
│   └── map_routes.py               # Map routes and API endpoints
├── templates/
│   ├── map.html                    # Main map interface
│   └── admin/
│       ├── map_tiles.html          # Admin tile list
│       └── map_tile_form.html      # Admin tile creation/editing
├── static/
│   └── js/
│       └── hexmap.js               # Interactive map JavaScript
├── json-data/schemas/
│   ├── map_tiles.json              # Tile database schema
│   ├── map_nodes.json              # Node database schema
│   └── nations.json                # Updated with map_color field
└── sample_data/
    └── sample_nations.py           # Sample data generation script
```

## Database Schema

### Map Tiles Collection
```json
{
  "q": 0,                          // Q coordinate (integer)
  "r": 0,                          // R coordinate (integer) 
  "s": 0,                          // S coordinate (integer, q+r+s=0)
  "terrain_type": "plains",        // Terrain type (enum)
  "nation_owner": "ObjectId",      // Owning nation (optional)
  "nodes": ["ObjectId"],           // Array of node IDs
  "district": "ObjectId",          // District ID (optional)
  "background_x": 0.5,             // Background image X position (0-1)
  "background_y": 0.5              // Background image Y position (0-1)
}
```

### Map Nodes Collection
```json
{
  "name": "Iron Mine",
  "node_type": "resource",         // resource, strategic, trade, magical, cultural, defensive
  "description": "Rich iron ore deposit",
  "resource_type": "iron",         // For resource nodes
  "resource_amount": 3,            // Amount per turn
  "modifiers": {                   // Stat bonuses
    "iron_production": 3
  },
  "requirements": {},              // Prerequisites to use
  "image_path": "/path/to/icon"    // Node icon (optional)
}
```

### Updated Nations Schema
```json
{
  "name": "Empire Name",
  "map_color": "#FF4444",          // Hex color for territory overlay
  "territory_tiles": ["ObjectId"] // Array of controlled tile IDs
}
```

## API Endpoints

- `GET /map` - Main map interface
- `GET /api/map/tiles` - Get all tiles for rendering
- `GET /api/map/tile/<q>/<r>` - Get specific tile details
- `GET /api/map/nations` - Get nations with colors
- `GET /admin/map/tiles` - Admin tile management (admin only)
- `POST /admin/map/tile/new` - Create new tile (admin only)
- `POST /admin/map/tile/<id>/edit` - Edit existing tile (admin only)
- `POST /admin/map/tile/<id>/delete` - Delete tile (admin only)

## View Modes

### 🌍 Terrain View (Default)
- Shows terrain types with distinct colors
- Plains (light green), Forest (dark green), Hills (brown), etc.
- Basic tile borders in black

### 🏛️ Political View
- Overlays nation colors on territories
- Thicker borders in nation colors for controlled tiles
- Shows territorial boundaries clearly

### 🎯 Nodes View
- Displays node indicators on tiles
- Color-coded dots for different node types:
  - Resource (gold), Strategic (red), Trade (green)
  - Magical (purple), Cultural (pink), Defensive (brown)

## Usage Instructions

### For Players
1. Navigate to `/map` to view the interactive map
2. Use the view mode buttons to switch between terrain, political, and nodes views
3. Click on tiles to see detailed information in the right panel
4. Use zoom controls (+/-) or mouse wheel to zoom in/out
5. Drag to pan around the map
6. Use the "Center" button to reset the view

### For Administrators
1. Go to `/admin/map/tiles` to manage map tiles
2. Click "Create New Tile" to add tiles to the map
3. Enter hexagonal coordinates (ensure q + r + s = 0)
4. Select terrain type, nation owner, and nodes
5. Set background position for image alignment
6. Use the edit/delete buttons to modify existing tiles

### Setting Up Sample Data
1. Ensure MongoDB connection is configured
2. Run `python sample_data/sample_nations.py` to create:
   - 8 sample nations with unique colors
   - 8 sample nodes of different types
   - 49 sample tiles in a 7x7 hex grid

## Technical Details

### Hexagonal Mathematics
The system uses the "cube coordinate" system for hexagons:
- Three coordinates (q, r, s) with constraint q + r + s = 0
- Distance between hexes: `(|q1-q2| + |r1-r2| + |s1-s2|) / 2`
- Neighbors found by adding direction vectors: `[(1,0,-1), (1,-1,0), (0,-1,1), (-1,0,1), (-1,1,0), (0,1,-1)]`

### Canvas Rendering
- Uses HTML5 Canvas for smooth rendering
- Separate background and foreground canvases
- Efficient redraw system with viewport culling
- Touch and mouse event handling for interaction

### Performance Considerations
- Tiles are only rendered when visible in viewport
- Efficient coordinate-to-pixel conversion
- Minimal DOM manipulation for better performance
- Responsive canvas sizing

## Future Enhancements

- **District system integration** when districts are implemented
- **Background image integration** with proper tile positioning
- **Pathfinding algorithms** for movement calculation
- **Territory expansion mechanics** 
- **Battle visualization** on the map
- **Trade route display** between nodes
- **Seasonal/weather effects** on terrain
- **Mini-map** for navigation of large maps

## Troubleshooting

### Common Issues
1. **Tiles not appearing**: Check database connection and ensure tiles exist
2. **Coordinate errors**: Verify q + r + s = 0 for all tiles
3. **Nation colors not showing**: Ensure nations have map_color field set
4. **Canvas not responsive**: Check CSS and JavaScript event handlers

### Debug Mode
Add `?debug=1` to the map URL to enable debug features:
- Coordinate display on tiles
- Performance metrics
- Console logging of tile data

This comprehensive map system provides a solid foundation for territorial gameplay and can be extended with additional features as needed.
