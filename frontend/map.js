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
}

window.MeshMap = MeshMap;
