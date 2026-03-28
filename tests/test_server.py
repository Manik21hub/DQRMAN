import pytest
from pathlib import Path

from backend.server import app, _init_mesh, TILES_CACHE_DIR
import backend.server as server_mod


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


def test_flush_emits_current_topology_with_events(monkeypatch):
    """Debounced websocket flush should include current nodes/edges and queued events."""
    _init_mesh(3)
    # Create at least one edge so payload has non-empty topology signal.
    server_mod.mesh.update_edge('node-001', 'node-002', auth_rate=0.9, proximity_score=1.0, recency=1.0)

    captured = {}

    def fake_emit(event_name, message):
        captured['event_name'] = event_name
        captured['message'] = message

    monkeypatch.setattr(server_mod.socketio, 'emit', fake_emit)

    server_mod.pending_events.clear()
    server_mod.pending_events.append({'event_type': 'TEST_EVENT'})
    server_mod._flush()

    assert captured['event_name'] == 'mesh_state'
    payload = captured['message']['payload']
    assert isinstance(payload['nodes'], list)
    assert isinstance(payload['edges'], list)
    assert len(payload['nodes']) == 3
    assert len(payload['events']) == 1
    assert payload['events'][0]['event_type'] == 'TEST_EVENT'


def test_route_endpoint_returns_path_and_queues_route_event(mock_server):
    """Route API computes trust path and returns it for frontend highlighting."""
    # Ensure deterministic graph connectivity.
    server_mod.mesh.update_edge('node-001', 'node-002', auth_rate=0.9, proximity_score=1.0, recency=1.0)
    server_mod.mesh.update_edge('node-002', 'node-003', auth_rate=0.9, proximity_score=1.0, recency=1.0)

    response = mock_server.post('/api/v1/route', json={
        'source_node_id': 'node-001',
        'target_node_id': 'node-003',
        'message_id': 'msg-1',
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['path'] == ['node-001', 'node-002', 'node-003']
    assert payload['duration_ms'] >= 0.0


def test_replay_attack_endpoint_emits_and_queues_detection_event(mock_server, monkeypatch):
    """Replay demo endpoint should emit attack_detected and queue feed event data."""
    emitted = []

    def fake_emit(event_name, payload):
        emitted.append((event_name, payload))

    monkeypatch.setattr(server_mod.socketio, 'emit', fake_emit)
    server_mod.pending_events.clear()

    response = mock_server.post('/api/v1/attack', json={
        'attack_type': 'replay',
        'target_node_id': 'node-001',
    })

    assert response.status_code == 202
    body = response.get_json()
    assert body['status'] == 'accepted'
    assert body['attack_type'] == 'replay'

    assert emitted, 'Expected attack_detected websocket emission'
    event_name, payload = emitted[0]
    assert event_name == 'attack_detected'
    assert payload['attack_type'] == 'replay'
    assert payload['detected'] is True
    assert payload['detection_reason'] == 'TIMESTAMP_EXPIRED'

    assert server_mod.pending_events, 'Expected queued event for mesh_state event feed'
    queued = server_mod.pending_events[-1]
    assert queued['event_type'] == server_mod.EVENT_REPLAY_DETECTED
    assert queued['detection_reason'] == 'TIMESTAMP_EXPIRED'
