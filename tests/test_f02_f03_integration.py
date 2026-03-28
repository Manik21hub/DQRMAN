"""test_f02_f03_integration.py: Integration tests for F-02 (ML-DSA-65 Auth) + F-03 (Mesh Routing).

Tests that verify ML-DSA-65 authentication (F-02) and mesh routing/trust scoring (F-03)
work together correctly in the DQRMAN protocol.
"""

import json
import time

import pytest

from backend.mesh import TrustGraph
from backend.node import AuthProtocol, Node, NodeState


def test_f02_authentication_updates_f03_mesh_trust():
    """F-02 + F-03: Verify authentication updates mesh routing trust.

    Demonstrates that successful ML-DSA-65 authentication between two nodes
    increases their trust score in the mesh routing graph, making them
    preferred for routing.
    """
    # Create mesh and nodes
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()

    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    # Add nodes to mesh
    mesh.add_node(node_a.node_id, node_a.public_key)
    mesh.add_node(node_b.node_id, node_b.public_key)

    # Create initial edge with low trust
    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.2, proximity_score=1.0, recency=0.5)
    initial_stats = mesh.get_edge_stats(node_a.node_id, node_b.node_id)
    initial_weight = initial_stats['weight']

    # Perform F-02 authentication
    protocol = AuthProtocol()
    auth_result = protocol.authenticate(node_a, node_b)

    # Should succeed
    assert auth_result['success'] is True

    # Update F-03 mesh trust based on F-02 result
    protocol.update_mesh_trust(auth_result, mesh)

    # Check that mesh trust was updated
    updated_stats = mesh.get_edge_stats(node_a.node_id, node_b.node_id)
    updated_weight = updated_stats['weight']

    # Trust weight should increase after successful auth
    assert updated_weight > initial_weight
    assert updated_stats['auth_attempt_count'] > initial_stats['auth_attempt_count']
    assert updated_stats['auth_success_count'] > initial_stats['auth_success_count']


def test_f03_prefers_authenticated_paths_over_low_trust():
    """F-03: Mesh routing prefers paths with high trust from F-02 auth.

    Creates a 4-node mesh with two possible paths from A to D.
    One path (A→B→D) has been authenticated and refreshed, building trust.
    Other path (A→C→D) has lower initial trust.
    Verifies that compute_trust_path prefers the authenticated path.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()
    node_c = Node()
    node_d = Node()

    for node in [node_a, node_b, node_c, node_d]:
        node.transition_to(NodeState.ACTIVE)
        mesh.add_node(node.node_id, node.public_key)

    # Create two paths: A→B→D and A→C→D
    # Path 1: A→B→D
    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)
    mesh.update_edge(node_b.node_id, node_d.node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)

    # Path 2: A→C→D (lower initial trust)
    mesh.update_edge(node_a.node_id, node_c.node_id, auth_rate=0.3, proximity_score=1.0, recency=1.0)
    mesh.update_edge(node_c.node_id, node_d.node_id, auth_rate=0.3, proximity_score=1.0, recency=1.0)

    # Authenticate A↔B and B↔D (building path 1 trust)
    protocol = AuthProtocol()

    auth_ab = protocol.authenticate(node_a, node_b)
    assert auth_ab['success']
    protocol.update_mesh_trust(auth_ab, mesh)

    auth_bd = protocol.authenticate(node_b, node_d)
    assert auth_bd['success']
    protocol.update_mesh_trust(auth_bd, mesh)

    # Compute path from A to D should prefer the authenticated path 1
    path = mesh.compute_trust_path(node_a.node_id, node_d.node_id)

    # Should route through B (the authenticated path)
    assert path == [node_a.node_id, node_b.node_id, node_d.node_id]


def test_f02_f03_mutual_authentication_updates_bidirectional_trust():
    """F-02 + F-03: Verify mutual auth updates both edge directions.

    ML-DSA-65 authentication is bidirectional (both A→B and B→A verified).
    The mesh should update trust in both directions.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()

    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    mesh.add_node(node_a.node_id, node_a.public_key)
    mesh.add_node(node_b.node_id, node_b.public_key)

    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)
    mesh.update_edge(node_b.node_id, node_a.node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)

    stats_ab_before = mesh.get_edge_stats(node_a.node_id, node_b.node_id)
    stats_ba_before = mesh.get_edge_stats(node_b.node_id, node_a.node_id)

    # Authenticate
    protocol = AuthProtocol()
    auth_result = protocol.authenticate(node_a, node_b)
    assert auth_result['success']

    # Update mesh with mutual trust refresh
    protocol.update_mesh_trust(auth_result, mesh)

    # Both directions should update
    stats_ab_after = mesh.get_edge_stats(node_a.node_id, node_b.node_id)
    stats_ba_after = mesh.get_edge_stats(node_b.node_id, node_a.node_id)

    assert stats_ab_after['weight'] > stats_ab_before['weight']
    assert stats_ba_after['weight'] > stats_ba_before['weight']


def test_f03_message_routing_through_authenticated_path():
    """F-03: Route message through authenticated path using F-02 trust.

    Uses F-02 authenticated paths as the basis for F-03 message routing,
    demonstrating that routing decisions are based on authentication history.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()
    node_c = Node()

    for node in [node_a, node_b, node_c]:
        node.transition_to(NodeState.ACTIVE)
        mesh.add_node(node.node_id, node.public_key)

    # Create path A→B→C
    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(node_b.node_id, node_c.node_id, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    # Authenticate the path
    protocol = AuthProtocol()
    auth_ab = protocol.authenticate(node_a, node_b)
    protocol.update_mesh_trust(auth_ab, mesh)

    auth_bc = protocol.authenticate(node_b, node_c)
    protocol.update_mesh_trust(auth_bc, mesh)

    # Compute trust path
    path = mesh.compute_trust_path(node_a.node_id, node_c.node_id)
    assert path == [node_a.node_id, node_b.node_id, node_c.node_id]

    # Route a message through this authenticated path
    message = b"Test message through authenticated path"
    packet = mesh.route_message(message, node_a, path, node_c.node_id)

    assert packet['message'] == message.hex()
    assert packet['source'] == node_a.node_id
    assert packet['destination'] == node_c.node_id
    assert len(packet['signatures']) == 3


def test_f03_trust_decay_after_f02_auth_absence():
    """F-03: Trust decays over time without F-02 authentication refresh.

    Demonstrates the trust decay mechanism: edges that haven't been
    refreshed with successful F-02 authentication gradually lose routing
    preference.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()

    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    mesh.add_node(node_a.node_id, node_a.public_key)
    mesh.add_node(node_b.node_id, node_b.public_key)

    # Create edge and perform authentication to build initial trust
    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    protocol = AuthProtocol()
    auth = protocol.authenticate(node_a, node_b)
    protocol.update_mesh_trust(auth, mesh)

    trusted_weight = mesh.get_edge_stats(node_a.node_id, node_b.node_id)['weight']

    # Simulate time passage by manually setting old auth time
    edge_data = mesh._graph[node_a.node_id][node_b.node_id]
    old_time = time.time() - 1200  # 20 minutes ago
    edge_data['last_auth_time'] = old_time

    # Run decay
    mesh.decay_stale_trust_edges(max_age_seconds=600)
    decayed_weight = mesh.get_edge_stats(node_a.node_id, node_b.node_id)['weight']

    # Trust should have decayed
    assert decayed_weight < trusted_weight


def test_f02_f03_concurrent_authentications_build_mesh():
    """F-02 + F-03: Multiple simultaneous authentications build trust mesh.

    Demonstrates a realistic scenario where multiple nodes authenticate
    pairwise, and the mesh routing graph builds up based on successful
    authentications.
    """
    mesh = TrustGraph()
    num_nodes = 4
    nodes = [Node() for _ in range(num_nodes)]

    for node in nodes:
        node.transition_to(NodeState.ACTIVE)
        mesh.add_node(node.node_id, node.public_key)

    # Create initial mesh topology (ring)
    for i in range(num_nodes):
        src = nodes[i].node_id
        dst = nodes[(i + 1) % num_nodes].node_id
        mesh.update_edge(src, dst, auth_rate=0.5, proximity_score=1.0, recency=1.0)
        mesh.update_edge(dst, src, auth_rate=0.5, proximity_score=1.0, recency=1.0)

    # Perform pairwise authentications
    protocol = AuthProtocol()
    for i in range(num_nodes):
        for j in range(i + 1, num_nodes):
            auth = protocol.authenticate(nodes[i], nodes[j])
            # Update mesh if auth succeeds
            if auth['success']:
                protocol.update_mesh_trust(auth, mesh)
                # Also ensure edges exist in both directions
                mesh.update_edge(nodes[i].node_id, nodes[j].node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)
                mesh.update_edge(nodes[j].node_id, nodes[i].node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)

    # All nodes should be connected
    for i in range(num_nodes):
        for j in range(num_nodes):
            if i != j:
                path = mesh.compute_trust_path(nodes[i].node_id, nodes[j].node_id)
                assert len(path) > 0, f"No path from node {i} to node {j}"


def test_f02_failed_auth_reduces_f03_trust():
    """F-02 + F-03: Failed authentication reduces routing trust.

    Demonstrates that failed F-02 authentication attempts reduce the
    trust score, making that edge less preferred for routing.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()

    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    mesh.add_node(node_a.node_id, node_a.public_key)
    mesh.add_node(node_b.node_id, node_b.public_key)

    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    initial_weight = mesh.get_edge_stats(node_a.node_id, node_b.node_id)['weight']

    # Simulate failed authentication
    failed_result = {
        'success': False,
        'failure_reason': 'INVALID_SIGNATURE',
        'node_a_id': node_a.node_id,
        'node_b_id': node_b.node_id,
    }

    protocol = AuthProtocol()
    protocol.update_mesh_trust(failed_result, mesh)

    final_weight = mesh.get_edge_stats(node_a.node_id, node_b.node_id)['weight']

    # Trust should have decreased
    assert final_weight < initial_weight


def test_f03_edge_stats_reflect_f02_auth_history():
    """F-03: Edge statistics accurately reflect authentication history.

    Verifies that edge statistics computed by get_edge_stats() correctly
    reflect the history of successful and failed authentications managed
    by F-02.
    """
    mesh = TrustGraph()
    node_a = Node()
    node_b = Node()

    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    mesh.add_node(node_a.node_id, node_a.public_key)
    mesh.add_node(node_b.node_id, node_b.public_key)

    mesh.update_edge(node_a.node_id, node_b.node_id, auth_rate=0.5, proximity_score=1.0, recency=1.0)

    # Perform sequence of authentications
    protocol = AuthProtocol()
    success_count = 0
    total_count = 5

    for _ in range(total_count):
        auth = protocol.authenticate(node_a, node_b)
        if auth['success']:
            success_count += 1
        protocol.update_mesh_trust(auth, mesh)

    # Get stats
    stats = mesh.get_edge_stats(node_a.node_id, node_b.node_id)

    # Stats should reflect actual history
    assert stats['auth_success_count'] == success_count
    assert stats['auth_attempt_count'] == total_count
    if total_count > 0:
        expected_rate = success_count / total_count
        assert stats['auth_rate'] == pytest.approx(expected_rate)
