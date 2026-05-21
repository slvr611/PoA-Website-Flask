// hex-map.js — Interactive hex map viewer for Path of Ages
// Flat-top hexagons, axial coordinates (q, r).
// Grid defined by offset (col, row) converted to axial for storage/lookup.

'use strict';

// Terrain fill colors — edit hex values here to change map appearance.
// disconnected and urban are display-only and cannot be painted.
const TERRAIN_COLORS = {
    plains:          '#87ff87',
    forest:          '#38b839',
    dense_forest:    '#286022',
    hill:            '#f38561',
    mountain:        '#4c4a47',
    river:           '#ff85d8',
    desert:          '#fbea4e',
    tundra:          '#ffffff',
    marsh:           '#817b26',
    urban:           '#808080',     // display-only
    hazardous_land:  '#8f203b',
    shallow_water:   '#42e3df',
    deep_water:      '#2b85d5',
    hazardous_water: '#581224',
    disconnected:    '#252525',     // display-only
};

const TERRAIN_NAMES = {
    plains: 'Plains', forest: 'Forest', dense_forest: 'Dense Forest',
    hill: 'Hill', mountain: 'Mountain', river: 'River', desert: 'Desert',
    tundra: 'Tundra', marsh: 'Marsh', urban: 'Urban',
    hazardous_land: 'Hazardous Land', shallow_water: 'Shallow Water',
    deep_water: 'Deep Water', hazardous_water: 'Hazardous Water',
    disconnected: 'Disconnected',
};

const NODE_SYMBOLS = {
    resource: '◆', strategic: '✦', trade: '◈',
    magical: '✧', cultural: '◉', defensive: '⊕',
};

// Terrain options available for painting/editing (excludes display-only types)
const TERRAIN_OPTIONS = Object.keys(TERRAIN_COLORS).filter(k => k !== 'disconnected' && k !== 'urban');
const NODE_TYPES      = Object.keys(NODE_SYMBOLS);
const CITY_TYPES      = ['generic','commerce','heritage','metropolis','capital','sacred','fortified','fortress'];
const DISTRICT_TYPES  = ['farming','logging','mining','pasture','arcane','barracks','harbor','market','temple','workshop'];

// Resource definitions (mirrors app_core.py json_data)
const GENERAL_RESOURCES = [
    {key:'food',name:'Food'}, {key:'wood',name:'Wood'}, {key:'stone',name:'Stone'},
    {key:'mounts',name:'Mounts'}, {key:'research',name:'Research'}, {key:'magic',name:'Magic'},
];
const UNIQUE_RESOURCES = [
    {key:'bronze',name:'Bronze'}, {key:'iron',name:'Iron'},
];
const LUXURY_RESOURCES = [
    {key:'narcotics',name:'Narcotics'}, {key:'spices',name:'Spices'},
    {key:'medicinal_herbs',name:'Medicinal Herbs'}, {key:'dyes',name:'Dyes'},
    {key:'magical_crystals',name:'Magical Crystals'}, {key:'gold',name:'Gold'},
    {key:'moonstone',name:'Moonstone'}, {key:'furs',name:'Furs'},
    {key:'quintessence',name:'Quintessence'},
];
const ALL_RESOURCES     = [...GENERAL_RESOURCES, ...UNIQUE_RESOURCES, ...LUXURY_RESOURCES];
const LUXURY_KEYS       = new Set(LUXURY_RESOURCES.map(r => r.key));
const RESOURCE_NAME     = Object.fromEntries(ALL_RESOURCES.map(r => [r.key, r.name]));

// Deterministic unique color per resource key
function _resourceColor(key) {
    let h = 0;
    for (const c of key) h = (h * 31 + c.charCodeAt(0)) & 0xFFFF;
    const hue = h % 360;
    const s = 0.70, l = 0.55;
    const cv = (1 - Math.abs(2*l - 1)) * s;
    const x  = cv * (1 - Math.abs((hue/60) % 2 - 1));
    const m  = l - cv/2;
    let r,g,b;
    if      (hue <  60) [r,g,b] = [cv, x,  0 ];
    else if (hue < 120) [r,g,b] = [x,  cv, 0 ];
    else if (hue < 180) [r,g,b] = [0,  cv, x ];
    else if (hue < 240) [r,g,b] = [0,  x,  cv];
    else if (hue < 300) [r,g,b] = [x,  0,  cv];
    else                [r,g,b] = [cv, 0,  x ];
    const to2 = v => Math.round((v+m)*255).toString(16).padStart(2,'0');
    return `#${to2(r)}${to2(g)}${to2(b)}`;
}
// Populated in HexMapViewer.loadConfig() from the server's resource_colors map.
// Falls back to _resourceColor() for any key not provided by the server.
const RESOURCE_COLORS = Object.fromEntries(ALL_RESOURCES.map(r => [r.key, _resourceColor(r.key)]));

// Alpha applied to all terrain fills so the background image shows through.
const TERRAIN_FILL_ALPHA = 0.40;

// Land movement costs per terrain (mirrors terrains.json speed_cost).
// Terrain with no entry → treated as impassable by the admin-range Dijkstra.
const TERRAIN_MOVE_COST = {
    plains:         1,
    urban:          1,
    desert:         1,
    tundra:         1,
    forest:         2,
    hill:           2,
    marsh:          2,
    hazardous_land: 2,
    dense_forest:    3,
    mountain:        3,
    river:           3,
    shallow_water:   2,
    deep_water:      3,
    hazardous_water: 2,
};
const _TERRAIN_MOVE_IMPASSABLE = 9999;

// Portal colors — each color identifies a pair (or group) of connected portal tiles.
const PORTAL_COLORS = {
    red:    '#e04444',
    blue:   '#4466e0',
    green:  '#38b050',
    yellow: '#d4b020',
    purple: '#9040d0',
    orange: '#d06020',
    cyan:   '#18a8c8',
    white:  '#c8c8c8',
    pink:   '#e060b0',
    teal:   '#28a898',
};
const PORTAL_COLOR_NAMES = Object.keys(PORTAL_COLORS);

// Dash patterns per route tier.
const ROUTE_DASHES = [
    null,              // index 0 unused
    [4, 7],            // tier 1 — small dots
    [14, 4, 3, 4],     // tier 2 — long-short alternating
    [17, 6],           // tier 3 — long dashes
];

// Axial hex neighbor directions (flat-top).
const _AXIAL_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];

// Minimal binary min-heap used by the admin-range Dijkstra.
// Each element is [priority, ...data].
class _MinHeap {
    constructor() { this._h = []; }
    get size() { return this._h.length; }
    push(v) {
        this._h.push(v);
        let i = this._h.length - 1;
        while (i > 0) {
            const p = (i - 1) >> 1;
            if (this._h[p][0] <= this._h[i][0]) break;
            [this._h[p], this._h[i]] = [this._h[i], this._h[p]];
            i = p;
        }
    }
    pop() {
        const top = this._h[0];
        const last = this._h.pop();
        if (this._h.length > 0) {
            this._h[0] = last;
            let i = 0;
            for (;;) {
                let s = i, l = 2*i+1, r = 2*i+2;
                if (l < this._h.length && this._h[l][0] < this._h[s][0]) s = l;
                if (r < this._h.length && this._h[r][0] < this._h[s][0]) s = r;
                if (s === i) break;
                [this._h[s], this._h[i]] = [this._h[i], this._h[s]];
                i = s;
            }
        }
        return top;
    }
}

// Precomputed flat-top hex corner trig (0°, 60°, 120°, 180°, 240°, 300°).
const _HEX_COS = Array.from({ length: 6 }, (_, i) => Math.cos(Math.PI / 3 * i));
const _HEX_SIN = Array.from({ length: 6 }, (_, i) => Math.sin(Math.PI / 3 * i));


class HexMapViewer {
    constructor(canvasId, detailPanelId, isAdmin) {
        this.canvas      = document.getElementById(canvasId);
        this.ctx         = this.canvas.getContext('2d');
        this.detailPanel = document.getElementById(detailPanelId);
        this.isAdmin     = !!isAdmin;

        // Grid config (loaded from API, updated per-session from snapshot)
        this.cols    = 20;
        this.rows    = 15;
        this.hexSize = 40;

        // Background — plain image path
        this.bgUrl   = null;
        this.bgImage = null;
        // Background — OSD viewer for DZI support (set via attachBgViewer)
        this._bgViewer = null;
        // Background alignment tuning (world-pixel offset and scale multiplier)
        this.bgOffsetX = 0;
        this.bgOffsetY = 0;
        this.bgScale   = 1.0;

        // Layers
        this.layers = {
            terrain:   false,
            political: true,
            regions:   false,
            nodes:     true,
            buildings: true,
            roads:     false,
        };

        // Paint modes — at most one active at a time
        this.paintMode       = null;  // terrain key, or null
        this.paintNationMode = null;  // nation name, or null
        this.paintRegionMode = null;  // region name, or null
        this.paintRouteMode  = null;  // {owner, tier} or {erase:true}, or null
        this.forbiddenTiles  = new Set();  // "q,r" keys that violate the selected nation's restrictions
        this._paintErrorTimer = null;
        this._painting    = false;
        this._midPainting = false;  // middle-mouse unown paint
        this._lastPainted = null;  // "q,r" of last painted tile (avoids duplicate API calls)

        // Undo stack — each entry is {type, tiles: Map(key → originalValue)}
        this._undoStack        = [];
        this._currentUndoBatch = null;

        // Nation cache for owner autocomplete and buildings
        this._nationList      = null;  // [{name, color, overlord}]
        this._nationOverlords = {};   // name -> overlord name string
        this._nationBuildings = {};    // name -> {cities, districts}
        this._wonderList      = null;  // [{id, name, owner_nation}]

        // Nation label cache — rebuilt when tile ownership changes
        this._nationLabels = null;  // Map<name, {wx, wy, wBboxW, wBboxH}>

        // District def image cache: def_key -> HTMLImageElement (null if load failed)
        this._districtImages  = {};    // def_key -> HTMLImageElement | null
        // City type image cache: type_key -> HTMLImageElement (null if load failed)
        this._cityTypeImages  = {};    // city_type -> HTMLImageElement | null
        // Default wonder image shown for all wonders on the map
        this._wonderDefaultImage = null;   // HTMLImageElement | null

        // Tile data
        this.tiles        = new Map();   // "q,r" -> tile object
        this.nationColors = {};
        this.regionColors = {};          // region name -> hex color

        // Admin range
        this.nationAdmin      = new Map();  // nation name -> stored administration value
        this.nationNomadic    = new Map();  // nation name -> boolean (is nomadic)
        this._outOfRangeTiles = new Set();  // "q,r" keys of owned tiles beyond admin range

        // Render caches — invalidated when colors change
        this._terrainFillCache = {};  // terrain key  → rgba string
        this._rgbaCache        = {};  // "name:alpha" → rgba string

        // Cached Path2D for full-grid outline — rebuilt when grid structure changes.
        // ctx.stroke(path) is ~free JS vs 80K canvas API calls per frame at full zoom-out.
        this._outlinePath  = null;
        // Cached Path2D per terrain type — rebuilt when tile terrain data changes.
        this._terrainPaths = null;  // { terrain_key → Path2D }

        // Perf overlay — toggle with viewer.showPerfOverlay = true
        this.showPerfOverlay = false;
        this._perf = { fps: 0, frameMs: 0, cellCount: 0, drawLines: false, showIcons: false,
                       terrainBuckets: 0, nationBuckets: 0, _lastT: 0, _fpsArr: [],
                       sections: { setup: 0, cells: 0, terrain: 0, nation: 0, outlines: 0, icons: 0 } };

        // Viewport
        this.panX    = 0;
        this.panY    = 0;
        this.zoom    = 1.0;
        this.minZoom = 0.03;    // very zoomed-out overview
        this.maxZoom = 14.0;    // very detailed close-up

        // Interaction
        this.isDragging = false;
        this.hasMoved   = false;
        this.dragStart  = { x: 0, y: 0 };
        this.panStart   = { x: 0, y: 0 };
        this.selectedTile = null;
        this._pinchDist   = null;

        this._bindEvents();
        this._initCanvas();
    }

    // -----------------------------------------------------------------------
    // Setup
    // -----------------------------------------------------------------------

    _initCanvas() {
        this._resize();
        window.addEventListener('resize', () => { this._resize(); this.render(); });
    }

    _resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width  = Math.max(rect.width,  200);
        this.canvas.height = Math.max(rect.height || 500, 200);
    }

    /** Call when the canvas container becomes visible after being hidden. */
    show() {
        this._resize();
        this.render();
    }

    // -----------------------------------------------------------------------
    // Data loading
    // -----------------------------------------------------------------------

    async loadConfig() {
        const resp = await fetch('/api/hex-map/config');
        const cfg  = await resp.json();
        this.cols      = cfg.cols         || 20;
        this.rows      = cfg.rows         || 15;
        this.hexSize   = cfg.hex_size     || 40;
        this.bgOffsetX = cfg.bg_offset_x  ?? 0;
        this.bgOffsetY = cfg.bg_offset_y  ?? 0;
        this.bgScale   = cfg.bg_scale     || 1.0;
        if (cfg.resource_colors) {
            Object.assign(RESOURCE_COLORS, cfg.resource_colors);
        }
        this._centerView();
        this._scheduleOutlineRebuild();
        this._scheduleTerrainRebuild();
    }

    /**
     * Attach a passive OSD viewer to use as a DZI background layer.
     * Pass null to detach. When attached, the canvas background is transparent
     * and the hex map's render loop keeps the OSD viewport in sync.
     */
    attachBgViewer(v) {
        this._bgViewer = v || null;
        this.render();
    }

    /**
     * Sync the passive OSD background viewer's viewport so it lines up
     * exactly with the hex canvas's current pan/zoom state.
     */
    _syncBgViewer() {
        if (!this._bgViewer) return;
        try {
            if (!this._bgViewer.isOpen()) return;
            const ww   = this._worldWidth() * (this.bgScale || 1.0);
            const offX = this.bgOffsetX || 0;
            const offY = this.bgOffsetY || 0;
            const l    = (-this.panX / this.zoom - offX) / ww;
            const t    = (-this.panY / this.zoom - offY) / ww;
            const w    = this.canvas.width  / this.zoom / ww;
            const h    = this.canvas.height / this.zoom / ww;
            this._bgViewer.viewport.fitBounds(
                new OpenSeadragon.Rect(l, t, w, h), true
            );
        } catch (_) {}
    }

    /** Update grid/background config and re-render. Only provided keys are changed. */
    setGridConfig({ cols, rows, hexSize, bgOffsetX, bgOffsetY, bgScale } = {}) {
        let recenter = false;
        if (cols     != null && cols     !== this.cols)    { this.cols    = cols;     recenter = true; }
        if (rows     != null && rows     !== this.rows)    { this.rows    = rows;     recenter = true; }
        if (hexSize  != null && hexSize  !== this.hexSize) { this.hexSize = hexSize;  recenter = true; }
        if (bgOffsetX != null) this.bgOffsetX = bgOffsetX;
        if (bgOffsetY != null) this.bgOffsetY = bgOffsetY;
        if (bgScale   != null) this.bgScale   = bgScale;
        if (recenter) { this._centerView(); this._scheduleOutlineRebuild(); this._scheduleTerrainRebuild(); this._computeNationLabels(); }
        this.render();
    }

    /** Return a snapshot of current grid/background config as plain JSON. */
    getGridConfig() {
        return {
            cols:      this.cols,
            rows:      this.rows,
            hexSize:   this.hexSize,
            bgOffsetX: this.bgOffsetX,
            bgOffsetY: this.bgOffsetY,
            bgScale:   this.bgScale,
        };
    }

    /**
     * Load a plain-image background. Pass null to clear.
     * Prefer attachBgViewer() for DZI files.
     */
    async setBackground(url) {
        const newUrl = url || null;
        if (newUrl === this.bgUrl && (this.bgImage || !this.bgUrl)) return;
        this._bgViewer = null;  // detach any OSD background
        this.bgUrl   = newUrl;
        this.bgImage = null;
        if (this.bgUrl) {
            await this._loadBg(this.bgUrl);
        }
        this.render();
    }

    _loadBg(url) {
        return new Promise(resolve => {
            const img   = new Image();
            img.onload  = () => { this.bgImage = img; resolve(); };
            img.onerror = () => resolve();
            img.src     = url;
        });
    }

    /**
     * Load tiles for a session. sessionNum=null loads the live state.
     * If the response includes grid config (cols/rows/hex_size), applies them.
     */
    async loadTiles(sessionNum) {
        const url  = sessionNum != null
            ? `/api/hex-map/tiles/${sessionNum}`
            : '/api/hex-map/tiles';
        const [resp, imgResp, wonderImgResp, cityImgResp] = await Promise.all([
            fetch(url),
            fetch('/api/district-defs/image-map'),
            fetch('/api/wonders/default-image'),
            fetch('/api/cities/image-map'),
        ]);
        const data          = await resp.json();
        const imgMap        = await imgResp.json().catch(() => ({}));
        const wonderImgData = await wonderImgResp.json().catch(() => ({}));
        const cityImgMap    = await cityImgResp.json().catch(() => ({}));

        this.tiles = new Map();
        for (const tile of (data.tiles || [])) {
            this.tiles.set(`${tile.q},${tile.r}`, tile);
        }
        this.nationColors = data.nation_colors || {};
        this._rgbaCache   = {};

        // Preload district images, city type images, and single default wonder image
        this._loadDistrictImages(imgMap);
        this._loadCityTypeImages(cityImgMap);
        this._loadWonderDefaultImage(wonderImgData.url || '');

        // Apply session-specific grid dimensions when present
        let recenter = false;
        if (data.cols     && data.cols     !== this.cols)    { this.cols    = data.cols;     recenter = true; }
        if (data.rows     && data.rows     !== this.rows)    { this.rows    = data.rows;     recenter = true; }
        if (data.hex_size && data.hex_size !== this.hexSize) { this.hexSize = data.hex_size; recenter = true; }
        if (recenter) this._centerView();
        this._scheduleOutlineRebuild();
        this._scheduleTerrainRebuild();
        this._nationLabels = null;
        this.nationAdmin      = new Map();
        this.nationNomadic    = new Map();
        this._outOfRangeTiles = new Set();
        this._ensureNationList().then(() => {
            this._computeNationLabels();
            this._computeAllAdminRanges();
            this.render();
        });

        this.render();
    }

    async loadRegions() {
        try {
            const data = await (await fetch('/api/hex-map/region-list')).json();
            this.regionColors = {};
            for (const r of (data.regions || [])) this.regionColors[r.name] = r.color;
            this._rgbaCache = {};
        } catch (_) {}
    }

    _loadDistrictImages(imgMap) {
        let pending = 0;
        for (const [key, url] of Object.entries(imgMap)) {
            if (this._districtImages[key]) continue;  // already cached
            pending++;
            const img = new Image();
            img.onload  = () => { this._districtImages[key] = img; if (--pending === 0) this.render(); };
            img.onerror = () => { this._districtImages[key] = null; if (--pending === 0) this.render(); };
            img.src = url;
        }
    }

    _loadCityTypeImages(imgMap) {
        let pending = 0;
        for (const [key, url] of Object.entries(imgMap)) {
            if (this._cityTypeImages[key]) continue;
            pending++;
            const img = new Image();
            img.onload  = () => { this._cityTypeImages[key] = img; if (--pending === 0) this.render(); };
            img.onerror = () => { this._cityTypeImages[key] = null; if (--pending === 0) this.render(); };
            img.src = url;
        }
    }

    _loadWonderDefaultImage(url) {
        if (!url) return;
        const img = new Image();
        img.onload  = () => { this._wonderDefaultImage = img; this.render(); };
        img.onerror = () => { this._wonderDefaultImage = null; };
        img.src = url;
    }

    // -----------------------------------------------------------------------
    // Coordinate math
    // -----------------------------------------------------------------------

    // Offset (col, row) → axial (q, r)  [flat-top, even-q shift]
    _offsetToAxial(col, row) {
        return { q: col, r: row - Math.floor(col / 2) };
    }

    // Axial → canvas world-space pixel (center of hex)
    _axialToPixel(q, r) {
        const s = this.hexSize;
        return {
            x: s * 1.5 * q,
            y: s * Math.sqrt(3) * (r + q / 2),
        };
    }

    // Canvas world-space pixel → nearest axial coord
    _pixelToAxial(px, py) {
        const s = this.hexSize;
        const q = (2 / 3 * px) / s;
        const r = (-px / 3 + Math.sqrt(3) / 3 * py) / s;
        return this._axialRound(q, r);
    }

    _axialRound(q, r) {
        const s  = -q - r;
        let rq   = Math.round(q), rr = Math.round(r), rs = Math.round(s);
        const dq = Math.abs(rq - q), dr = Math.abs(rr - r), ds = Math.abs(rs - s);
        if      (dq > dr && dq > ds) rq = -rr - rs;
        else if (dr > ds)            rr = -rq - rs;
        return { q: rq, r: rr };
    }

    // Screen pixel → world pixel
    _screenToWorld(sx, sy) {
        return {
            x: (sx - this.panX) / this.zoom,
            y: (sy - this.panY) / this.zoom,
        };
    }

    // Grid world dimensions
    _worldWidth()  { return (this.cols * 1.5 + 0.5) * this.hexSize; }
    _worldHeight() { return (this.rows + 0.5) * this.hexSize * Math.sqrt(3); }

    _centerView() {
        const ww = this._worldWidth(), wh = this._worldHeight();
        const fitZoom = Math.min(
            this.canvas.width  / ww * 0.9,
            this.canvas.height / wh * 0.9,
        );
        this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, fitZoom));
        this.panX = (this.canvas.width  - ww * this.zoom) / 2;
        this.panY = (this.canvas.height - wh * this.zoom) / 2;
    }

    // -----------------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------------

    // Add a single hex to the current path without calling beginPath — for batching.
    _hexSubpath(ctx, cx, cy, size) {
        ctx.moveTo(cx + size * _HEX_COS[0], cy + size * _HEX_SIN[0]);
        for (let i = 1; i < 6; i++) {
            ctx.lineTo(cx + size * _HEX_COS[i], cy + size * _HEX_SIN[i]);
        }
        ctx.closePath();
    }

    // Begin a new path for a single hex (selection highlight, etc.).
    _hexPath(ctx, cx, cy, size) {
        ctx.beginPath();
        this._hexSubpath(ctx, cx, cy, size);
    }

    // Build a Path2D covering every hex in the grid using an SVG path string.
    // SVG string construction + a single C++ parse is far faster than 80K canvas API calls.
    _buildOutlinePath() {
        const inner = this.hexSize - 1;
        const ox = _HEX_COS.map(c => inner * c);
        const oy = _HEX_SIN.map(s => inner * s);
        const parts = [];
        for (let col = 0; col < this.cols; col++) {
            for (let row = 0; row < this.rows; row++) {
                const { q, r } = this._offsetToAxial(col, row);
                const { x, y } = this._axialToPixel(q, r);
                parts.push(
                    `M${x+ox[0]},${y+oy[0]}` +
                    `L${x+ox[1]},${y+oy[1]}` +
                    `L${x+ox[2]},${y+oy[2]}` +
                    `L${x+ox[3]},${y+oy[3]}` +
                    `L${x+ox[4]},${y+oy[4]}` +
                    `L${x+ox[5]},${y+oy[5]}Z`
                );
            }
        }
        this._outlinePath = new Path2D(parts.join(''));
    }

    // Invalidate the cached path and schedule a rebuild during idle time.
    _scheduleOutlineRebuild() {
        this._outlinePath = null;
        const build = () => this._buildOutlinePath();
        if (window.requestIdleCallback) {
            window.requestIdleCallback(build, { timeout: 3000 });
        } else {
            setTimeout(build, 100);
        }
    }

    // Build one Path2D per terrain type covering every hex of that terrain.
    _buildTerrainPaths() {
        const inner = this.hexSize - 1;
        const ox = _HEX_COS.map(c => inner * c);
        const oy = _HEX_SIN.map(s => inner * s);
        const groups = {};
        for (let col = 0; col < this.cols; col++) {
            for (let row = 0; row < this.rows; row++) {
                const { q, r } = this._offsetToAxial(col, row);
                const { x, y } = this._axialToPixel(q, r);
                const tile    = this.tiles.get(`${q},${r}`);
                const terrain = tile ? (tile.terrain || 'disconnected') : 'disconnected';
                if (!groups[terrain]) groups[terrain] = [];
                groups[terrain].push(
                    `M${x+ox[0]},${y+oy[0]}` +
                    `L${x+ox[1]},${y+oy[1]}` +
                    `L${x+ox[2]},${y+oy[2]}` +
                    `L${x+ox[3]},${y+oy[3]}` +
                    `L${x+ox[4]},${y+oy[4]}` +
                    `L${x+ox[5]},${y+oy[5]}Z`
                );
            }
        }
        this._terrainPaths = {};
        for (const [terrain, parts] of Object.entries(groups)) {
            this._terrainPaths[terrain] = new Path2D(parts.join(''));
        }
    }

    _scheduleTerrainRebuild() {
        this._terrainPaths = null;
        const build = () => this._buildTerrainPaths();
        if (window.requestIdleCallback) {
            window.requestIdleCallback(build, { timeout: 3000 });
        } else {
            setTimeout(build, 100);
        }
    }

    _drawHex(ctx, cx, cy, size, fill, stroke, lineWidth) {
        this._hexPath(ctx, cx, cy, size);
        if (fill)   { ctx.fillStyle = fill; ctx.fill(); }
        if (stroke) {
            ctx.strokeStyle = stroke;
            ctx.lineWidth   = (lineWidth || 1) / this.zoom;
            ctx.stroke();
        }
    }

    // Convert a solid hex color + alpha into an rgba string.
    _hexToRgba(hexColor, alpha) {
        const r = parseInt(hexColor.slice(1, 3), 16);
        const g = parseInt(hexColor.slice(3, 5), 16);
        const b = parseInt(hexColor.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    _terrainFill(terrain) {
        return this._terrainFillCache[terrain]
            || (this._terrainFillCache[terrain] =
                this._hexToRgba(TERRAIN_COLORS[terrain] || '#555555', TERRAIN_FILL_ALPHA));
    }

    _nationRgba(name, alpha) {
        const key = `${name}:${alpha}`;
        return this._rgbaCache[key]
            || (this._rgbaCache[key] =
                this._hexToRgba(this.nationColors[name] || '#888888', alpha));
    }

    _regionRgba(name, alpha) {
        const key = `region:${name}:${alpha}`;
        return this._rgbaCache[key]
            || (this._rgbaCache[key] =
                this._hexToRgba(this.regionColors[name] || '#888888', alpha));
    }

    // -----------------------------------------------------------------------
    // Viewport culling — returns iterator over [col, row] pairs in view
    // -----------------------------------------------------------------------
    *_visibleCells() {
        const s    = this.hexSize;
        const sq3  = Math.sqrt(3);
        const wl   = -this.panX / this.zoom;
        const wt   = -this.panY / this.zoom;
        const wr   = wl + this.canvas.width  / this.zoom;
        const wb   = wt + this.canvas.height / this.zoom;
        const margin = s * 2;

        const cMin = Math.max(0, Math.floor((wl - margin) / (s * 1.5)) - 1);
        const cMax = Math.min(this.cols - 1, Math.ceil((wr + margin) / (s * 1.5)) + 1);

        for (let col = cMin; col <= cMax; col++) {
            // For odd columns the hex centers are shifted down by 0.5 * hex_height,
            // so we subtract that to recover offset row from world-y.
            const shift = (col % 2 === 1) ? -0.5 : 0;
            const rMin = Math.max(0, Math.floor((wt - margin) / (s * sq3) + shift) - 1);
            const rMax = Math.min(this.rows - 1, Math.ceil((wb + margin) / (s * sq3) + shift) + 1);
            for (let row = rMin; row <= rMax; row++) {
                yield [col, row];
            }
        }
    }

    render() {
        const _t0 = performance.now();
        const ctx = this.ctx;
        const W = this.canvas.width, H = this.canvas.height;
        ctx.clearRect(0, 0, W, H);

        if (this._bgViewer) {
            this._syncBgViewer();
        } else {
            ctx.fillStyle = '#1a1a1a';
            ctx.fillRect(0, 0, W, H);
        }

        ctx.save();
        ctx.translate(this.panX, this.panY);
        ctx.scale(this.zoom, this.zoom);

        if (this.bgImage) {
            ctx.drawImage(this.bgImage, 0, 0, this._worldWidth(), this._worldHeight());
        }

        const inner      = this.hexSize - 1;
        const showIcons  = this.zoom >= 0.25;
        // Skip hex outlines when they'd be < 1 screen pixel — invisible and wastes time.
        const drawLines  = inner * this.zoom >= 1.0;

        const hasTerrain   = this.layers.terrain;
        const hasPolitical = this.layers.political;
        const hasRegions   = this.layers.regions;

        // Flat x/y arrays per fill style — push(x, y) pairs to avoid per-hex object allocation.
        const terrainBuckets = {};  // fillStyle → Float64Array-like flat [x,y,x,y,…]
        const regionBuckets  = {};
        const nationBuckets  = {};
        const portalBuckets  = {};  // portal hex color → [x,y,…]

        // Parallel flat arrays for all visible hexes (used for outline pass and icons).
        const allX    = [];
        const allY    = [];
        const allKey  = [];
        const allTile = [];

        const _t1 = performance.now();
        for (const [col, row] of this._visibleCells()) {
            const { q, r } = this._offsetToAxial(col, row);
            const { x, y } = this._axialToPixel(q, r);
            const key       = `${q},${r}`;
            const tile      = this.tiles.get(key);

            allX.push(x);
            allY.push(y);
            allKey.push(key);
            allTile.push(tile);

            if (hasTerrain && !this._terrainPaths) {
                const fill = this._terrainFill(tile ? (tile.terrain || 'disconnected') : 'disconnected');
                if (!terrainBuckets[fill]) terrainBuckets[fill] = [];
                terrainBuckets[fill].push(x, y);
            }

            if (hasRegions && tile && tile.region) {
                const fill = this._regionRgba(tile.region, 0.45);
                if (!regionBuckets[fill]) regionBuckets[fill] = [];
                regionBuckets[fill].push(x, y);
            }

            if (hasPolitical && tile && tile.owner) {
                const fill = this._nationRgba(tile.owner, 0.38);
                if (!nationBuckets[fill]) nationBuckets[fill] = [];
                nationBuckets[fill].push(x, y);
            }

            if (tile && tile.portal?.color) {
                const portalHex = PORTAL_COLORS[tile.portal.color] || tile.portal.color;
                if (!portalBuckets[portalHex]) portalBuckets[portalHex] = [];
                portalBuckets[portalHex].push(x, y);
            }
        }

        // ── Terrain fills ─────────────────────────────────────────────────────
        const _t2 = performance.now();
        if (hasTerrain) {
            if (this._terrainPaths) {
                // Fast path: one ctx.fill(Path2D) per terrain type — no JS loop over hexes.
                for (const [terrain, path] of Object.entries(this._terrainPaths)) {
                    ctx.fillStyle = this._terrainFill(terrain);
                    ctx.fill(path);
                }
            } else {
                // Fallback while cache is building: viewport-culled dynamic buckets.
                for (const fill in terrainBuckets) {
                    const pts = terrainBuckets[fill];
                    ctx.beginPath();
                    for (let i = 0; i < pts.length; i += 2) this._hexSubpath(ctx, pts[i], pts[i + 1], inner);
                    ctx.fillStyle = fill;
                    ctx.fill();
                }
            }
        }

        // ── Region fills ──────────────────────────────────────────────────────
        for (const fill in regionBuckets) {
            const pts = regionBuckets[fill];
            ctx.beginPath();
            for (let i = 0; i < pts.length; i += 2) this._hexSubpath(ctx, pts[i], pts[i + 1], inner);
            ctx.fillStyle = fill;
            ctx.fill();
        }

        // ── Nation fills: one beginPath+fill per nation ───────────────────────
        const _t3 = performance.now();
        for (const fill in nationBuckets) {
            const pts = nationBuckets[fill];
            ctx.beginPath();
            for (let i = 0; i < pts.length; i += 2) this._hexSubpath(ctx, pts[i], pts[i + 1], inner);
            ctx.fillStyle = fill;
            ctx.fill();
        }

        // ── Portal ovals ─────────────────────────────────────────────────────
        if (this.layers.buildings) {
            const lw  = Math.max(2, 3.5 / this.zoom);
            const rx  = inner * 0.48;
            const ry  = inner * 0.24;
            const rot = Math.PI / 8;   // ~22.5° tilt
            for (const color in portalBuckets) {
                const pts = portalBuckets[color];
                ctx.strokeStyle = color;
                ctx.lineWidth   = lw;
                for (let i = 0; i < pts.length; i += 2) {
                    ctx.beginPath();
                    ctx.ellipse(pts[i], pts[i+1], rx, ry, rot, 0, Math.PI * 2);
                    ctx.stroke();
                }
            }
        }

        // ── Grid outlines ─────────────────────────────────────────────────────
        // When many hexes are visible, use the pre-built Path2D: ctx.stroke(path) costs
        // no JS loop regardless of hex count. When few hexes are visible, viewport-culled
        // live path is cheaper (avoids GPU processing thousands of off-screen hexes).
        const _t4 = performance.now();
        if (drawLines) {
            // Fade lines out as zoom decreases — full opacity above zoom 0.4, approaches 0 near minZoom.
            const lineFade  = Math.min(1, this.zoom / 0.4);
            const baseAlpha = 0.2;
            ctx.strokeStyle = `rgba(0,0,0,${(baseAlpha * lineFade).toFixed(3)})`;
            ctx.lineWidth   = 1 / this.zoom;
            if (allX.length > 500 && this._outlinePath) {
                ctx.stroke(this._outlinePath);
            } else {
                ctx.beginPath();
                for (let i = 0; i < allX.length; i++) this._hexSubpath(ctx, allX[i], allY[i], inner);
                ctx.stroke();
            }
        }

        // ── Out-of-range overlay (dark tint over owned tiles beyond admin range) ──
        if (hasPolitical && this._outOfRangeTiles.size) {
            ctx.beginPath();
            for (let i = 0; i < allX.length; i++) {
                if (this._outOfRangeTiles.has(allKey[i])) {
                    this._hexSubpath(ctx, allX[i], allY[i], inner);
                }
            }
            ctx.fillStyle = 'rgba(0,0,0,0.45)';
            ctx.fill();
        }

        // ── Routes (dashed lines connecting route tiles and adjacent cities) ────
        // Line width scales with zoom: 3.5 screen-px at zoom ≥ 0.875, thinner when zoomed out.
        if (hasPolitical) {
            const routeLW    = Math.max(0.8, Math.min(4, 3.5 / this.zoom));
            const drawnEdges = new Set();
            ctx.save();
            ctx.lineCap  = 'round';
            ctx.lineWidth = routeLW;
            for (let i = 0; i < allTile.length; i++) {
                const tile = allTile[i];
                if (!tile?.route?.owner) continue;
                const q     = tile.q, r = tile.r;
                const sx    = allX[i], sy = allY[i];
                const tier  = Math.min(3, Math.max(1, tile.route.tier || 1));
                const color = this._nationRgba(tile.route.owner, 1.0);
                const dash  = ROUTE_DASHES[tier];
                for (const [dq, dr] of _AXIAL_DIRS) {
                    const nq   = q + dq, nr = r + dr;
                    const nkey = `${nq},${nr}`;
                    const ekey = allKey[i] < nkey ? `${allKey[i]}|${nkey}` : `${nkey}|${allKey[i]}`;
                    if (drawnEdges.has(ekey)) continue;
                    const nb = this.tiles.get(nkey);
                    if (!nb?.route?.owner && !nb?.city && !nb?.capital) continue;
                    drawnEdges.add(ekey);
                    const { x: nx, y: ny } = this._axialToPixel(nq, nr);

                    const nbTier  = nb?.route?.tier ? Math.min(3, Math.max(1, nb.route.tier)) : 0;
                    const nbOwner = nb?.route?.owner || null;
                    const split   = nbTier && (nbTier !== tier || nbOwner !== tile.route.owner);
                    if (split) {
                        // Different tier or owner — draw each half in its own style
                        const mx = (sx + nx) / 2, my = (sy + ny) / 2;
                        ctx.setLineDash(dash);
                        ctx.strokeStyle = color;
                        ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(mx, my); ctx.stroke();
                        ctx.setLineDash(ROUTE_DASHES[nbTier]);
                        ctx.strokeStyle = this._nationRgba(nbOwner, 1.0);
                        ctx.beginPath(); ctx.moveTo(mx, my); ctx.lineTo(nx, ny); ctx.stroke();
                    } else {
                        // Same tier + owner, or endpoint is city/capital — full line in this tile's style
                        ctx.setLineDash(dash);
                        ctx.strokeStyle = color;
                        ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(nx, ny); ctx.stroke();
                    }
                }
            }
            ctx.setLineDash([]);
            ctx.restore();
        }

        // ── Forbidden tiles overlay (territory restriction, paint mode only) ──
        if (this.forbiddenTiles.size && this.paintNationMode) {
            ctx.beginPath();
            for (const [col, row] of this._visibleCells()) {
                const { q, r } = this._offsetToAxial(col, row);
                if (this.forbiddenTiles.has(`${q},${r}`)) {
                    const { x, y } = this._axialToPixel(q, r);
                    this._hexSubpath(ctx, x, y, inner);
                }
            }
            ctx.fillStyle = 'rgba(200,0,0,0.35)';
            ctx.fill();
        }

        // ── Selection highlight (single hex, drawn on top) ────────────────────
        if (this.selectedTile) {
            const { x, y } = this._axialToPixel(this.selectedTile.q, this.selectedTile.r);
            this._drawHex(ctx, x, y, inner, 'rgba(255,255,100,0.25)', '#ffff44', 2.5);
        }

        // ── Icons (only rendered when zoomed in enough to see them) ───────────
        const _t5 = performance.now();
        if (showIcons) {
            const doBldgs = this.layers.buildings;
            const doNodes = this.layers.nodes;
            for (let i = 0; i < allX.length; i++) {
                const tile = allTile[i];
                if (!tile) continue;
                if (doNodes) this._drawNode(ctx, allX[i], allY[i], tile);
                if (doBldgs) this._drawBuildings(ctx, allX[i], allY[i], tile);
            }
        }

        ctx.restore();

        // ── Nation name labels (screen space, drawn over everything) ──────────
        this._drawNationLabels();

        // ── Perf bookkeeping (cheap — always runs) ────────────────────────────
        const _t6 = performance.now();
        const p = this._perf;
        p.frameMs       = _t6 - _t0;
        p.cellCount     = allX.length;
        p.drawLines     = drawLines;
        p.showIcons     = showIcons;
        p.terrainBuckets = Object.keys(terrainBuckets).length;
        p.nationBuckets  = Object.keys(nationBuckets).length;
        p.sections = { setup: _t1 - _t0, cells: _t2 - _t1, terrain: _t3 - _t2,
                       nation: _t4 - _t3, outlines: _t5 - _t4, icons: _t6 - _t5 };
        if (p._lastT) {
            p._fpsArr.push(1000 / (_t0 - p._lastT));
            if (p._fpsArr.length > 20) p._fpsArr.shift();
            p.fps = p._fpsArr.reduce((a, b) => a + b, 0) / p._fpsArr.length;
        }
        p._lastT = _t0;

        if (this.showPerfOverlay) this._drawPerfOverlay();
    }

    _drawPerfOverlay() {
        const p   = this._perf;
        const ctx = this.ctx;
        const s   = p.sections;
        const fmt = v => v.toFixed(1).padStart(5);
        const lines = [
            `FPS ${p.fps.toFixed(1).padStart(5)}   frame ${p.frameMs.toFixed(1)}ms`,
            `cells    ${fmt(s.cells)}ms  (${p.cellCount} hexes)`,
            `terrain  ${fmt(s.terrain)}ms  (${p.terrainBuckets} buckets)`,
            `nation   ${fmt(s.nation)}ms  (${p.nationBuckets} buckets)`,
            `outlines ${fmt(s.outlines)}ms  (${p.drawLines ? 'drawn' : 'skipped'})`,
            `icons    ${fmt(s.icons)}ms  (${p.showIcons ? 'drawn' : 'skipped'})`,
            `setup    ${fmt(s.setup)}ms`,
        ];
        ctx.save();
        ctx.font = 'bold 12px monospace';
        ctx.textBaseline = 'top';
        const lh  = 16, pad = 8;
        const maxW = Math.max(...lines.map(l => ctx.measureText(l).width));
        ctx.fillStyle = 'rgba(0,0,0,0.72)';
        ctx.fillRect(pad - 4, pad - 4, maxW + 16, lines.length * lh + 10);
        ctx.fillStyle = '#00ff88';
        lines.forEach((l, i) => ctx.fillText(l, pad, pad + i * lh));
        ctx.restore();
    }

    _drawNationLabels() {
        if (!this._nationLabels || !this.layers.political) return;
        const ctx = this.ctx;
        ctx.save();
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';

        for (const lb of this._nationLabels) {
            const { name } = lb;
            // Convert world centroid → screen
            const sx = lb.wx * this.zoom + this.panX;
            const sy = lb.wy * this.zoom + this.panY;

            // Skip if centroid is off-screen (with generous margin)
            if (sx < -200 || sx > this.canvas.width + 200 ||
                sy < -200 || sy > this.canvas.height + 200) continue;

            // Compute font size: fill ~60% of bbox width, clamped
            const bboxWScreen = lb.wBboxW * this.zoom;
            let fontSize = Math.min(bboxWScreen * 0.18, 52);
            fontSize = Math.max(fontSize, 7);

            // Shrink until name fits within bbox width
            ctx.font = `bold italic ${fontSize}px serif`;
            while (fontSize > 7 && ctx.measureText(name).width > bboxWScreen * 0.85) {
                fontSize -= 1;
                ctx.font = `bold italic ${fontSize}px serif`;
            }

            // Skip labels that would be tiny and unreadable
            if (fontSize < 8) continue;

            const overlord = this._nationOverlords[name] || '';
            const ovFontSize = Math.max(6, fontSize * 0.55);

            // Draw name shadow + text
            ctx.shadowColor = 'rgba(0,0,0,0.75)';
            ctx.shadowBlur  = Math.max(2, fontSize * 0.25);
            ctx.fillStyle   = '#ffffff';
            ctx.font = `bold italic ${fontSize}px serif`;
            ctx.fillText(name, sx, sy);

            // Draw overlord below, if present
            if (overlord && ovFontSize >= 6) {
                ctx.font = `italic ${ovFontSize}px serif`;
                ctx.shadowBlur = Math.max(2, ovFontSize * 0.25);
                ctx.fillText(`(${overlord})`, sx, sy + fontSize * 0.72);
            }
        }

        ctx.shadowBlur = 0;
        ctx.restore();
    }

    /** One-shot render + console.table breakdown — call from browser console. */
    profileRender() {
        this.render();
        const p = this._perf;
        console.log(`%cHexMap profile — ${p.cellCount} cells, ${p.frameMs.toFixed(1)}ms total`, 'font-weight:bold');
        console.table(Object.fromEntries(
            Object.entries(p.sections).map(([k, v]) => [k, { ms: +v.toFixed(2), pct: +(v / p.frameMs * 100).toFixed(1) }])
        ));
    }

    _drawBuildings(ctx, cx, cy, tile) {
        const s       = this.hexSize;
        const szCity  = Math.max(10, s * 1);   // city — drawn on top
        const szWond  = Math.max(10, s * 1.2);   // wonder — larger, drawn beneath city
        const szDist  = Math.max(10, s * 0.55);  // district
        ctx.shadowColor = 'rgba(0,0,0,0.85)';
        ctx.shadowBlur  = 3;

        const _drawImg = (img, fallbackChar, sz) => {
            if (img) {
                ctx.drawImage(img, cx - sz / 2, cy - sz / 2, sz, sz);
            } else {
                const fSz = Math.max(7, s * 0.32);
                ctx.font         = `${fSz}px sans-serif`;
                ctx.textAlign    = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle    = '#ffffff';
                ctx.fillText(fallbackChar, cx, cy);
            }
        };

        // Draw order: capital (bottom) → wonder → city (top) → district
        if (tile.capital) this._drawCapital(ctx, cx, cy, tile);
        if (tile.wonder)   _drawImg(this._wonderDefaultImage, '✦', szWond);
        if (tile.city || tile.district) ctx.globalAlpha = 0.6;
        if (tile.city)     _drawImg(tile.city.type ? this._cityTypeImages[tile.city.type] : undefined, '🏛', szCity);
        if (tile.district) _drawImg(tile.district.def_key ? this._districtImages[tile.district.def_key] : undefined, '⬡', szDist);
        ctx.globalAlpha = 1;

        ctx.shadowBlur = 0;
    }

    _drawNode(ctx, cx, cy, tile) {
        if (!tile.node) return;
        const s    = this.hexSize;
        const rKey = tile.node.resource_type || tile.node.value || tile.node.type;
        const col  = RESOURCE_COLORS[rKey] || '#aaaaaa';
        const sz   = Math.max(5, s * 0.5);
        ctx.fillStyle   = col;
        ctx.strokeStyle = 'rgba(0,0,0,0.7)';
        ctx.lineWidth   = 1 / this.zoom;

        if (LUXURY_KEYS.has(rKey)) {
            ctx.fillRect(cx - sz/2, cy - sz/2, sz, sz);
            ctx.strokeRect(cx - sz/2, cy - sz/2, sz, sz);
        } else {
            ctx.beginPath();
            ctx.arc(cx, cy, sz/2, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        }
    }

    _drawCapital(ctx, cx, cy, tile) {
        if (!tile.capital) return;
        const s  = this.hexSize;
        const sz = Math.max(10, s * 1.5);
        ctx.font         = `${sz}px sans-serif`;
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.shadowColor  = 'rgba(0,0,0,0.85)';
        ctx.shadowBlur   = 4;
        ctx.fillStyle    = '#ffd700';
        ctx.fillText('★', cx, cy);
        ctx.shadowBlur   = 0;
    }

    // -----------------------------------------------------------------------
    // Event handling
    // -----------------------------------------------------------------------

    _bindEvents() {
        const c = this.canvas;
        c.addEventListener('contextmenu', e => e.preventDefault());
        c.addEventListener('mousedown', e => {
            if (e.button === 2) { this._startPan(e.clientX, e.clientY); return; }
            if (e.button === 1) {
                e.preventDefault();
                this._midPainting = true;
                this._lastPainted = null;
                this._currentUndoBatch = { type: 'unown', tiles: new Map() };
                this._paintUnownedAt(e.clientX, e.clientY);
                return;
            }
            this._onDown(e.clientX, e.clientY);
        });
        c.addEventListener('mousemove', e => {
            if (this._midPainting) { this._paintUnownedAt(e.clientX, e.clientY); return; }
            this._onMove(e.clientX, e.clientY);
        });
        c.addEventListener('mouseup', e => {
            if (e.button === 1) {
                this._midPainting = false;
                this._lastPainted = null;
                this._finalizeUndoBatch();
                return;
            }
            this._onUp(e.clientX, e.clientY);
        });
        c.addEventListener('mouseleave', () => {
            this.isDragging   = false;
            this._painting    = false;
            this._midPainting = false;
            this._finalizeUndoBatch();
            this._updateCursor();
        });
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                this.setPaintMode(null);
                this.setPaintNationMode(null);
                this.setPaintRegionMode(null);
                this.canvas.dispatchEvent(new CustomEvent('hexmap:pantool', { bubbles: true }));
            }
            if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
                e.preventDefault();
                this.undo();
            }
        });
        c.addEventListener('wheel',      e => this._onWheel(e), { passive: false });
        c.addEventListener('touchstart', e => this._onTouchStart(e), { passive: false });
        c.addEventListener('touchmove',  e => this._onTouchMove(e),  { passive: false });
        c.addEventListener('touchend',   e => this._onTouchEnd(e));
        c.style.cursor = 'grab';
    }

    // -----------------------------------------------------------------------
    // Layer and paint-mode controls (called from map.html toolbar)
    // -----------------------------------------------------------------------

    setLayer(name, enabled) {
        if (name in this.layers) {
            this.layers[name] = enabled;
            this.render();
        }
    }

    setPaintMode(terrainKey) {
        this.paintMode       = terrainKey || null;
        this.paintNationMode = null;
        this.paintRegionMode = null;
        this.paintRouteMode  = null;
        this.forbiddenTiles  = new Set();
        this._updateCursor();
        this.render();
    }

    setPaintRegionMode(regionName) {
        this.paintRegionMode = regionName || null;
        this.paintMode       = null;
        this.paintNationMode = null;
        this.paintRouteMode  = null;
        this.forbiddenTiles  = new Set();
        this._updateCursor();
        this.render();
    }

    setPaintRouteMode(owner, tier) {
        if (tier === 'erase') {
            this.paintRouteMode = { erase: true };
        } else if (owner && tier) {
            this.paintRouteMode = { owner, tier: parseInt(tier) };
        } else {
            this.paintRouteMode = null;
        }
        this.paintMode       = null;
        this.paintNationMode = null;
        this.paintRegionMode = null;
        this.forbiddenTiles  = new Set();
        this._updateCursor();
        this.render();
    }

    async setPaintNationMode(nationName) {
        this.paintNationMode = nationName || null;
        this.paintMode       = null;
        this.paintRegionMode = null;
        this.paintRouteMode  = null;
        this.forbiddenTiles  = new Set();
        this._updateCursor();
        if (nationName && nationName !== '__unowned__') {
            try {
                const resp = await fetch(`/api/hex-map/nation/${encodeURIComponent(nationName)}/restriction-tiles`);
                if (resp.ok) {
                    const data = await resp.json();
                    this.forbiddenTiles = new Set((data.forbidden || []).map(([q, r]) => `${q},${r}`));
                }
            } catch (_) {}
        }
        this.render();
    }

    _showPaintError(msg) {
        const el = document.getElementById('hex-paint-error');
        if (!el) return;
        el.textContent = msg;
        el.style.display = 'block';
        clearTimeout(this._paintErrorTimer);
        this._paintErrorTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);
    }

    _updateCursor() {
        this.canvas.style.cursor = (this.paintMode || this.paintNationMode || this.paintRegionMode || this.paintRouteMode) ? 'crosshair' : 'grab';
    }

    _startPan(cx, cy) {
        this.isDragging = true;
        this.hasMoved   = false;
        this.dragStart  = { x: cx, y: cy };
        this.panStart   = { x: this.panX, y: this.panY };
        this.canvas.style.cursor = 'grabbing';
    }

    _onDown(cx, cy) {
        if (this.paintMode || this.paintNationMode || this.paintRegionMode || this.paintRouteMode) {
            this._painting    = true;
            this._lastPainted = null;
            const type = this.paintNationMode ? 'nation' : this.paintRegionMode ? 'region' : this.paintRouteMode ? 'route' : 'terrain';
            this._currentUndoBatch = { type, tiles: new Map() };
            this._paintAt(cx, cy);
        } else {
            this._startPan(cx, cy);
        }
    }

    _onMove(cx, cy) {
        if (this._painting && (this.paintMode || this.paintNationMode || this.paintRegionMode || this.paintRouteMode)) {
            this._paintAt(cx, cy);
            return;
        }
        if (!this.isDragging) return;
        const dx = cx - this.dragStart.x, dy = cy - this.dragStart.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) this.hasMoved = true;
        this.panX = this.panStart.x + dx;
        this.panY = this.panStart.y + dy;
        this.render();
    }

    _onUp(cx, cy) {
        if (this._painting) {
            this._painting    = false;
            this._lastPainted = null;
            this._finalizeUndoBatch();
            return;
        }
        this.isDragging = false;
        this._updateCursor();
        if (!this.hasMoved) this._onClickWorld(cx, cy);
    }

    _finalizeUndoBatch() {
        if (this._currentUndoBatch && this._currentUndoBatch.tiles.size > 0) {
            this._undoStack.push(this._currentUndoBatch);
            if (this._undoStack.length > 50) this._undoStack.shift();
        }
        this._currentUndoBatch = null;
    }

    undo() {
        if (!this._undoStack.length) return;
        const batch = this._undoStack.pop();
        for (const [key, originalValue] of batch.tiles) {
            const [q, r] = key.split(',').map(Number);
            const existing = this.tiles.get(key) || { q, r };
            let field, apiPayload;
            if (batch.type === 'terrain') {
                field      = { terrain: originalValue };
                apiPayload = { terrain: originalValue };
            } else if (batch.type === 'nation' || batch.type === 'unown') {
                field      = { owner: originalValue };
                apiPayload = { owner: originalValue };
            } else if (batch.type === 'region') {
                field      = { region: originalValue };
                apiPayload = { region: originalValue };
            } else if (batch.type === 'route') {
                field      = { route: originalValue };
                apiPayload = { route: originalValue };
            }
            this.tiles.set(key, { ...existing, ...field });
            fetch(`/api/hex-map/tile/${q}/${r}`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(apiPayload),
            }).catch(() => {});
        }
        if (batch.type === 'terrain') this._scheduleTerrainRebuild();
        if (batch.type === 'nation' || batch.type === 'unown') this._computeNationLabels();
        this.render();
    }

    async _paintAt(screenX, screenY) {
        if (!this.isAdmin) return;
        const rect  = this.canvas.getBoundingClientRect();
        const world = this._screenToWorld(screenX - rect.left, screenY - rect.top);
        const { q, r } = this._pixelToAxial(world.x, world.y);
        const col = q; const row = r + Math.floor(q / 2);
        if (col < 0 || col >= this.cols || row < 0 || row >= this.rows) return;
        const key = `${q},${r}`;
        if (key === this._lastPainted) return;
        this._lastPainted = key;

        const existing = this.tiles.get(key) || { q, r };

        if (this.paintMode) {
            const terrain = this.paintMode;
            if (this._currentUndoBatch && !this._currentUndoBatch.tiles.has(key))
                this._currentUndoBatch.tiles.set(key, existing.terrain || 'disconnected');
            this.tiles.set(key, { ...existing, terrain });
            this._scheduleTerrainRebuild();
            this.render();
            await fetch(`/api/hex-map/tile/${q}/${r}`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ terrain }),
            }).catch(() => {});
        } else if (this.paintNationMode) {
            const owner = this.paintNationMode === '__unowned__' ? null : this.paintNationMode;
            if (owner && this.forbiddenTiles.has(key)) {
                this._showPaintError('This tile cannot be claimed: it violates a territory restriction.');
                return;
            }
            if (this._currentUndoBatch && !this._currentUndoBatch.tiles.has(key))
                this._currentUndoBatch.tiles.set(key, existing.owner || null);
            this.tiles.set(key, { ...existing, owner });
            this._computeNationLabels();
            this.render();
            let resp;
            try {
                resp = await fetch(`/api/hex-map/tile/${q}/${r}`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ owner }),
                });
            } catch (_) { resp = null; }
            if (resp && !resp.ok) {
                // Server rejected — roll back the local change and remove from undo batch
                if (this._currentUndoBatch) this._currentUndoBatch.tiles.delete(key);
                this.tiles.set(key, existing);
                this._computeNationLabels();
                this.render();
                const data = await resp.json().catch(() => ({}));
                this._showPaintError(data.error || 'This tile cannot be claimed.');
            }
        } else if (this.paintRegionMode) {
            const region = this.paintRegionMode === '__unregioned__' ? null : this.paintRegionMode;
            if (this._currentUndoBatch && !this._currentUndoBatch.tiles.has(key))
                this._currentUndoBatch.tiles.set(key, existing.region || null);
            this.tiles.set(key, { ...existing, region });
            this.render();
            let resp;
            try {
                resp = await fetch(`/api/hex-map/tile/${q}/${r}`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ region }),
                });
            } catch (_) { resp = null; }
            if (resp && !resp.ok) {
                if (this._currentUndoBatch) this._currentUndoBatch.tiles.delete(key);
                this.tiles.set(key, existing);
                this.render();
                const data = await resp.json().catch(() => ({}));
                this._showPaintError(data.error || 'Could not set region on this tile.');
            }
        } else if (this.paintRouteMode) {
            const route = this.paintRouteMode.erase ? null
                : { owner: this.paintRouteMode.owner, tier: this.paintRouteMode.tier };
            if (route) {
                const terrain = existing.terrain || 'disconnected';
                const blocked12 = new Set(['hazardous_land','hazardous_water','mountain','deep_water']);
                const blocked3  = new Set(['deep_water']);
                const blocked   = route.tier === 3 ? blocked3 : blocked12;
                if (blocked.has(terrain)) {
                    this._showPaintError(`Tier ${route.tier} routes cannot be built on ${TERRAIN_NAMES[terrain] || terrain} terrain.`);
                    return;
                }
            }
            if (this._currentUndoBatch && !this._currentUndoBatch.tiles.has(key))
                this._currentUndoBatch.tiles.set(key, existing.route || null);
            this.tiles.set(key, { ...existing, route });
            this.render();
            let resp;
            try {
                resp = await fetch(`/api/hex-map/tile/${q}/${r}`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ route }),
                });
            } catch (_) { resp = null; }
            if (resp && !resp.ok) {
                if (this._currentUndoBatch) this._currentUndoBatch.tiles.delete(key);
                this.tiles.set(key, existing);
                this.render();
                const data = await resp.json().catch(() => ({}));
                this._showPaintError(data.error || 'Could not set route on this tile.');
            }
        }
    }

    async _paintUnownedAt(screenX, screenY) {
        if (!this.isAdmin) return;
        const rect  = this.canvas.getBoundingClientRect();
        const world = this._screenToWorld(screenX - rect.left, screenY - rect.top);
        const { q, r } = this._pixelToAxial(world.x, world.y);
        const col = q, row = r + Math.floor(q / 2);
        if (col < 0 || col >= this.cols || row < 0 || row >= this.rows) return;
        const key = `${q},${r}`;
        if (key === this._lastPainted) return;
        this._lastPainted = key;
        const existing = this.tiles.get(key) || { q, r };
        if (this._currentUndoBatch && !this._currentUndoBatch.tiles.has(key))
            this._currentUndoBatch.tiles.set(key, existing.owner || null);
        this.tiles.set(key, { ...existing, owner: null });
        this._computeNationLabels();
        this.render();
        await fetch(`/api/hex-map/tile/${q}/${r}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ owner: null }),
        }).catch(() => {});
    }

    _onWheel(e) {
        e.preventDefault();
        const rect    = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const factor  = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        this._zoomAround(mx, my, factor);
    }

    _onTouchStart(e) {
        e.preventDefault();
        if (e.touches.length === 1) {
            const t = e.touches[0];
            this._onDown(t.clientX, t.clientY);
        } else if (e.touches.length === 2) {
            this.isDragging = false;
            this._pinchDist = this._dist2(e.touches);
        }
    }

    _onTouchMove(e) {
        e.preventDefault();
        if (e.touches.length === 1 && this.isDragging) {
            const t = e.touches[0];
            this._onMove(t.clientX, t.clientY);
        } else if (e.touches.length === 2 && this._pinchDist !== null) {
            const dist   = this._dist2(e.touches);
            const rect   = this.canvas.getBoundingClientRect();
            const mx     = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
            const my     = (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
            this._zoomAround(mx, my, dist / this._pinchDist);
            this._pinchDist = dist;
        }
    }

    _onTouchEnd(e) {
        if (e.touches.length === 0) {
            this.isDragging = false;
            this._pinchDist = null;
        }
    }

    _dist2(touches) {
        return Math.hypot(
            touches[0].clientX - touches[1].clientX,
            touches[0].clientY - touches[1].clientY,
        );
    }

    // -----------------------------------------------------------------------
    // Click / tile selection
    // -----------------------------------------------------------------------

    _onClickWorld(screenX, screenY) {
        const rect  = this.canvas.getBoundingClientRect();
        const world = this._screenToWorld(screenX - rect.left, screenY - rect.top);
        const { q, r } = this._pixelToAxial(world.x, world.y);

        const col = q; const row = r + Math.floor(q / 2);
        if (col >= 0 && col < this.cols && row >= 0 && row < this.rows) {
            this.selectedTile = { q, r };
            this._showDetails(q, r);
        } else {
            this.selectedTile = null;
            this._clearDetails();
        }
        this.render();
    }

    // -----------------------------------------------------------------------
    // Detail panel
    // -----------------------------------------------------------------------

    _showDetails(q, r) {
        const tile = this.tiles.get(`${q},${r}`);
        const offsetRow = r + Math.floor(q / 2);

        let html = `<h4 class="hex-detail-title">Tile — col ${q}, row ${offsetRow}</h4>`;
        html += `<div class="hex-detail-coords">Axial q=${q}, r=${r}</div>`;

        if (tile) {
            const t = tile.terrain || 'disconnected';
            html += row('Terrain', TERRAIN_NAMES[t] || t);

            if (tile.owner) {
                html += row('Owner', `<a href="/nations/item/${encodeURIComponent(tile.owner)}" target="_blank">${_esc(tile.owner)}</a>`);
            } else {
                html += row('Owner', '<em>None</em>');
            }

            if (tile.city) {
                const cityLabel = `${_esc(tile.city.name || '')} <em>(${_esc(tile.city.type || '')})</em>`;
                const cityLink  = tile.owner
                    ? `<a href="/nations/item/${encodeURIComponent(tile.owner)}" target="_blank">${cityLabel}</a>`
                    : cityLabel;
                html += row('City', cityLink);
            }
            if (tile.district) {
                const distLabel = _esc(tile.district.type || '') + (tile.district.imperial ? ' <em>(imperial)</em>' : '');
                const distLink  = tile.owner
                    ? `<a href="/nations/item/${encodeURIComponent(tile.owner)}" target="_blank">${distLabel}</a>`
                    : distLabel;
                html += row('District', distLink);
            }
            if (tile.wonder) {
                const wLabel = _esc(tile.wonder.name || '');
                const wLink  = tile.wonder.name
                    ? `<a href="/wonders/item/${encodeURIComponent(tile.wonder.name)}" target="_blank">${wLabel}</a>`
                    : wLabel;
                html += row('Wonder', wLink);
            }
            if (tile.region) {
                html += row('Region', _esc(tile.region));
            }
            if (tile.capital) {
                html += row('Capital', '★ Yes');
            }
            if (tile.node) {
                const nd   = tile.node;
                const rKey = nd.resource_type || nd.value || nd.type;
                html += row('Node', _esc(RESOURCE_NAME[rKey] || rKey || nd.type || ''));
            }
            if (this.isAdmin) html += this._buildEditForm(q, r, tile);
        } else {
            html += `<div class="hex-detail-empty">Empty tile (no data)</div>`;
            if (this.isAdmin) html += this._buildEditForm(q, r, {});
        }

        this.detailPanel.innerHTML = html;
        if (this.isAdmin) this._bindEditForm(q, r);
    }

    _clearDetails() {
        this.detailPanel.innerHTML = '<p class="hex-detail-placeholder">Click a tile to view details</p>';
    }

    // -----------------------------------------------------------------------
    // Admin edit form
    // -----------------------------------------------------------------------

    _buildEditForm(q, r, tile) {
        const tv   = tile.terrain || 'disconnected';
        const own  = tile.owner   || '';
        const rKey = tile.node ? (tile.node.resource_type || tile.node.value || '') : '';

        // Current placement IDs (may be absent on old tiles that predate this system)
        const curCityId   = tile.city     ? (tile.city.id     || '') : '';
        const curDistId   = tile.district ? (tile.district.id || '') : '';
        const curWonderId = tile.wonder   ? (tile.wonder.id   || '') : '';

        const terr = TERRAIN_OPTIONS.map(k =>
            `<option value="${k}"${k===tv?' selected':''}>${TERRAIN_NAMES[k]||k}</option>`).join('');

        const resOpts = ALL_RESOURCES.map(res => {
            const cat = LUXURY_KEYS.has(res.key) ? ' (luxury)' : '';
            return `<option value="${res.key}"${res.key===rKey?' selected':''}>${res.name}${cat}</option>`;
        }).join('');

        const isCapital    = tile.capital ? 'checked' : '';
        const curPortal    = tile.portal?.color || '';
        const portalOpts   = PORTAL_COLOR_NAMES.map(c =>
            `<option value="${c}"${c===curPortal?' selected':''}>${c.charAt(0).toUpperCase()+c.slice(1)}</option>`
        ).join('');
        const curRouteOwner = tile.route?.owner || '';
        const curRouteTier  = tile.route?.tier  || '';
        const routeTierOpts = [1,2,3].map(t =>
            `<option value="${t}"${t==curRouteTier?' selected':''}>${t}</option>`
        ).join('');

        return `
<details class="hex-edit-details" id="hex-edit-${q}-${r}">
  <summary>Edit Tile</summary>
  <div class="hex-edit-body">
    <label>Terrain<select name="terrain">${terr}</select></label>
    <label>Owner
      <input name="owner" type="text" value="${_esc(own)}" placeholder="Nation name"
             list="nation-datalist-${q}-${r}" autocomplete="off">
      <datalist id="nation-datalist-${q}-${r}"></datalist>
    </label>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
      <input type="checkbox" name="capital" ${isCapital}> Capital
    </label>
    <fieldset><legend>Node</legend>
      <label>Resource<select name="node_resource"><option value="">None</option>${resOpts}</select></label>
    </fieldset>
    <fieldset><legend>Portal</legend>
      <label>Color<select name="portal_color"><option value="">None</option>${portalOpts}</select></label>
    </fieldset>
    <fieldset><legend>Route</legend>
      <label>Owner
        <input name="route_owner" type="text" value="${_esc(curRouteOwner)}" placeholder="Nation name"
               list="route-nation-datalist-${q}-${r}" autocomplete="off">
        <datalist id="route-nation-datalist-${q}-${r}"></datalist>
      </label>
      <label>Tier<select name="route_tier"><option value="">None</option>${routeTierOpts}</select></label>
    </fieldset>
    <fieldset><legend>City</legend>
      <select name="city_id" data-current="${_esc(curCityId)}">
        <option value="">Loading…</option>
      </select>
    </fieldset>
    <fieldset><legend>District</legend>
      <select name="district_id" data-current="${_esc(curDistId)}">
        <option value="">Loading…</option>
      </select>
    </fieldset>
    <fieldset><legend>Wonder</legend>
      <select name="wonder_id" data-current="${_esc(curWonderId)}">
        <option value="">Loading…</option>
      </select>
    </fieldset>
    <button type="button" class="hex-save-btn btn" data-q="${q}" data-r="${r}">Save</button>
  </div>
</details>`;
    }

    async _ensureNationList() {
        if (this._nationList) {
            // List cached — re-populate nation maps if they were reset (e.g. loadTiles)
            if (this.nationAdmin.size === 0) {
                for (const n of this._nationList) {
                    this.nationAdmin.set(n.name, n.admin || 1);
                    this.nationNomadic.set(n.name, !!n.nomadic);
                }
            }
            return;
        }
        try {
            const resp = await fetch('/api/hex-map/nation-list');
            this._nationList = (await resp.json()).nations || [];
            this._nationOverlords = {};
            for (const n of this._nationList) {
                if (n.overlord) this._nationOverlords[n.name] = n.overlord;
                this.nationAdmin.set(n.name, n.admin || 1);
                this.nationNomadic.set(n.name, !!n.nomadic);
            }
        } catch (_) { this._nationList = []; }
    }

    _computeAllAdminRanges() {
        this._outOfRangeTiles = new Set();
        if (!this.nationAdmin || this.nationAdmin.size === 0) return;

        // Build portal map: color → [{q,r,key,terrain}] for all portal tiles on the map.
        const portalMap = new Map();
        for (const [key, tile] of this.tiles) {
            const color = tile.portal?.color;
            if (!color) continue;
            if (!portalMap.has(color)) portalMap.set(color, []);
            portalMap.get(color).push({ q: tile.q, r: tile.r, key, terrain: tile.terrain || 'disconnected' });
        }

        // Group owned tiles by nation, tracking capital and building flags
        const nationTiles = new Map();
        for (const [key, tile] of this.tiles) {
            if (!tile.owner) continue;
            if (!nationTiles.has(tile.owner)) nationTiles.set(tile.owner, []);
            nationTiles.get(tile.owner).push({
                q: tile.q, r: tile.r, key,
                terrain: tile.terrain || 'disconnected',
                hasBuilding: !!(tile.city || tile.district || tile.wonder),
                isCapital: !!tile.capital,
                routeTier: tile.route?.tier || 0,
            });
        }

        const INF = Infinity;

        for (const [nationName, ownedTiles] of nationTiles) {
            const admin    = this.nationAdmin.get(nationName) || 1;
            const isNomadic = this.nationNomadic.get(nationName) || false;
            const limit    = admin * (isNomadic ? 4 : 2);

            // Determine sources: nomadic → capital only; non-nomadic → buildings, fall back to capital
            let sources;
            if (isNomadic) {
                sources = ownedTiles.filter(t => t.isCapital);
            } else {
                const buildings = ownedTiles.filter(t => t.hasBuilding);
                sources = buildings.length > 0 ? buildings : ownedTiles.filter(t => t.isCapital);
            }

            if (sources.length === 0) {
                for (const t of ownedTiles) this._outOfRangeTiles.add(t.key);
                continue;
            }

            const sourceKeys = new Set(sources.map(s => s.key));

            // Multi-source Dijkstra — entry-cost semantics
            const dist = new Map();
            const heap = new _MinHeap();
            for (const s of sources) {
                dist.set(s.key, 0);
                heap.push([0, s.q, s.r]);
            }

            while (heap.size > 0) {
                const [d, q, r] = heap.pop();
                const key = `${q},${r}`;
                if (d > (dist.get(key) ?? INF)) continue;
                // Normal hex neighbors
                for (const [dq, dr] of _AXIAL_DIRS) {
                    const nq = q + dq, nr = r + dr;
                    const nkey = `${nq},${nr}`;
                    const ntile = this.tiles.get(nkey);
                    const terrain = ntile ? (ntile.terrain || 'disconnected') : 'disconnected';
                    let cost = TERRAIN_MOVE_COST[terrain] ?? _TERRAIN_MOVE_IMPASSABLE;
                    if (cost >= _TERRAIN_MOVE_IMPASSABLE) continue;
                    if (ntile?.route?.tier === 3) cost = Math.max(1, cost - 1);
                    const nd = d + cost;
                    if (nd < (dist.get(nkey) ?? INF)) {
                        dist.set(nkey, nd);
                        heap.push([nd, nq, nr]);
                    }
                }
                // Portal virtual neighbors — treat same-color portal pairs as adjacent
                const curTile = this.tiles.get(key);
                const pColor  = curTile?.portal?.color;
                if (pColor) {
                    for (const partner of (portalMap.get(pColor) || [])) {
                        if (partner.key === key) continue;
                        const cost = TERRAIN_MOVE_COST[partner.terrain] ?? _TERRAIN_MOVE_IMPASSABLE;
                        if (cost >= _TERRAIN_MOVE_IMPASSABLE) continue;
                        const nd = d + cost;
                        if (nd < (dist.get(partner.key) ?? INF)) {
                            dist.set(partner.key, nd);
                            heap.push([nd, partner.q, partner.r]);
                        }
                    }
                }
            }

            // "Entering a tile is sufficient" — check cost-to-entrance ≤ limit
            for (const t of ownedTiles) {
                if (sourceKeys.has(t.key)) continue;
                const d = dist.get(t.key) ?? INF;
                if (d === INF) {
                    this._outOfRangeTiles.add(t.key);
                } else {
                    let tileCost = TERRAIN_MOVE_COST[t.terrain] ?? _TERRAIN_MOVE_IMPASSABLE;
                    if (t.routeTier === 3) tileCost = Math.max(1, tileCost - 1);
                    if ((d - tileCost) > limit) this._outOfRangeTiles.add(t.key);
                }
            }
        }
    }

    async _ensureWonderList() {
        if (this._wonderList) return;
        try {
            this._wonderList = await (await fetch('/api/hex-map/wonder-list')).json();
        } catch (_) { this._wonderList = []; }
    }

    // Flat-top hex axial neighbors: the 6 directions adjacent to any (q, r).
    static _HEX_DIRS = [[1,0],[-1,0],[0,1],[0,-1],[1,-1],[-1,1]];

    // Compute one label per contiguous region of same-owner tiles via BFS flood-fill.
    // Returns an array so one nation with two separate landmasses gets two entries.
    _computeNationLabels() {
        // Group tile objects by owner name
        const byOwner = new Map();  // name -> Map<"q,r", tile>
        for (const [key, tile] of this.tiles) {
            if (!tile.owner) continue;
            if (!byOwner.has(tile.owner)) byOwner.set(tile.owner, new Map());
            byOwner.get(tile.owner).set(key, tile);
        }

        const labels = [];  // [{name, wx, wy, wBboxW, wBboxH, count}]
        const dirs = HexMapViewer._HEX_DIRS;

        for (const [name, tileMap] of byOwner) {
            const unvisited = new Set(tileMap.keys());

            while (unvisited.size > 0) {
                // BFS for one connected component
                const startKey = unvisited.values().next().value;
                unvisited.delete(startKey);
                const queue = [startKey];
                let sumX = 0, sumY = 0, count = 0;
                let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

                while (queue.length > 0) {
                    const key = queue.pop();
                    const tile = tileMap.get(key);
                    const { x, y } = this._axialToPixel(tile.q, tile.r);
                    sumX += x; sumY += y; count++;
                    if (x < minX) minX = x; if (x > maxX) maxX = x;
                    if (y < minY) minY = y; if (y > maxY) maxY = y;

                    for (const [dq, dr] of dirs) {
                        const nk = `${tile.q + dq},${tile.r + dr}`;
                        if (unvisited.has(nk)) { unvisited.delete(nk); queue.push(nk); }
                    }
                }

                labels.push({
                    name,
                    wx:     sumX / count,
                    wy:     sumY / count,
                    wBboxW: maxX - minX + this.hexSize * 2,
                    wBboxH: maxY - minY + this.hexSize * 2,
                    count,
                });
            }
        }

        this._nationLabels = labels;
    }

    async _loadNationBuildings(nationName) {
        if (!nationName) return { cities: [], districts: [] };
        if (this._nationBuildings[nationName]) return this._nationBuildings[nationName];
        try {
            const data = await (await fetch(`/api/hex-map/nation/${encodeURIComponent(nationName)}/buildings`)).json();
            this._nationBuildings[nationName] = data;
            return data;
        } catch (_) { return { cities: [], districts: [] }; }
    }

    _populateBuildingSelects(root, buildings, wonderList, curCityId, curDistId, curWonderId) {
        const citySelect   = root.querySelector('[name="city_id"]');
        const distSelect   = root.querySelector('[name="district_id"]');
        const wonderSelect = root.querySelector('[name="wonder_id"]');

        if (citySelect) {
            citySelect.innerHTML = '<option value="">None</option>' +
                buildings.cities.map(c => {
                    const label = `${_esc(c.name)} (${_esc(c.type)})`;
                    return `<option value="${_esc(c.id)}"${c.id===curCityId?' selected':''}>${label}</option>`;
                }).join('');
            // Keep current value even if it's from a different nation (e.g. captured territory)
            if (curCityId && !buildings.cities.find(c => c.id === curCityId)) {
                citySelect.insertAdjacentHTML('beforeend',
                    `<option value="${_esc(curCityId)}" selected>[current — not in owner's list]</option>`);
            }
        }

        if (distSelect) {
            const namedDistricts = buildings.districts.filter(d => d.display_name || d.type || d.def_key);
            distSelect.innerHTML = '<option value="">None</option>' +
                namedDistricts.map(d => {
                    const name = d.display_name || d.type || d.def_key;
                    const suffix = d.imperial ? ' (imperial)' : '';
                    return `<option value="${_esc(d.id)}"${d.id===curDistId?' selected':''}>${_esc(name)}${suffix}</option>`;
                }).join('');
            if (curDistId && !namedDistricts.find(d => d.id === curDistId)) {
                distSelect.insertAdjacentHTML('beforeend',
                    `<option value="${_esc(curDistId)}" selected>[current — not in owner's list]</option>`);
            }
        }

        if (wonderSelect) {
            const wonders = Array.isArray(wonderList) ? wonderList : [];
            wonderSelect.innerHTML = '<option value="">None</option>' +
                wonders.map(w => {
                    const label = w.owner_nation ? `${_esc(w.name)} (${_esc(w.owner_nation)})` : _esc(w.name);
                    return `<option value="${_esc(w.id)}"${w.id===curWonderId?' selected':''}>${label}</option>`;
                }).join('');
        }
    }

    _bindEditForm(q, r) {
        const root = document.getElementById(`hex-edit-${q}-${r}`);
        if (!root) return;

        const ownerInput   = root.querySelector('[name="owner"]');
        const citySelect   = root.querySelector('[name="city_id"]');
        const distSelect   = root.querySelector('[name="district_id"]');
        const curCityId    = citySelect?.dataset.current  || '';
        const curDistId    = distSelect?.dataset.current  || '';
        const curWonderId  = root.querySelector('[name="wonder_id"]')?.dataset.current || '';

        // Populate nation datalists (owner + route owner)
        this._ensureNationList().then(() => {
            if (!this._nationList) return;
            const opts = this._nationList.map(n => `<option value="${_esc(n.name)}">`).join('');
            const dl  = root.querySelector(`#nation-datalist-${q}-${r}`);
            const rdl = root.querySelector(`#route-nation-datalist-${q}-${r}`);
            if (dl)  dl.innerHTML  = opts;
            if (rdl) rdl.innerHTML = opts;
        });

        // Populate building selects for current owner
        const refreshBuildings = async (ownerName) => {
            const [buildings, wonderList] = await Promise.all([
                this._loadNationBuildings(ownerName),
                this._ensureWonderList().then(() => this._wonderList),
            ]);
            this._populateBuildingSelects(root, buildings, wonderList, curCityId, curDistId, curWonderId);
        };

        refreshBuildings(ownerInput?.value?.trim() || '');

        // Reload city/district selects when owner changes
        if (ownerInput) {
            ownerInput.addEventListener('change', () => {
                refreshBuildings(ownerInput.value.trim());
            });
        }

        const btn = root.querySelector('.hex-save-btn');
        if (!btn) return;
        btn.addEventListener('click', async () => { try {
            const val = name => root.querySelector(`[name="${name}"]`)?.value?.trim() || null;

            const rKey = val('node_resource');
            const node = rKey ? { resource_type: rKey, type: 'resource' } : null;

            // Resolve city from select → denormalized {id, name, type}
            const cityId    = val('city_id');
            const nationBld = this._nationBuildings[val('owner') || ''] || { cities: [], districts: [] };
            const cityObj   = nationBld.cities.find(c => c.id === cityId);
            const city      = cityId ? (cityObj || { id: cityId }) : null;

            // Resolve district
            const distId  = val('district_id');
            const distObj = nationBld.districts.find(d => d.id === distId);
            const district = distId ? (distObj || { id: distId }) : null;

            // Resolve wonder
            const wonderId  = val('wonder_id');
            const wonderObj = (this._wonderList || []).find(w => w.id === wonderId);
            const wonder    = wonderId ? (wonderObj || { id: wonderId }) : null;

            const capital      = root.querySelector('[name="capital"]')?.checked || false;
            const portalColor  = val('portal_color');
            const portal       = portalColor ? { color: portalColor } : null;
            const routeOwner   = val('route_owner');
            const routeTierStr = val('route_tier');
            const route        = (routeOwner && routeTierStr) ? { owner: routeOwner, tier: parseInt(routeTierStr) } : null;

            // Validate route terrain restrictions before hitting the server
            if (route) {
                const terrain = val('terrain') || 'disconnected';
                const blocked12 = new Set(['hazardous_land','hazardous_water','mountain','deep_water']);
                const blocked3  = new Set(['deep_water']);
                const blocked   = route.tier === 3 ? blocked3 : blocked12;
                if (blocked.has(terrain)) {
                    this._showPaintError(`Tier ${route.tier} routes cannot be built on ${TERRAIN_NAMES[terrain] || terrain} terrain.`);
                    return;
                }
            }

            const update = {
                terrain:  val('terrain') || 'disconnected',
                owner:    val('owner'),
                node,
                city,
                district,
                wonder,
                capital,
                portal,
                route,
            };

            const resp = await fetch(`/api/hex-map/tile/${q}/${r}`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(update),
            });
            if (resp.ok) {
                const key = `${q},${r}`;
                const old = this.tiles.get(key);
                // Refresh nation color cache if owner changed
                if (update.owner && update.owner !== (old && old.owner)) {
                    try {
                        const nc = await (await fetch('/api/hex-map/nation-list')).json();
                        if (nc.nations) {
                            this._nationList = nc.nations;
                            for (const n of nc.nations) {
                                this.nationColors[n.name] = n.color;
                                if (n.overlord) this._nationOverlords[n.name] = n.overlord;
                                this.nationAdmin.set(n.name, n.admin || 1);
                                this.nationNomadic.set(n.name, !!n.nomadic);
                            }
                        }
                    } catch (_) {}
                }
                this.tiles.set(key, { ...(old || { q, r }), ...update });
                this._computeNationLabels();
                // Recompute admin ranges if a building was added or removed
                if ('city' in update || 'district' in update || 'wonder' in update || 'owner' in update || 'capital' in update || 'portal' in update)
                    this._computeAllAdminRanges();
                this._showDetails(q, r);
                this.render();
            } else {
                const data = await resp.json().catch(() => ({}));
                this._showPaintError(data.error || `Save failed (HTTP ${resp.status}).`);
            }
        } catch (err) {
            this._showPaintError(`Save failed: ${err.message}`);
        } });
    }

    // -----------------------------------------------------------------------
    // Viewport controls
    // -----------------------------------------------------------------------

    zoomIn()  { this._zoomAround(this.canvas.width / 2, this.canvas.height / 2, 1.25); }
    zoomOut() { this._zoomAround(this.canvas.width / 2, this.canvas.height / 2, 1 / 1.25); }
    resetView() { this._centerView(); this.render(); }

    _zoomAround(mx, my, factor) {
        const nz  = Math.max(this.minZoom, Math.min(this.maxZoom, this.zoom * factor));
        this.panX = mx - (mx - this.panX) * (nz / this.zoom);
        this.panY = my - (my - this.panY) * (nz / this.zoom);
        this.zoom = nz;
        this.render();
    }
}


// ---------------------------------------------------------------------------
// Small helpers used by detail panel HTML
// ---------------------------------------------------------------------------

function row(label, value) {
    return `<div class="hex-detail-row"><span class="hex-detail-label">${label}</span><span>${value}</span></div>`;
}

function _esc(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}
