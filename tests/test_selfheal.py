"""test_selfheal.py: Self-healing and fault-containment tests for mesh resilience."""

import time

from backend.mesh import TrustGraph
from backend.node import AuthProtocol, HeartbeatReceiver, Node, NodeState


def _node_id(i):
    """Create deterministic 64-char hex node IDs for tests."""
    return f"{i:064x}"


def _pubkey(i):
    """Create deterministic pseudo-public-key bytes for tests."""
    return bytes([i % 256]) * 32


def _build_ring(mesh, count, base=1000):
    """Build a directed ring of nodes in the mesh and return node_id list."""
    ring = [_node_id(base + i) for i in range(count)]
    for i, node_id in enumerate(ring):
        mesh.add_node(node_id, _pubkey(base + i))

    for i in range(count):
        src = ring[i]
        dst = ring[(i + 1) % count]
        mesh.update_edge(src, dst, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    return ring


def test_10_nodes_destroy_8_is_operational_true():
    mesh = TrustGraph()
    node_ids = [_node_id(10 + i) for i in range(10)]
    for i, node_id in enumerate(node_ids):
        mesh.add_node(node_id, _pubkey(10 + i))

    for node_id in node_ids[:8]:
        mesh.on_node_failure(node_id)

    assert mesh.is_operational() is True


def test_10_nodes_destroy_9_is_operational_false():
    mesh = TrustGraph()
    node_ids = [_node_id(30 + i) for i in range(10)]
    for i, node_id in enumerate(node_ids):
        mesh.add_node(node_id, _pubkey(30 + i))

    for node_id in node_ids[:9]:
        mesh.on_node_failure(node_id)

    assert mesh.is_operational() is False


def test_on_node_failure_poll_for_survivor_path_within_2_seconds():
    mesh = TrustGraph()
    a, b, c, d = _node_id(101), _node_id(102), _node_id(103), _node_id(104)
    for i, node_id in enumerate([a, b, c, d], start=1):
        mesh.add_node(node_id, _pubkey(100 + i))

    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(a, d, auth_rate=0.2, proximity_score=1.0, recency=1.0)
    mesh.update_edge(d, c, auth_rate=0.2, proximity_score=1.0, recency=1.0)

    mesh.on_node_failure(d)

    deadline = time.time() + 2.0
    path = []
    while time.time() < deadline:
        path = mesh.compute_trust_path(a, c)
        if path:
            break
        time.sleep(0.02)

    assert path == [a, b, c]


def test_on_node_failure_completes_without_external_call():
    mesh = TrustGraph()
    n1, n2 = _node_id(201), _node_id(202)
    mesh.add_node(n1, _pubkey(201))
    mesh.add_node(n2, _pubkey(202))

    event = mesh.on_node_failure(n1)

    assert event['event_type'] == 'NODE_FAILURE'
    assert event['node_id'] == n1
    assert 'timestamp' in event


def test_quarantine_node_3_auth_between_unaffected_nodes_succeeds():
    mesh = TrustGraph()
    ring = _build_ring(mesh, count=6, base=300)

    node_3 = ring[2]
    affected = mesh.quarantine_node(node_3)

    node_5 = ring[4]
    node_6 = ring[5]
    assert node_5 not in affected
    assert node_6 not in affected

    node_a = Node()
    node_b = Node()
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
    result = AuthProtocol().authenticate(node_a, node_b)

    assert result['success'] is True


def test_ring8_quarantine_node_2_auth_5_and_6_succeeds_nfr12():
    mesh = TrustGraph()
    ring = _build_ring(mesh, count=8, base=400)

    node_2 = ring[1]
    affected = mesh.quarantine_node(node_2)

    node_5 = ring[4]
    node_6 = ring[5]
    assert node_5 not in affected
    assert node_6 not in affected

    node_a = Node()
    node_b = Node()
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
    result = AuthProtocol().authenticate(node_a, node_b)

    assert result['success'] is True


def test_rejoin_after_on_node_failure_mesh_accepts_node():
    mesh = TrustGraph()
    node = Node()
    node.transition_to(NodeState.ACTIVE)
    mesh.add_node(node.node_id, node.public_key)

    old_node_id = node.node_id
    mesh.on_node_failure(old_node_id)

    # F-04: failed node must be removed from graph and trust table.
    assert old_node_id not in mesh._graph
    assert old_node_id not in mesh._trust_table

    rejoined = node.rejoin(mesh, neighbours=[])

    assert rejoined is True
    assert node.node_id in mesh._graph
    assert mesh._graph.nodes[node.node_id]['status'] == 'ACTIVE'


def test_on_node_failure_removes_edges_and_invalidates_cached_routes():
    mesh = TrustGraph()
    a, b, c = _node_id(500), _node_id(501), _node_id(502)
    for i, node_id in enumerate([a, b, c], start=1):
        mesh.add_node(node_id, _pubkey(500 + i))

    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    # Warm cache with route through b.
    assert mesh.compute_trust_path(a, c) == [a, b, c]
    assert (a, c) in mesh._path_cache

    mesh.on_node_failure(b)

    # Wait until reroute task finishes.
    deadline = time.time() + 2.0
    while time.time() < deadline and not mesh._paths_dirty:
        time.sleep(0.01)

    assert b not in mesh._graph
    assert b not in mesh._trust_table
    # No path exists after bridge node removal, and old cache is invalidated.
    assert mesh.compute_trust_path(a, c) == []


def test_partitioned_mesh_keeps_each_partition_operational_after_failure():
    mesh = TrustGraph()
    a, b, x, c, d = (_node_id(600), _node_id(601), _node_id(602), _node_id(603), _node_id(604))
    for i, node_id in enumerate([a, b, x, c, d], start=1):
        mesh.add_node(node_id, _pubkey(600 + i))

    # Partition 1 internal connectivity
    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, a, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    # Partition 2 internal connectivity
    mesh.update_edge(c, d, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(d, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    # Single bridge via x
    mesh.update_edge(b, x, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(x, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    # Before failure path exists across bridge.
    assert mesh.compute_trust_path(a, d)

    mesh.on_node_failure(x)

    # Wait for automatic reroute attempt.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        # Cross-partition route should not exist.
        if mesh.compute_trust_path(a, d) == []:
            break
        time.sleep(0.02)

    # Partitioned network must continue operating independently.
    assert mesh.compute_trust_path(a, b) == [a, b]
    assert mesh.compute_trust_path(c, d) == [c, d]
    assert mesh.compute_trust_path(a, d) == []


def test_heartbeat_timeout_triggers_automatic_self_heal():
    mesh = TrustGraph()
    monitor = Node()
    monitor.transition_to(NodeState.ACTIVE)

    a, b, c = _node_id(700), _node_id(701), _node_id(702)
    for i, node_id in enumerate([a, b, c], start=1):
        mesh.add_node(node_id, _pubkey(700 + i))

    # Route a -> b -> c so stale b should break bridge automatically.
    mesh.update_edge(a, b, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    mesh.update_edge(b, c, auth_rate=0.9, proximity_score=1.0, recency=1.0)
    assert mesh.compute_trust_path(a, c) == [a, b, c]

    receiver = HeartbeatReceiver(monitor)
    receiver.last_seen[b] = time.time() - 5.0  # stale
    receiver.last_seen[a] = time.time()        # fresh

    removed = receiver.self_heal_stale_neighbours(mesh, interval=1.0, timeout=3.0)

    assert b in removed
    assert b not in mesh._graph
    assert mesh.compute_trust_path(a, c) == []


def test_nfr12_fault_containment():
    mesh = TrustGraph()
    nodes = [Node() for _ in range(8)]

    for node in nodes:
        node.transition_to(NodeState.ACTIVE)
        mesh.add_node(node.node_id, node.public_key)

    # Build ring where each node is connected to its two direct neighbours.
    for i in range(len(nodes)):
        curr = nodes[i].node_id
        nxt = nodes[(i + 1) % len(nodes)].node_id
        prv = nodes[(i - 1) % len(nodes)].node_id
        mesh.update_edge(curr, nxt, auth_rate=0.9, proximity_score=1.0, recency=1.0)
        mesh.update_edge(curr, prv, auth_rate=0.9, proximity_score=1.0, recency=1.0)

    mesh.quarantine_node(nodes[2].node_id)

    # Nodes at indexes 5 and 6 are not adjacent to index 2.
    result = AuthProtocol().authenticate(nodes[5], nodes[6])
    assert result['success'] is True
    print('NFR-12 PASS: fault contained to direct neighbours only')
