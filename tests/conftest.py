"""conftest.py: Shared Pytest fixtures for DQRMAN tests."""

import pytest

from backend.crypto import CryptoModule
from backend.node import Node
from backend.mesh import TrustGraph
from simulation.attacks import AttackSimulator
from backend.server import app

@pytest.fixture(scope="function")
def crypto_module():
    """Returns a fresh CryptoModule instance."""
    return CryptoModule()

@pytest.fixture(scope="function")
def node_a():
    """Returns a fresh Node instance."""
    return Node("node_a")

@pytest.fixture(scope="function")
def node_b():
    """Returns a fresh Node instance."""
    return Node("node_b")

@pytest.fixture(scope="module")
def ten_node_mesh():
    """Creates a 10-node localized mesh for simulation tests."""
    mesh = TrustGraph()
    nodes = [Node(f"node_{i}") for i in range(10)]
    
    # Add nodes to graph
    for node in nodes:
        mesh.add_node(node.node_id, node.public_key)
        
    # Scatter near New Delhi coordinates
    mesh.scatter_nodes_geographically(lat=28.6139, lon=77.2090, spread_m=500)
    
    # Add fully connected edges with 0.8 weight
    for a in nodes:
        for b in nodes:
            if a.node_id != b.node_id:
                mesh.update_edge(
                    a.node_id, 
                    b.node_id, 
                    auth_rate=0.8, 
                    proximity_score=1.0, 
                    recency=1.0
                )
                
    # Nodes also need trust tables updated so they can authenticate each other
    for a in nodes:
        for b in nodes:
            if a.node_id != b.node_id:
                a.trust_table[b.node_id] = b.public_key
                
    return mesh, nodes

@pytest.fixture(scope="function")
def attacker(ten_node_mesh):
    """Returns an AttackSimulator initialized with the 10-node mesh."""
    mesh, nodes = ten_node_mesh
    return AttackSimulator(mesh)

@pytest.fixture(scope="function")
def mock_server():
    """Returns a Flask test client for the server."""
    app.config.update({
        "TESTING": True,
    })
    return app.test_client()
