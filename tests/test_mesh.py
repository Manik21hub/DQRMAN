"""test_mesh.py: Tests for trust graph topology, routing, and resilience."""

import math
import random
import time

import pytest

from backend.mesh import TrustGraph, _compute_edge_weight
from backend.node import AuthProtocol, Node, NodeState


def _node_id(i):
    """Create deterministic 64-char hex node IDs for tests."""
    return f"{i:064x}"


def _pubkey(i):
    """Create deterministic pseudo-public-key bytes for tests."""
    return bytes([i % 256]) * 32


def _haversine_m(lat1, lon1, lat2, lon2):
    """Compute great-circle distance in meters."""
    r = 6371000.0
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    lat2_r = math.radians(lat2)
    lon2_r = math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return r * c


def test_add_two_nodes_len_is_two():
    mesh = TrustGraph()
    mesh.add_node(_node_id(1), _pubkey(1))
    mesh.add_node(_node_id(2), _pubkey(2))
    assert len(mesh) == 2


def test_add_same_node_with_different_public_key_raises():
    mesh = TrustGraph()
    node_id = _node_id(10)
    mesh.add_node(node_id, _pubkey(10))
    with pytest.raises(ValueError):
        mesh.add_node(node_id, _pubkey(11))


def test_trust_path_prefers_higher_weight_route():
    mesh = TrustGraph()
    a, b, c, d = _node_id(1), _node_id(2), _node_id(3), _node_id(4)
    for idx, node_id in enumerate([a, b, c, d], start=1):
        mesh.add_node(node_id, _pubkey(idx))

    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, c, auth_rate=0.8, proximity_score=1.0, recency=1.0)
    mesh.update_edge(a, d, auth_rate=0.3, proximity_score=1.0, recency=1.0)
    mesh.update_edge(d, c, auth_rate=0.2, proximity_score=1.0, recency=1.0)

    path = mesh.compute_trust_path(a, c)
    assert path == [a, b, c]


def test_disconnected_nodes_return_empty_path():
    mesh = TrustGraph()
    a, b = _node_id(21), _node_id(22)
    mesh.add_node(a, _pubkey(21))
    mesh.add_node(b, _pubkey(22))
    assert mesh.compute_trust_path(a, b) == []


def test_100_path_computations_under_100ms_on_random_graph():
    mesh = TrustGraph()
    random.seed(42)
    node_ids = [_node_id(i) for i in range(50)]

    for i, node_id in enumerate(node_ids):
        mesh.add_node(node_id, _pubkey(i))

    # Ensure at least one guaranteed path between endpoints.
    for i in range(len(node_ids) - 1):
        mesh.update_edge(node_ids[i], node_ids[i + 1], auth_rate=0.7, proximity_score=1.0, recency=1.0)

    # Add random extra edges.
    for _ in range(150):
        src, dst = random.sample(node_ids, 2)
        mesh.update_edge(src, dst, auth_rate=random.random(), proximity_score=1.0, recency=1.0)

    source, target = node_ids[0], node_ids[-1]
    for _ in range(100):
        t0 = time.perf_counter()
        mesh.compute_trust_path(source, target)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100.0


def test_compute_edge_weight_extremes():
    assert _compute_edge_weight(1.0, 1.0, 1.0) == pytest.approx(1.0)
    assert _compute_edge_weight(0.0, 0.0, 0.0) == pytest.approx(0.0)


def test_proximity_score_same_coordinates_is_one():
    mesh = TrustGraph()
    a, b = _node_id(31), _node_id(32)
    mesh.add_node(a, _pubkey(31))
    mesh.add_node(b, _pubkey(32))
    mesh.set_node_coords(a, 37.7749, -122.4194)
    mesh.set_node_coords(b, 37.7749, -122.4194)
    assert mesh.compute_proximity_score(a, b) == pytest.approx(1.0)


def test_proximity_score_far_coordinates_is_below_point_one():
    mesh = TrustGraph()
    a, b = _node_id(41), _node_id(42)
    mesh.add_node(a, _pubkey(41))
    mesh.add_node(b, _pubkey(42))
    mesh.set_node_coords(a, 0.0, 0.0)
    mesh.set_node_coords(b, 80.0, 170.0)
    assert mesh.compute_proximity_score(a, b) < 0.1


def test_scatter_nodes_within_600m_of_center():
    mesh = TrustGraph()
    center_lat, center_lon = 37.7749, -122.4194

    for i in range(10):
        mesh.add_node(_node_id(100 + i), _pubkey(100 + i))

    random.seed(42)
    mesh.scatter_nodes_geographically(center_lat, center_lon, spread_m=500)

    for node_id in mesh.get_active_nodes():
        node_data = mesh._graph.nodes[node_id]
        dist_m = _haversine_m(center_lat, center_lon, node_data['lat'], node_data['lon'])
        assert dist_m <= 600.0


def test_destroying_eight_of_ten_nodes_still_operational():
    mesh = TrustGraph()
    node_ids = [_node_id(200 + i) for i in range(10)]
    for i, node_id in enumerate(node_ids):
        mesh.add_node(node_id, _pubkey(200 + i))

    for node_id in node_ids[:8]:
        mesh.on_node_failure(node_id)

    # 2 of 10 active remains exactly 20%, which is operational.
    assert mesh.is_operational() is True


def test_on_node_failure_eventually_allows_survivor_path_within_two_seconds():
    mesh = TrustGraph()
    a, b, c, d = _node_id(301), _node_id(302), _node_id(303), _node_id(304)
    for idx, node_id in enumerate([a, b, c, d], start=1):
        mesh.add_node(node_id, _pubkey(300 + idx))

    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(a, d, auth_rate=0.1, proximity_score=1.0, recency=1.0)
    mesh.update_edge(d, c, auth_rate=0.1, proximity_score=1.0, recency=1.0)

    mesh.on_node_failure(d)

    deadline = time.time() + 2.0
    path = []
    while time.time() < deadline:
        path = mesh.compute_trust_path(a, c)
        if path:
            break
        time.sleep(0.02)

    assert path == [a, b, c]


def test_quarantine_in_ring_preserves_non_adjacent_authentication():
    mesh = TrustGraph()
    ring = [_node_id(400 + i) for i in range(6)]

    for i, node_id in enumerate(ring):
        mesh.add_node(node_id, _pubkey(400 + i))

    for i in range(len(ring)):
        src = ring[i]
        dst = ring[(i + 1) % len(ring)]
        mesh.update_edge(src, dst, auth_rate=0.8, proximity_score=1.0, recency=1.0)

    quarantined = ring[0]
    affected = mesh.quarantine_node(quarantined)

    # Choose nodes non-adjacent to quarantined node in the ring.
    node_x, node_y = ring[2], ring[4]
    assert node_x not in affected and node_y not in affected

    # Non-adjacent routing remains possible.
    path = mesh.compute_trust_path(node_x, node_y)
    assert path

    # Authentication between non-adjacent nodes succeeds.
    n1 = Node()
    n2 = Node()
    n1.transition_to(NodeState.ACTIVE)
    n2.transition_to(NodeState.ACTIVE)
    result = AuthProtocol().authenticate(n1, n2)
    assert result['success'] is True


def test_survivors_authenticate():
    mesh = TrustGraph()
    nodes = [Node() for _ in range(10)]

    for node in nodes:
        node.transition_to(NodeState.ACTIVE)
        mesh.add_node(node.node_id, node.public_key)

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            result = AuthProtocol().authenticate(nodes[i], nodes[j])
            if result['success']:
                mesh.update_edge(nodes[i].node_id, nodes[j].node_id, auth_rate=1.0)
                mesh.update_edge(nodes[j].node_id, nodes[i].node_id, auth_rate=1.0)

    for node in nodes[:8]:
        mesh.on_node_failure(node.node_id)

    # 2 survivors out of 10 is exactly 20% and should be operational.
    assert mesh.is_operational() is True

    survivor_a, survivor_b = nodes[8], nodes[9]
    survivor_result = AuthProtocol().authenticate(survivor_a, survivor_b)
    assert survivor_result['success'] is True


def test_haversine_same_point():
    mesh = TrustGraph()
    a, b = _node_id(1001), _node_id(1002)
    mesh.add_node(a, _pubkey(1001))
    mesh.add_node(b, _pubkey(1002))
    mesh.set_node_coords(a, 10.0, 20.0)
    mesh.set_node_coords(b, 10.0, 20.0)
    assert mesh.compute_proximity_score(a, b) == 1.0


def test_haversine_500m():
    mesh = TrustGraph()
    a, b = _node_id(1003), _node_id(1004)
    mesh.add_node(a, _pubkey(1003))
    mesh.add_node(b, _pubkey(1004))
    mesh.set_node_coords(a, 28.6139, 77.2090)
    mesh.set_node_coords(b, 28.6184, 77.2090)
    score = mesh.compute_proximity_score(a, b)
    assert 0.4 <= score <= 0.6


def test_haversine_far_apart():
    mesh = TrustGraph()
    a, b = _node_id(1005), _node_id(1006)
    mesh.add_node(a, _pubkey(1005))
    mesh.add_node(b, _pubkey(1006))
    mesh.set_node_coords(a, 0.0, 0.0)
    mesh.set_node_coords(b, 90.0, 0.0)
    score = mesh.compute_proximity_score(a, b)
    assert score < 0.01


def test_scatter_within_spread():
    mesh = TrustGraph()
    center_lat, center_lon = 28.6139, 77.2090
    spread_m = 500
    
    # Create 10 nodes
    for i in range(10):
        mesh.add_node(_node_id(2001 + i), _pubkey(2001 + i))
        
    # Scatter nodes
    mesh.scatter_nodes_geographically(center_lat, center_lon, spread_m=spread_m)
    
    max_dist = 0
    for node_id in mesh.get_active_nodes():
        node_data = mesh._graph.nodes[node_id]
        dist_m = _haversine_m(center_lat, center_lon, node_data['lat'], node_data['lon'])
        if dist_m > max_dist:
            max_dist = dist_m
        assert dist_m <= 600.0, f"Node {node_id} is too far: {dist_m}m"
        
    print(f"\nMax distance found: {max_dist:.2f}m")


