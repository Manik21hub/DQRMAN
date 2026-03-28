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
    
    body = response.get_json()
    assert isinstance(body, dict)
    assert 'nodes' in body
    assert 'stats' in body

    nodes_payload = body['nodes']
    assert isinstance(nodes_payload, list)
    assert len(nodes_payload) == 3  # The mock mesh has 3 nodes
    
    for node in nodes_payload:
        assert 'lat' in node, "Node object missing 'lat' attribute"
        assert 'lon' in node, "Node object missing 'lon' attribute"

    stats = body['stats']
    assert stats['total_nodes'] == 3
    assert stats['active_nodes'] == 3
    assert stats['destroyed_nodes'] == 0
    assert stats['mesh_status'] == 'OPERATIONAL'


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


def test_delete_node_endpoint_returns_f09_status_and_reroute_metadata(mock_server):
    """Node kill endpoint should return status counters and reroute preview for F-09 demo."""
    # Ensure simple chain so a reroute path can still be computed after one kill.
    server_mod.mesh.update_edge('node-001', 'node-002', auth_rate=0.9, proximity_score=1.0, recency=1.0)
    server_mod.mesh.update_edge('node-002', 'node-003', auth_rate=0.9, proximity_score=1.0, recency=1.0)

    response = mock_server.delete('/api/v1/nodes/node-002')
    assert response.status_code == 200
    body = response.get_json()

    assert body['node_id'] == 'node-002'
    assert body['surviving_count'] == 2
    assert body['total_nodes'] == 3
    assert body['destroyed_count'] == 1
    assert body['destroyed_percent'] == pytest.approx(33.3, abs=0.2)
    assert body['mesh_status'] in {'DEGRADED', 'PARTITIONED'}
    assert isinstance(body['reroute_path'], list)


def test_delete_node_queues_destroy_event_with_heal_payload(mock_server):
    """Queued destroy event should include edge transition metadata for self-heal animation."""
    server_mod.pending_events.clear()
    server_mod.mesh.update_edge('node-001', 'node-002', auth_rate=0.9, proximity_score=1.0, recency=1.0)

    response = mock_server.delete('/api/v1/nodes/node-001')
    assert response.status_code == 200

    destroy_events = [evt for evt in server_mod.pending_events if evt.get('event_type') == 'NODE_DESTROYED']
    assert destroy_events, 'Expected NODE_DESTROYED event queued for mesh_state updates'
    event = destroy_events[-1]

    assert event.get('destroyed_node_id') == 'node-001'
    assert isinstance(event.get('old_edges'), list)
    assert isinstance(event.get('new_edges'), list)
    assert 'payload' in event
    assert 'mesh_status' in event['payload']
    assert 'destroyed_percent' in event['payload']


def test_delete_node_attempts_docker_stop_when_enabled(mock_server, monkeypatch):
    """When configured, node destroy should attempt docker stop for mapped container."""

    class _Done:
        returncode = 0
        stderr = ''

    def fake_run(cmd, capture_output, text, check, timeout):
        assert cmd[:2] == ['docker', 'stop']
        assert cmd[2] == 'container-node-003'
        return _Done()

    monkeypatch.setattr(server_mod.subprocess, 'run', fake_run)
    server_mod.APP_CONFIG['docker'] = {
        'stop_on_destroy': True,
        'node_container_map': {'node-003': 'container-node-003'},
    }

    try:
        response = mock_server.delete('/api/v1/nodes/node-003')
        assert response.status_code == 200
        body = response.get_json()
        assert body['docker']['attempted'] is True
        assert body['docker']['stopped'] is True
        assert body['docker']['container_name'] == 'container-node-003'
    finally:
        server_mod.APP_CONFIG.pop('docker', None)


def test_scale_report_endpoint_returns_report_json(mock_server, tmp_path):
    """Scale report endpoint should expose benchmark JSON for frontend F-10 screen."""
    report_path = tmp_path / 'f10_scale_report.json'
    report_path.write_text('{"feature":"F-10","pass":true}', encoding='utf-8')

    original = server_mod.APP_CONFIG.get('scale_report_file')
    server_mod.APP_CONFIG['scale_report_file'] = str(report_path)
    try:
        response = mock_server.get('/api/v1/scale-report')
        assert response.status_code == 200
        body = response.get_json()
        assert body['feature'] == 'F-10'
        assert body['pass'] is True
    finally:
        if original is None:
            server_mod.APP_CONFIG.pop('scale_report_file', None)
        else:
            server_mod.APP_CONFIG['scale_report_file'] = original


def test_scale_report_endpoint_missing_returns_404(mock_server):
    """Scale report endpoint should return a clear error when report file is absent."""
    original = server_mod.APP_CONFIG.get('scale_report_file')
    server_mod.APP_CONFIG['scale_report_file'] = 'logs/does-not-exist-f10-report.json'
    try:
        response = mock_server.get('/api/v1/scale-report')
        assert response.status_code == 404
        body = response.get_json()
        assert body['error'] == 'SCALE_REPORT_NOT_FOUND'
    finally:
        if original is None:
            server_mod.APP_CONFIG.pop('scale_report_file', None)
        else:
            server_mod.APP_CONFIG['scale_report_file'] = original


def test_simulation_config_endpoint_normalizes_payload(mock_server):
    """Simulation config endpoint should accept camelCase and persist normalized values."""
    response = mock_server.post(
        '/api/v1/simulation/config',
        json={
            'nodeCount': '7',
            'centerLat': '28.55',
            'centerLon': '77.33',
            'spreadM': '320',
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert body['config']['node_count'] == 7
    assert body['config']['center_lat'] == pytest.approx(28.55)
    assert body['config']['center_lon'] == pytest.approx(77.33)
    assert body['config']['spread_m'] == pytest.approx(320.0)

    read_back = mock_server.get('/api/v1/simulation/config')
    assert read_back.status_code == 200
    read_body = read_back.get_json()
    assert read_body['config']['node_count'] == 7


def test_simulation_start_stop_endpoints_toggle_runtime_and_mesh(mock_server):
    """Simulation start/stop should rebuild then clear mesh state for setup workflow."""
    start_response = mock_server.post(
        '/api/v1/simulation/start',
        json={'node_count': 4, 'center_lat': 28.61, 'center_lon': 77.20, 'spread_m': 250},
    )
    assert start_response.status_code == 200
    start_body = start_response.get_json()
    assert start_body['success'] is True
    assert start_body['running'] is True
    assert start_body['active_nodes'] == 4

    nodes_response = mock_server.get('/api/v1/nodes')
    assert nodes_response.status_code == 200
    nodes_body = nodes_response.get_json()
    assert nodes_body['stats']['active_nodes'] == 4
    active_nodes = [node for node in nodes_body['nodes'] if node.get('status') != 'DESTROYED']
    assert len(active_nodes) == 4

    stop_response = mock_server.post('/api/v1/simulation/stop', json={})
    assert stop_response.status_code == 200
    stop_body = stop_response.get_json()
    assert stop_body['success'] is True
    assert stop_body['running'] is False

    nodes_after_stop = mock_server.get('/api/v1/nodes')
    assert nodes_after_stop.status_code == 200
    after_body = nodes_after_stop.get_json()
    assert after_body['stats']['active_nodes'] == 0
    assert after_body['nodes'] == []
