"""test_scale.py: Scalability and concurrency tests for DQRMAN mesh.

Validates 50-node authentication failure rates, deadlock absence under concurrent
load, and path latency compliance with NFR-05 (< 100ms) at 50-node scale.
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.node import Node, NodeState, AuthProtocol
from backend.mesh import TrustGraph


# ---------------------------------------------------------------------------
# Helper factories (mirror test_mesh.py conventions for TrustGraph-only tests)
# ---------------------------------------------------------------------------

def _node_id(i):
    """Deterministic 64-char hex node ID for TrustGraph-only tests."""
    return f"{i:064x}"


def _pubkey(i):
    """Deterministic pseudo-public-key bytes for TrustGraph-only tests."""
    return bytes([i % 256]) * 32


def _make_active_node(label: str) -> Node:
    """Create and activate a real Node instance."""
    node = Node(label)
    node.rejoin(TrustGraph(), [])
    return node


# ---------------------------------------------------------------------------
# TEST 1 — 50-node scale: failure rate under 1%
# ---------------------------------------------------------------------------

def test_50_node_scale():
    """Create 50 active nodes, run 1000 random pair authentications.

    Asserts that the failure rate stays below 1 % (only genuine crypto
    failures are tolerated — no false positives).
    """
    nodes = [_make_active_node(f"scale-{i:03d}") for i in range(50)]

    # Register every node in a shared mesh so trust tables are populated
    mesh = TrustGraph()
    for node in nodes:
        mesh.add_node(node.node_id, node.public_key)

    # Cross-populate trust tables so all pairs can authenticate
    for a in nodes:
        for b in nodes:
            if a is not b:
                a.trust_table[b.node_id] = b.public_key

    mesh.scatter_nodes_geographically(28.6139, 77.2090, spread_m=500)

    random.seed(42)
    protocol = AuthProtocol()
    successes = 0
    failures = 0

    for _ in range(1000):
        i, j = random.sample(range(50), 2)
        result = protocol.authenticate(nodes[i], nodes[j])
        if result['success']:
            successes += 1
        else:
            failures += 1

    total = successes + failures
    failure_rate = failures / total
    assert failure_rate < 0.01, (
        f"Failure rate {failure_rate:.4f} exceeds 1 % limit "
        f"({failures} failures out of {total} attempts)"
    )
    print(f"\n50-node failure rate: {failure_rate:.4f} — PASS")


# ---------------------------------------------------------------------------
# TEST 2 — No deadlock under 10 simultaneous concurrent auth calls
# ---------------------------------------------------------------------------

def test_no_deadlock():
    """Run 10 simultaneous authenticate(a, b) calls via ThreadPoolExecutor.

    All futures must complete within 30 seconds — any timeout indicates a
    deadlock in the Node locking or NonceCache logic.
    """
    # Build 10 isolated pairs (each pair shares trust tables)
    pairs = []
    for i in range(10):
        a = _make_active_node(f"dl-a-{i:02d}")
        b = _make_active_node(f"dl-b-{i:02d}")
        a.trust_table[b.node_id] = b.public_key
        b.trust_table[a.node_id] = a.public_key
        pairs.append((a, b))

    protocol = AuthProtocol()

    def run_auth(pair):
        a, b = pair
        return protocol.authenticate(a, b)

    completed = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(run_auth, pair): i for i, pair in enumerate(pairs)}
        for future in as_completed(futures, timeout=30):
            result = future.result()
            assert result is not None
            completed += 1

    assert completed == 10, f"Only {completed}/10 futures completed"
    print("\nNo deadlock — PASS")


# ---------------------------------------------------------------------------
# TEST 3 — Path computation NFR-05: < 100 ms at 50-node scale
# ---------------------------------------------------------------------------

def test_path_nfr05_50_nodes():
    """Run 100 trust-path queries on a 50-node, 150-edge graph.

    Each call must complete within 100 ms to satisfy NFR-05 routing latency
    requirements.
    """
    mesh = TrustGraph()

    # Add 50 nodes with deterministic IDs
    node_ids = [_node_id(i) for i in range(50)]
    for i, nid in enumerate(node_ids):
        mesh.add_node(nid, _pubkey(i))

    # Add 150 random directed edges at weight 0.8
    random.seed(42)
    edges_added = 0
    attempts = 0
    while edges_added < 150 and attempts < 5000:
        attempts += 1
        i = random.randint(0, 49)
        j = random.randint(0, 49)
        if i == j:
            continue
        mesh.update_edge(node_ids[i], node_ids[j], auth_rate=0.8, proximity_score=1.0, recency=1.0)
        edges_added += 1

    source = node_ids[0]
    target = node_ids[49]

    durations = []
    for _ in range(100):
        # Force a fresh edge-weight touch so routing can't short-circuit trivially
        i, j = random.randint(1, 48), random.randint(1, 48)
        if i != j:
            mesh.update_edge(node_ids[i], node_ids[j], auth_rate=0.8, proximity_score=1.0, recency=1.0)

        t0 = time.perf_counter()
        mesh.compute_trust_path(source, target)
        elapsed = time.perf_counter() - t0
        durations.append(elapsed)

    slow_calls = [d for d in durations if d > 0.1]
    max_ms = max(durations) * 1000

    assert len(slow_calls) == 0, (
        f"{len(slow_calls)} path computations exceeded 100 ms "
        f"(max was {max_ms:.1f} ms)"
    )
    print(f"\nMax path time: {max_ms:.1f}ms — PASS")
