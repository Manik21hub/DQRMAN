class MeshMap {
  constructor(containerId, config = {}) {
    this.containerId = containerId || 'map-container';

    const fallbackLat = config.centerCoords ? config.centerCoords[0] : 28.6139;
    const fallbackLon = config.centerCoords ? config.centerCoords[1] : 77.2090;

    // Fix Leaflet default icon resolution for vendored static assets.
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconUrl: '/vendor/leaflet-images/marker-icon.png',
      shadowUrl: '/vendor/leaflet-images/marker-shadow.png'
    });

    this.map = L.map(this.containerId).setView([fallbackLat, fallbackLon], 13);

    L.tileLayer('/osm-tiles/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(this.map);

    this._markers = {};
    this._edges = {};
    this._activePathEdgeKeys = new Set();
    this._activePathTimeout = null;
    this._attackCircles = {};
    this._ato = new Map();
    this._centered = false;
  }

  centerOnLocation(lat, lon, zoom) {
    this.map.setView([lat, lon], zoom);
    this._centered = true;
  }

  /**
   * Create a Leaflet DivIcon representing a hexagonal drone with arms and rotors.
   * @param {string} colour - Hexagon fill colour (e.g., '#22c55e')
   * @param {number} size - Icon size in pixels (default 28)
   * @returns {L.DivIcon} Leaflet DivIcon with inline SVG drone
   */
  _droneIcon(colour, size = 28) {
    const radius = size / 2;
    const center = size / 2;

    // Calculate hexagon vertices (pointy-top orientation)
    const hexVertices = [];
    for (let i = 0; i < 6; i++) {
      const angle = (i * 60) * Math.PI / 180;
      const x = center + radius * Math.cos(angle);
      const y = center + radius * Math.sin(angle);
      hexVertices.push([x, y]);
    }
    const hexPoints = hexVertices.map(v => `${v[0]},${v[1]}`).join(' ');

    // Arm endpoints extending to NE, SE, SW, NW corners
    const armLength = size * 0.7;
    const armEndpoints = [
      [center + armLength * 0.707, center - armLength * 0.707], // NE
      [center + armLength * 0.707, center + armLength * 0.707], // SE
      [center - armLength * 0.707, center + armLength * 0.707], // SW
      [center - armLength * 0.707, center - armLength * 0.707], // NW
    ];

    const rotorRadius = size * 0.12;

    let svgContent = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">`;

    // Hexagon body
    svgContent += `<polygon points="${hexPoints}" fill="${colour}" stroke="white" stroke-width="1.5"/>`;

    // Arms extending from centre to corners
    armEndpoints.forEach(endpoint => {
      svgContent += `<line x1="${center}" y1="${center}" x2="${endpoint[0]}" y2="${endpoint[1]}" stroke="white" stroke-width="1.5"/>`;
      svgContent += `<circle cx="${endpoint[0]}" cy="${endpoint[1]}" r="${rotorRadius}" fill="white"/>`;
    });

    // Centre dot
    svgContent += `<circle cx="${center}" cy="${center}" r="${rotorRadius * 1.2}" fill="white"/>`;

    // X mark for destroyed nodes
    if (colour === '#ef4444') {
      const xOffset = radius * 0.6;
      svgContent += `<line x1="${center - xOffset}" y1="${center - xOffset}" x2="${center + xOffset}" y2="${center + xOffset}" stroke="white" stroke-width="2"/>`;
      svgContent += `<line x1="${center - xOffset}" y1="${center + xOffset}" x2="${center + xOffset}" y2="${center - xOffset}" stroke="white" stroke-width="2"/>`;
    }

    svgContent += `</svg>`;

    return L.divIcon({
      html: svgContent,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
      className: 'drone-icon'
    });
  }

  /**
   * Update or create markers for nodes on the map.
   * @param {Array} nodes - Array of node objects with id, status, lat, lon
   */
  _updateMarkers(nodes) {
    const nodeStatusColors = {
      'ACTIVE': '#22C55E',
      'DESTROYED': '#EF4444',
      'QUARANTINED': '#EF4444',
      'HEALING': '#00BFFF',
      'ISOLATED': '#8B5CF6'
    };

    const nodeIds = new Set();
    nodes.forEach(node => {
      if (node.lat == null || node.lon == null) return;
      const nodeId = node.node_id || node.id;
      if (!nodeId) return;
      nodeIds.add(nodeId);

      const colour = nodeStatusColors[node.status] || '#a855f7';
      const icon = this._droneIcon(colour, 28);

      if (this._markers[nodeId]) {
        // Update existing marker
        this._markers[nodeId].setLatLng([node.lat, node.lon]);
        this._markers[nodeId].setIcon(icon);
        this._markers[nodeId]._nodeColour = colour;
        this._markers[nodeId]._nodeStatus = node.status;
      } else {
        // Create new marker
        const marker = L.marker([node.lat, node.lon], { icon })
          .bindTooltip(`${String(nodeId).substring(0, 8)} (${node.status})`, { permanent: false })
          .addTo(this.map);
        marker._nodeColour = colour;
        marker._nodeStatus = node.status;
        this._markers[nodeId] = marker;
      }
    });

    // Remove markers for nodes no longer in the list
    Object.keys(this._markers).forEach(id => {
      if (!nodeIds.has(id)) {
        this.map.removeLayer(this._markers[id]);
        delete this._markers[id];
      }
    });
  }

  /**
   * Update or create polylines for edges on the map.
   * @param {Array} nodes - Array of node objects with id, lat, lon
   * @param {Array} edges - Array of edge objects with source, target, weight
   */
  _updateEdges(nodes, edges) {
    // Build lat/lon lookup from nodes
    const nodePositions = {};
    nodes.forEach(node => {
      if (node.lat != null && node.lon != null) {
        const nodeId = node.node_id || node.id;
        if (nodeId) {
          nodePositions[nodeId] = { lat: node.lat, lon: node.lon };
        }
      }
    });

    const edgeKeys = new Set();
    edges.forEach(edge => {
      const sourceId = typeof edge.source === 'object' ? (edge.source.node_id || edge.source.id) : edge.source;
      const targetId = typeof edge.target === 'object' ? (edge.target.node_id || edge.target.id) : edge.target;
      const edgeKey = `${sourceId}--${targetId}`;

      if (!nodePositions[sourceId] || !nodePositions[targetId]) return;
      edgeKeys.add(edgeKey);

      const latlngs = [
        [nodePositions[sourceId].lat, nodePositions[sourceId].lon],
        [nodePositions[targetId].lat, nodePositions[targetId].lon]
      ];

      const weight = edge.weight || 0;
      const isActivePath = this._activePathEdgeKeys.has(edgeKey) || this._activePathEdgeKeys.has(`${targetId}--${sourceId}`);
      const colour = isActivePath ? '#FACC15' : '#14B8A6';
      const lineWeight = 1 + weight * 4;
      const opacity = isActivePath ? 1.0 : (0.2 + weight * 0.6);

      if (this._edges[edgeKey]) {
        // Update existing polyline
        this._edges[edgeKey].setLatLngs(latlngs);
        this._edges[edgeKey].setStyle({ color: colour, weight: lineWeight, opacity });
      } else {
        // Create new polyline
        const polyline = L.polyline(latlngs, {
          color: colour,
          weight: isActivePath ? lineWeight + 2 : lineWeight,
          opacity,
          dashArray: null
        }).addTo(this.map);
        this._edges[edgeKey] = polyline;
      }
    });

    // Remove stale polylines
    Object.keys(this._edges).forEach(key => {
      if (!edgeKeys.has(key)) {
        this.map.removeLayer(this._edges[key]);
        delete this._edges[key];
      }
    });
  }

  /**
   * Update map with new nodes and edges data.
   * @param {Array} nodes - Array of node objects
   * @param {Array} edges - Array of edge objects
   */
  setData(nodes, edges) {
    this._updateMarkers(nodes);
    this._updateEdges(nodes, edges);
  }

  /**
   * Animate self-healing mesh rerouting over 2 seconds.
   * @param {string} destroyedNodeId - ID of the destroyed node
   * @param {Array} oldEdgeKeys - Edge keys to remove (e.g., ['nodeA--nodeB'])
   * @param {Array} newEdges - New edges to add
   * @param {Object} nodeLatLonMap - Lookup of node positions {nodeId: {lat, lon}}
   */
  showSelfHeal(destroyedNodeId, oldEdgeKeys, newEdges, nodeLatLonMap) {
    // Phase 1 (0ms): Highlight old edges in red
    oldEdgeKeys.forEach(key => {
      if (this._edges[key]) {
        this._edges[key].setStyle({ color: '#EF4444', weight: 3, opacity: 0.9 });
      }
    });

    // Phase 2 (400ms): Remove old edges
    setTimeout(() => {
      oldEdgeKeys.forEach(key => {
        if (this._edges[key]) {
          this.map.removeLayer(this._edges[key]);
          delete this._edges[key];
        }
      });
    }, 400);

    // Phase 3 (500ms): Draw new routes as dashed orange
    setTimeout(() => {
      newEdges.forEach(edge => {
        const sourceId = typeof edge.source === 'object' ? (edge.source.node_id || edge.source.id) : edge.source;
        const targetId = typeof edge.target === 'object' ? (edge.target.node_id || edge.target.id) : edge.target;
        const edgeKey = `${sourceId}--${targetId}`;

        if (!nodeLatLonMap[sourceId] || !nodeLatLonMap[targetId]) return;

        const latlngs = [
          [nodeLatLonMap[sourceId].lat, nodeLatLonMap[sourceId].lon],
          [nodeLatLonMap[targetId].lat, nodeLatLonMap[targetId].lon]
        ];

        const weight = edge.weight || 0;
        const polyline = L.polyline(latlngs, {
          color: '#F97316',  // orange
          weight: 1 + weight * 4,
          opacity: 0.2 + weight * 0.6,
          dashArray: '8 6'
        }).addTo(this.map);

        this._edges[edgeKey] = polyline;
      });
    }, 500);

    // Phase 4 (1500ms): Replace dashed orange with solid green, slightly thicker
    setTimeout(() => {
      newEdges.forEach(edge => {
        const sourceId = typeof edge.source === 'object' ? (edge.source.node_id || edge.source.id) : edge.source;
        const targetId = typeof edge.target === 'object' ? (edge.target.node_id || edge.target.id) : edge.target;
        const edgeKey = `${sourceId}--${targetId}`;

        if (this._edges[edgeKey]) {
          const weight = edge.weight || 0;
          this._edges[edgeKey].setStyle({
            color: '#22C55E',  // green
            weight: 2 + weight * 4,  // slightly thicker
            opacity: 0.7,
            dashArray: null  // solid
          });
        }
      });
    }, 1500);

    // Phase 5 (2000ms): Settle to normal blue at standard trust-weighted thickness
    setTimeout(() => {
      newEdges.forEach(edge => {
        const sourceId = typeof edge.source === 'object' ? (edge.source.node_id || edge.source.id) : edge.source;
        const targetId = typeof edge.target === 'object' ? (edge.target.node_id || edge.target.id) : edge.target;
        const edgeKey = `${sourceId}--${targetId}`;

        if (this._edges[edgeKey]) {
          const weight = edge.weight || 0;
          this._edges[edgeKey].setStyle({
            color: '#14B8A6',  // blue
            weight: 1 + weight * 4,
            opacity: 0.2 + weight * 0.6,
            dashArray: null
          });
        }
      });
    }, 2000);
  }

  /**
   * Show attack animation at a node location.
   * @param {string} nodeId - ID of the attacked node
   * @param {string} [attackType] - Optional attack type (e.g., 'spoof')
   */
  showAttack(nodeId, attackType) {
    const marker = this._markers[nodeId];
    if (!marker) return;

    const markerLatLng = marker.getLatLng();
    const nodeColour = marker._nodeColour || '#22c55e';

    // Clear any existing attack animation for this node
    if (this._ato.has(nodeId)) {
      const existingTimeouts = this._ato.get(nodeId);
      existingTimeouts.forEach(id => clearTimeout(id));
      existingTimeouts.forEach(id => clearInterval(id));
    }
    const timeoutIds = [];

    // Inner circle: red fill, 40% opacity
    const innerCircle = L.circle(markerLatLng, {
      radius: 80,
      color: '#ef4444',
      fill: true,
      fillColor: '#ef4444',
      fillOpacity: 0.4,
      weight: 0,
      opacity: 0
    }).addTo(this.map);

    // Outer ring circle: red border, no fill, 60% opacity
    const outerCircle = L.circle(markerLatLng, {
      radius: 200,
      color: '#ef4444',
      fill: false,
      weight: 2,
      opacity: 0.6
    }).addTo(this.map);

    // Temporarily enlarge drone icon to 34 pixels
    const enlargedIcon = this._droneIcon(nodeColour, 34);
    marker.setIcon(enlargedIcon);

    // Outer ring pulse animation: every 300ms, reduce opacity by 0.12, remove after 4 steps
    let pulseStep = 0;
    const pulseInterval = setInterval(() => {
      pulseStep++;
      if (pulseStep >= 4) {
        clearInterval(pulseInterval);
        this.map.removeLayer(outerCircle);
      } else {
        const newOpacity = 0.6 - pulseStep * 0.12;
        outerCircle.setStyle({ opacity: newOpacity });
      }
    }, 300);
    timeoutIds.push(pulseInterval);

    // After 1500ms: remove inner circle and restore normal icon size
    const cleanupTimeout = setTimeout(() => {
      this.map.removeLayer(innerCircle);
      const normalIcon = this._droneIcon(nodeColour, 28);
      marker.setIcon(normalIcon);
      this._ato.delete(nodeId);
    }, 1500);
    timeoutIds.push(cleanupTimeout);

    this._ato.set(nodeId, timeoutIds);
  }

  highlightPath(pathNodeIds, durationMs = 1500) {
    if (!Array.isArray(pathNodeIds) || pathNodeIds.length < 2) {
      return;
    }

    this._activePathEdgeKeys.clear();
    for (let i = 0; i < pathNodeIds.length - 1; i += 1) {
      const a = pathNodeIds[i];
      const b = pathNodeIds[i + 1];
      this._activePathEdgeKeys.add(`${a}--${b}`);
      this._activePathEdgeKeys.add(`${b}--${a}`);
    }

    // Re-apply styling immediately with active-path emphasis.
    this.setData(
      Object.keys(this._markers).map((id) => {
        const marker = this._markers[id];
        const ll = marker.getLatLng();
        return { node_id: id, lat: ll.lat, lon: ll.lng, status: marker._nodeStatus || 'ACTIVE' };
      }),
      Object.keys(this._edges).map((key) => {
        const parts = key.split('--');
        return { source: parts[0], target: parts[1], weight: 0.5 };
      })
    );

    if (this._activePathTimeout) {
      clearTimeout(this._activePathTimeout);
    }

    this._activePathTimeout = setTimeout(() => {
      this._activePathEdgeKeys.clear();
      this._activePathTimeout = null;
    }, durationMs);
  }
}

window.MeshMap = MeshMap;
