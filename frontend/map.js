class MeshMap {
  constructor(containerId) {
    this.containerId = containerId || 'map-container';

    // Fix Leaflet default icon resolution for vendored static assets.
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconUrl: '/vendor/leaflet-images/marker-icon.png',
      shadowUrl: '/vendor/leaflet-images/marker-shadow.png'
    });

    this.map = L.map(this.containerId);

    L.tileLayer('/osm-tiles/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(this.map);

    this._markers = {};
    this._edges = {};
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
}

window.MeshMap = MeshMap;
