import pytest
from pathlib import Path

from backend.server import app, _init_mesh, TILES_CACHE_DIR


@pytest.fixture
def mock_server():
    """Create and configure a new app instance for each test."""
    app.config.update({
        "TESTING": True,
    })
    
    # Initialize a dummy 3-node mesh for the endpoints to query
    _init_mesh(3)
    
    yield app.test_client()


def test_tile_proxy_serves_local(mock_server):
    """
    Test that the server successfully serves cached local tiles
    without fetching from the upstream OSM proxy if the file exists.
    """
    # Create the dummy tile directories and file
    tile_dir = TILES_CACHE_DIR / "13" / "1234"
    tile_dir.mkdir(parents=True, exist_ok=True)
    
    test_file_path = tile_dir / "5678.png"
    fake_bytes = b"fake_osm_tile_data_for_test"
    
    try:
        test_file_path.write_bytes(fake_bytes)
        
        # Verify via API
        response = mock_server.get('/osm-tiles/13/1234/5678.png')
        
        assert response.status_code == 200
        assert response.data == fake_bytes
    finally:
        # Cleanup injected test data
        if test_file_path.exists():
            test_file_path.unlink()


def test_nodes_api_includes_lat_lon(mock_server):
    """
    Test that the /api/v1/nodes endpoint includes valid 'lat' and 'lon' 
    keys required for frontend Leaflet map marker rendering.
    """
    response = mock_server.get('/api/v1/nodes')
    
    assert response.status_code == 200
    
    nodes_payload = response.get_json()
    assert isinstance(nodes_payload, list)
    assert len(nodes_payload) == 3  # The mock mesh has 3 nodes
    
    for node in nodes_payload:
        assert 'lat' in node, "Node object missing 'lat' attribute"
        assert 'lon' in node, "Node object missing 'lon' attribute"
