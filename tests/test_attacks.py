import time
import pytest
from unittest.mock import patch, MagicMock

from backend.node import Node, AnomalyLogger
from backend.mesh import TrustGraph
from simulation.attacks import AttackSimulator


@pytest.fixture
def mesh():
    return TrustGraph()


@pytest.fixture
def nodes(mesh):
    # Create two nodes for testing attacks
    n1 = Node('node1')
    n2 = Node('node2')
    n1.rejoin(mesh, [])
    n2.rejoin(mesh, [])
    # Add each to the other's trust table to allow authentication
    n1.trust_table[n2.node_id] = n2.public_key
    n2.trust_table[n1.node_id] = n1.public_key
    return n1, n2


@pytest.fixture
def simulator(mesh):
    return AttackSimulator(mesh)


def test_replay_attack_outside_window(nodes, simulator):
    """(1) THE OFFICIAL TEST: run replay_attack_outside_window 500 times."""
    n1, n2 = nodes
    undetected = 0
    
    current_time = 1000000.0
    
    def mock_time():
        return current_time
        
    def mock_sleep(seconds):
        nonlocal current_time
        current_time += seconds
        
    with patch('time.time', side_effect=mock_time), patch('time.sleep', side_effect=mock_sleep):
        for _ in range(500):
            # Pass delay=10.0 so it sleeps 10s (advancing simulated time by 10s)
            result = simulator.replay_attack_outside_window(n1, n2, delay=10.0)
            if not result.detected:
                undetected += 1
                
    assert undetected == 0
    print('500/500 detected')


def test_replay_attack_within_window(nodes, simulator):
    """(2) Run replay_attack_within_window 200 times, assert all have reason 'DUPLICATE_NONCE'."""
    n1, n2 = nodes
    for _ in range(200):
        result = simulator.replay_attack_within_window(n1, n2)
        assert result.detected is True
        assert result.detection_reason == 'DUPLICATE_NONCE'


def test_spoof_attack(nodes, simulator):
    """(3) Run spoof_attack 100 times, assert all detected with 'INVALID_SIGNATURE'."""
    n1, n2 = nodes
    for _ in range(100):
        result = simulator.spoof_attack(n1, n2)
        assert result.detected is True
        assert result.detection_reason == 'INVALID_SIGNATURE'


def test_master_all_attacks(nodes, simulator):
    """(4) Master test: run all three attack types totalling 800 attempts, assert total undetected across all is zero."""
    n1, n2 = nodes
    undetected = 0
    
    current_time = 2000000.0
    
    def mock_time():
        return current_time
        
    def mock_sleep(seconds):
        nonlocal current_time
        current_time += seconds
        
    with patch('time.time', side_effect=mock_time), patch('time.sleep', side_effect=mock_sleep):
        for _ in range(500):
            res = simulator.replay_attack_outside_window(n1, n2, delay=10.0)
            if not res.detected: undetected += 1
            
        for _ in range(200):
            res = simulator.replay_attack_within_window(n1, n2)
            if not res.detected: undetected += 1
            
        for _ in range(100):
            res = simulator.spoof_attack(n1, n2)
            if not res.detected: undetected += 1
            
    assert undetected == 0


def test_anomaly_logger_threshold():
    """(5) AnomalyLogger: 4 failures is False, 5th is True."""
    node_mock = MagicMock()
    node_mock.state = "ACTIVE"
    logger = AnomalyLogger(node_mock, threshold=5, window_seconds=30)
    
    # Add 4 failures
    for _ in range(4):
        logger.record_failure("test_failure")
        assert logger.check_anomaly() is False
        
    # 5th failure triggers anomaly
    logger.record_failure("test_failure")
    assert logger.check_anomaly() is True


def test_anomaly_logger_window_expiry():
    """(6) 5 failures then wait past window then one more — check_anomaly is False."""
    node_mock = MagicMock()
    node_mock.state = "ACTIVE"
    logger = AnomalyLogger(node_mock, threshold=5, window_seconds=30)
    
    current_time = 3000000.0
    
    def mock_time():
        return current_time
    
    with patch('time.time', side_effect=mock_time):
        # 5 failures initially - hits anomaly threshold
        for _ in range(5):
            logger.record_failure("test_failure")
            
        assert logger.check_anomaly() is True
        
        # Fast forward past the 30-second window
        current_time += 31.0
        
        # One more failure - now the previous 5 are expired (elapsed > 30s)
        # So only 1 valid failure exists in the time window
        logger.record_failure("test_failure")
        assert logger.check_anomaly() is False


def test_attack_duration_positive(nodes, simulator):
    """(7) All attack methods return AttackResult with duration_ms greater than zero."""
    n1, n2 = nodes
    
    res1 = simulator.replay_attack_outside_window(n1, n2, delay=0.01)
    res2 = simulator.replay_attack_within_window(n1, n2)
    res3 = simulator.spoof_attack(n1, n2)
    
    # For jamming test to not interfere with other variables
    n2.jamming_active = False 
    n2.last_seen = {}
    res4 = simulator.jamming_simulation(n1, n2, duration=0.01)
    
    assert res1.duration_ms > 0.0
    assert res2.duration_ms > 0.0
    assert res3.duration_ms > 0.0
    assert res4.duration_ms > 0.0


def test_quarantine_ring_mesh():
    """(8) Quarantine a node in a ring mesh, authenticate between non-adjacent nodes — succeeds.
    We test this by computing a trust path in a 4-node ring mesh after one node is quarantined.
    """
    mesh = TrustGraph()
    n1_id = 'node1'
    n2_id = 'node2'
    n3_id = 'node3'
    n4_id = 'node4'
    
    mesh.add_node(n1_id, b'pub1')
    mesh.add_node(n2_id, b'pub2')
    mesh.add_node(n3_id, b'pub3')
    mesh.add_node(n4_id, b'pub4')
    
    # Ring topology: 1-2, 2-3, 3-4, 4-1 (bidirectional weights)
    mesh.update_edge(n1_id, n2_id, 1.0)
    mesh.update_edge(n2_id, n1_id, 1.0)
    
    mesh.update_edge(n2_id, n3_id, 1.0)
    mesh.update_edge(n3_id, n2_id, 1.0)
    
    mesh.update_edge(n3_id, n4_id, 1.0)
    mesh.update_edge(n4_id, n3_id, 1.0)
    
    mesh.update_edge(n4_id, n1_id, 1.0)
    mesh.update_edge(n1_id, n4_id, 1.0)
    
    # Quarantine n2
    mesh.quarantine_node(n2_id)
    
    # The direct path via n2 is dead, but n1 and n3 can still "authenticate" and route
    # securely through the alternative path via n4.
    path = mesh.compute_trust_path(n1_id, n3_id)
    
    # Path should successfully route through n4
    assert path == [n1_id, n4_id, n3_id]
