/**
 * HexMap - Interactive hexagonal map renderer
 */
class HexMap {
    constructor(canvasId, backgroundCanvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.backgroundCanvas = document.getElementById(backgroundCanvasId);
        this.backgroundCtx = this.backgroundCanvas.getContext('2d');
        
        // Map state
        this.tiles = [];
        this.nations = [];
        this.viewMode = 'terrain'; // 'terrain', 'political', 'nodes'
        this.selectedTile = null;
        this.onTileSelect = null; // Callback for tile selection
        
        // View parameters
        this.hexSize = 20;
        this.offsetX = 0;
        this.offsetY = 0;
        this.scale = 1.0;
        this.minScale = 0.5;
        this.maxScale = 3.0;
        
        // Colors for different terrain types
        this.terrainColors = {
            'plains': '#90EE90',
            'forest': '#228B22',
            'hills': '#8B7355',
            'mountains': '#696969',
            'desert': '#F4A460',
            'swamp': '#556B2F',
            'coast': '#87CEEB',
            'ocean': '#4682B4',
            'river': '#4169E1'
        };
        
        // Initialize event handlers
        this.initEventHandlers();
        
        // Set canvas size
        this.resizeCanvas();
        window.addEventListener('resize', () => this.resizeCanvas());
    }
    
    initEventHandlers() {
        // Mouse events for interaction
        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
        
        // Touch events for mobile
        this.canvas.addEventListener('touchstart', (e) => this.handleTouchStart(e));
        this.canvas.addEventListener('touchmove', (e) => this.handleTouchMove(e));
        this.canvas.addEventListener('touchend', (e) => this.handleTouchEnd(e));
        
        // Dragging state
        this.isDragging = false;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
    }
    
    resizeCanvas() {
        const container = this.canvas.parentElement;
        const rect = container.getBoundingClientRect();
        
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        this.backgroundCanvas.width = rect.width;
        this.backgroundCanvas.height = rect.height;
        
        // Reposition canvases
        this.backgroundCanvas.style.position = 'absolute';
        this.backgroundCanvas.style.top = '0';
        this.backgroundCanvas.style.left = '0';
        this.backgroundCanvas.style.zIndex = '1';
        this.canvas.style.position = 'absolute';
        this.canvas.style.top = '0';
        this.canvas.style.left = '0';
        this.canvas.style.zIndex = '2';
        
        this.render();
    }
    
    async loadMapData() {
        try {
            // Load tiles
            const tilesResponse = await fetch('/api/map/tiles');
            const tilesData = await tilesResponse.json();
            
            if (tilesData.success) {
                this.tiles = tilesData.tiles;
            } else {
                throw new Error(tilesData.error || 'Failed to load tiles');
            }
            
            // Load nations
            const nationsResponse = await fetch('/api/map/nations');
            const nationsData = await nationsResponse.json();
            
            if (nationsData.success) {
                this.nations = nationsData.nations;
                this.updateNationsLegend();
            } else {
                console.warn('Failed to load nations:', nationsData.error);
            }
            
        } catch (error) {
            console.error('Error loading map data:', error);
            throw error;
        }
    }
    
    updateNationsLegend() {
        const legendContent = document.getElementById('nations-legend-content');
        if (!legendContent) return;
        
        let html = '';
        this.nations.forEach(nation => {
            html += `<div class="legend-item">
                <span class="nation-color" style="background-color: ${nation.color}"></span>
                ${nation.name}
            </div>`;
        });
        
        legendContent.innerHTML = html;
    }
    
    setViewMode(mode) {
        this.viewMode = mode;
        this.render();
    }
    
    // Hexagon math functions
    hexToPixel(q, r) {
        const x = this.hexSize * (3/2 * q);
        const y = this.hexSize * (Math.sqrt(3)/2 * q + Math.sqrt(3) * r);
        return [x, y];
    }
    
    pixelToHex(x, y) {
        const q = (2/3 * x) / this.hexSize;
        const r = (-1/3 * x + Math.sqrt(3)/3 * y) / this.hexSize;
        return this.hexRound(q, r);
    }
    
    hexRound(q, r) {
        const s = -q - r;
        
        let rq = Math.round(q);
        let rr = Math.round(r);
        let rs = Math.round(s);
        
        const qDiff = Math.abs(rq - q);
        const rDiff = Math.abs(rr - r);
        const sDiff = Math.abs(rs - s);
        
        if (qDiff > rDiff && qDiff > sDiff) {
            rq = -rr - rs;
        } else if (rDiff > sDiff) {
            rr = -rq - rs;
        } else {
            rs = -rq - rr;
        }
        
        return { q: rq, r: rr, s: rs };
    }
    
    drawHexagon(ctx, x, y, size, fillColor, strokeColor = '#000', strokeWidth = 1) {
        ctx.beginPath();
        
        for (let i = 0; i < 6; i++) {
            const angle = (Math.PI / 3) * i;
            const hexX = x + size * Math.cos(angle);
            const hexY = y + size * Math.sin(angle);
            
            if (i === 0) {
                ctx.moveTo(hexX, hexY);
            } else {
                ctx.lineTo(hexX, hexY);
            }
        }
        
        ctx.closePath();
        
        if (fillColor) {
            ctx.fillStyle = fillColor;
            ctx.fill();
        }
        
        if (strokeColor) {
            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = strokeWidth;
            ctx.stroke();
        }
    }
    
    render() {
        // Clear canvases
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.backgroundCtx.clearRect(0, 0, this.backgroundCanvas.width, this.backgroundCanvas.height);
        
        // Save context state
        this.ctx.save();
        this.backgroundCtx.save();
        
        // Apply transformations
        this.ctx.translate(this.canvas.width / 2 + this.offsetX, this.canvas.height / 2 + this.offsetY);
        this.ctx.scale(this.scale, this.scale);
        
        this.backgroundCtx.translate(this.backgroundCanvas.width / 2 + this.offsetX, this.backgroundCanvas.height / 2 + this.offsetY);
        this.backgroundCtx.scale(this.scale, this.scale);
        
        // Render tiles
        this.tiles.forEach(tile => {
            this.renderTile(tile);
        });
        
        // Restore context state
        this.ctx.restore();
        this.backgroundCtx.restore();
    }
    
    renderTile(tile) {
        const [x, y] = this.hexToPixel(tile.coordinates.q, tile.coordinates.r);
        
        let fillColor = this.terrainColors[tile.terrain_type] || '#CCCCCC';
        let strokeColor = '#000000';
        let strokeWidth = 1;
        
        // Modify appearance based on view mode
        if (this.viewMode === 'political' && tile.nation) {
            // Add nation color overlay
            fillColor = this.blendColors(fillColor, tile.nation.color, 0.6);
            strokeColor = tile.nation.color;
            strokeWidth = 2;
        }
        
        // Draw the hexagon
        this.drawHexagon(this.ctx, x, y, this.hexSize, fillColor, strokeColor, strokeWidth);
        
        // Draw additional elements based on view mode
        if (this.viewMode === 'nodes' && tile.nodes && tile.nodes.length > 0) {
            this.drawNodeIndicators(x, y, tile.nodes);
        }
        
        // Highlight selected tile
        if (this.selectedTile && 
            this.selectedTile.coordinates.q === tile.coordinates.q &&
            this.selectedTile.coordinates.r === tile.coordinates.r &&
            this.selectedTile.coordinates.s === tile.coordinates.s) {
            this.drawHexagon(this.ctx, x, y, this.hexSize + 2, null, '#FFFF00', 3);
        }
    }
    
    drawNodeIndicators(x, y, nodes) {
        const nodeSize = 3;
        const positions = [
            [x - 8, y - 8], [x + 8, y - 8], [x, y],
            [x - 8, y + 8], [x + 8, y + 8]
        ];
        
        nodes.slice(0, 5).forEach((node, index) => {
            const [nodeX, nodeY] = positions[index];
            this.ctx.beginPath();
            this.ctx.arc(nodeX, nodeY, nodeSize, 0, 2 * Math.PI);
            this.ctx.fillStyle = this.getNodeColor(node.type);
            this.ctx.fill();
            this.ctx.strokeStyle = '#000';
            this.ctx.lineWidth = 1;
            this.ctx.stroke();
        });
    }
    
    getNodeColor(nodeType) {
        const colors = {
            'resource': '#FFD700',
            'strategic': '#FF4500',
            'trade': '#32CD32',
            'magical': '#9370DB',
            'cultural': '#FF69B4',
            'defensive': '#8B4513'
        };
        return colors[nodeType] || '#808080';
    }
    
    blendColors(color1, color2, ratio) {
        // Simple color blending function
        const hex1 = color1.replace('#', '');
        const hex2 = color2.replace('#', '');
        
        const r1 = parseInt(hex1.substr(0, 2), 16);
        const g1 = parseInt(hex1.substr(2, 2), 16);
        const b1 = parseInt(hex1.substr(4, 2), 16);
        
        const r2 = parseInt(hex2.substr(0, 2), 16);
        const g2 = parseInt(hex2.substr(2, 2), 16);
        const b2 = parseInt(hex2.substr(4, 2), 16);
        
        const r = Math.round(r1 * (1 - ratio) + r2 * ratio);
        const g = Math.round(g1 * (1 - ratio) + g2 * ratio);
        const b = Math.round(b1 * (1 - ratio) + b2 * ratio);
        
        return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }
    
    // Event handlers
    handleClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left - this.canvas.width / 2 - this.offsetX) / this.scale;
        const y = (e.clientY - rect.top - this.canvas.height / 2 - this.offsetY) / this.scale;
        
        const hexCoord = this.pixelToHex(x, y);
        const tile = this.findTileByCoordinates(hexCoord.q, hexCoord.r, hexCoord.s);
        
        this.selectedTile = tile;
        this.render();
        
        if (this.onTileSelect) {
            this.onTileSelect(tile);
        }
    }
    
    findTileByCoordinates(q, r, s) {
        return this.tiles.find(tile => 
            tile.coordinates.q === q && 
            tile.coordinates.r === r && 
            tile.coordinates.s === s
        );
    }
    
    handleMouseDown(e) {
        this.isDragging = true;
        this.lastMouseX = e.clientX;
        this.lastMouseY = e.clientY;
        this.canvas.style.cursor = 'grabbing';
    }
    
    handleMouseMove(e) {
        if (this.isDragging) {
            const deltaX = e.clientX - this.lastMouseX;
            const deltaY = e.clientY - this.lastMouseY;
            
            this.offsetX += deltaX;
            this.offsetY += deltaY;
            
            this.lastMouseX = e.clientX;
            this.lastMouseY = e.clientY;
            
            this.render();
        }
    }
    
    handleMouseUp(e) {
        this.isDragging = false;
        this.canvas.style.cursor = 'grab';
    }
    
    handleWheel(e) {
        e.preventDefault();
        
        const rect = this.canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        
        const wheel = e.deltaY < 0 ? 1 : -1;
        const zoom = Math.exp(wheel * 0.1);
        
        const newScale = this.scale * zoom;
        if (newScale >= this.minScale && newScale <= this.maxScale) {
            // Zoom towards mouse position
            this.offsetX = mouseX - (mouseX - this.offsetX) * zoom;
            this.offsetY = mouseY - (mouseY - this.offsetY) * zoom;
            this.scale = newScale;
            
            this.render();
        }
    }
    
    // Touch event handlers (simplified)
    handleTouchStart(e) {
        e.preventDefault();
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            this.lastMouseX = touch.clientX;
            this.lastMouseY = touch.clientY;
            this.isDragging = true;
        }
    }
    
    handleTouchMove(e) {
        e.preventDefault();
        if (e.touches.length === 1 && this.isDragging) {
            const touch = e.touches[0];
            const deltaX = touch.clientX - this.lastMouseX;
            const deltaY = touch.clientY - this.lastMouseY;
            
            this.offsetX += deltaX;
            this.offsetY += deltaY;
            
            this.lastMouseX = touch.clientX;
            this.lastMouseY = touch.clientY;
            
            this.render();
        }
    }
    
    handleTouchEnd(e) {
        e.preventDefault();
        this.isDragging = false;
    }
    
    // Zoom controls
    zoomIn() {
        const newScale = this.scale * 1.2;
        if (newScale <= this.maxScale) {
            this.scale = newScale;
            this.render();
        }
    }
    
    zoomOut() {
        const newScale = this.scale / 1.2;
        if (newScale >= this.minScale) {
            this.scale = newScale;
            this.render();
        }
    }
    
    centerView() {
        this.offsetX = 0;
        this.offsetY = 0;
        this.scale = 1.0;
        this.render();
    }
}
