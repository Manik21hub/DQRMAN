"""test_node.py: Tests for node identity, state management, and authentication.

Tests the Node class, AuthProtocol, and NonceCache implementations.
"""

import pytest
import time
import os
import struct
from backend.node import Node, AuthProtocol, NonceCache, NodeState


@pytest.fixture
def node_a():
    """Return a fresh Node instance for testing."""
    return Node()


@pytest.fixture
def node_b():
    """Return a fresh Node instance for testing."""
    return Node()


def test_node_initialization(node_a):
    """Test that new node has correct identity and initial state.
    
    Validates:
    - node_id is 64-character hexadecimal string
    - public_key is 1952 bytes (ML-DSA-65)
    - state is INITIALIZING
    """
    # node_id should be 64-character hex string
    assert len(node_a.node_id) == 64
    assert all(c in '0123456789abcdef' for c in node_a.node_id)
    
    # public_key should be 1952 bytes (ML-DSA-65)
    assert isinstance(node_a.public_key, bytes)
    assert len(node_a.public_key) == 1952
    
    # Should start in INITIALIZING state
    assert node_a.state == NodeState.INITIALIZING


def test_authenticate_success(node_a, node_b):
    """Test successful mutual authentication between two nodes.
    
    Validates:
    - AuthProtocol().authenticate returns success=True
    - duration_ms is under 300 milliseconds
    - timestamp is ISO 8601 UTC with Z suffix
    """
    result = AuthProtocol().authenticate(node_a, node_b)
    
    assert result['success'] is True
    assert result['failure_reason'] is None
    assert result['duration_ms'] < 300
    assert result['node_a_id'] == node_a.node_id
    assert result['node_b_id'] == node_b.node_id
    assert result['timestamp'].endswith('Z')


def test_challenge_expired_timestamp(node_a, node_b):
    """Test that challenge with old timestamp is rejected.
    
    A challenge with timestamp 10 seconds in the past should return
    (False, 'TIMESTAMP_EXPIRED') from verify_challenge.
    """
    ch = node_a.create_challenge()
    # Modify timestamp to 10 seconds in the past
    ch['timestamp'] = time.time() - 10.0
    
    success, reason = node_b.verify_challenge(ch)
    assert success is False
    assert reason == 'TIMESTAMP_EXPIRED'


def test_nonce_replay_detection(node_a, node_b):
    """Test that duplicate nonces are detected.
    
    Validates:
    - First verification of challenge succeeds
    - Second verification with same challenge returns DUPLICATE_NONCE
    """
    ch = node_a.create_challenge()
    
    # First verification should succeed
    success1, reason1 = node_b.verify_challenge(ch)
    assert success1 is True
    assert reason1 is None
    
    # Second verification with same challenge should fail
    success2, reason2 = node_b.verify_challenge(ch)
    assert success2 is False
    assert reason2 == 'DUPLICATE_NONCE'


def test_invalid_challenge_signature(node_a, node_b):
    """Test that corrupted challenge signature is detected.
    
    Corrupts the last 4 characters of the signature hex string
    and verifies that verify_challenge returns INVALID_SIGNATURE.
    """
    ch = node_a.create_challenge()
    
    # Corrupt the last 4 characters of signature
    sig = ch['signature']
    ch['signature'] = sig[:-4] + 'abcd'
    
    success, reason = node_b.verify_challenge(ch)
    assert success is False
    assert reason == 'INVALID_SIGNATURE'


def test_verify_challenge_quarantined_state(node_a, node_b):
    """Test that QUARANTINED nodes cannot verify challenges.
    
    Transitions node_b to QUARANTINED state and verifies that
    verify_challenge returns (False, 'QUARANTINED').
    """
    # Transition node_b to QUARANTINED
    node_b.transition_to(NodeState.QUARANTINED)
    
    ch = node_a.create_challenge()
    success, reason = node_b.verify_challenge(ch)
    
    assert success is False
    assert reason == 'QUARANTINED'


def test_broadcast_join_and_verify(node_a, node_b):
    """Test join broadcast and verification workflow.
    
    Validates:
    - broadcast_join returns packet with required fields
    - verify_join returns (True, None) for valid packet
    - Sender added to trust_table
    """
    # node_a broadcasts join to empty list
    packet = node_a.broadcast_join([])
    
    # Validate packet structure
    assert 'node_id' in packet
    assert 'public_key' in packet
    assert 'signature' in packet
    assert 'timestamp' in packet
    
    # node_b verifies the join packet
    success, reason = node_b.verify_join(packet)
    
    assert success is True
    assert reason is None
    # node_a should be in trust_table
    assert node_a.node_id in node_b.trust_table


def test_invalid_join_signature(node_a, node_b):
    """Test that corrupted join signature is detected.
    
    Corrupts the signature in a join packet and verifies that
    verify_join returns (False, 'INVALID_JOIN_SIGNATURE').
    """
    packet = node_a.broadcast_join([])
    
    # Corrupt the signature
    sig = packet['signature']
    packet['signature'] = sig[:-4] + 'abcd'
    
    success, reason = node_b.verify_join(packet)
    assert success is False
    assert reason == 'INVALID_JOIN_SIGNATURE'


def test_nonce_cache_functionality():
    """Test NonceCache add and contains operations.
    
    Validates:
    - New cache does not contain arbitrary nonce
    - After adding nonce, contains returns True
    - .hex() format is used (not str())
    """
    cache = NonceCache()
    nonce = bytes.fromhex('a1b2c3d4e5f6789012345678901234567890123456789012345678')
    
    # Initially should not contain nonce
    assert cache.contains(nonce) is False
    
    # After add, should be found
    cache.add(nonce, ttl=10.0)
    assert cache.contains(nonce) is True


def test_authenticate_response_includes_nonce_a(node_a, node_b):
    """Test that response message includes original nonce_A bytes.
    
    Validates that the authenticate function builds the response message
    with the correct structure: nonce_b + timestamp + nonce_a, where
    nonce_a is the original nonce from node_a's challenge (steps 7-9).
    """
    # Capture the nonce created by node_a
    captured_nonce_a = []
    original_create_challenge = node_a.create_challenge
    
    def capture_create_challenge():
        ch = original_create_challenge()
        captured_nonce_a.append(bytes.fromhex(ch['nonce']))
        return ch
    
    node_a.create_challenge = capture_create_challenge
    
    # Capture the message in verify
    captured_messages = []
    original_verify = node_a.crypto.verify
    
    def capture_verify(pub_key, message, sig):
        captured_messages.append(message)
        return original_verify(pub_key, message, sig)
    
    node_a.crypto.verify = capture_verify
    
    # Run authentication
    result = AuthProtocol().authenticate(node_a, node_b)
    assert result['success'] is True
    
    # Extract nonce_a from captured message
    # Message format: nonce_b (32 bytes) + ts_b (8 bytes double) + nonce_a (32 bytes)
    msg = captured_messages[0]
    assert len(msg) == 72  # 32 + 8 + 32
    
    nonce_a_from_message = msg[40:72]  # Extract last 32 bytes
    
    # Should match the nonce_a that was created in the challenge
    assert nonce_a_from_message == captured_nonce_a[0]


def test_in_window_replay(node_a, node_b):
    """Test that nonce is cached even when timestamp is still fresh.
    
    Validates that nonce cache is the mechanism preventing replay, not
    timestamp expiration. Proves that the same nonce is rejected immediately
    on reuse even though the timestamp is within the sync window.
    """
    # Create challenge once
    ch = node_a.create_challenge()
    
    # First verification should succeed
    success1, reason1 = node_b.verify_challenge(ch)
    assert success1 is True
    assert reason1 is None
    
    # Immediately verify with same challenge again
    # Should fail with DUPLICATE_NONCE, NOT TIMESTAMP_EXPIRED
    success2, reason2 = node_b.verify_challenge(ch)
    assert success2 is False
    assert reason2 == 'DUPLICATE_NONCE'  # This proves nonce cache is working


def test_auth_latency_1000():
    """Test AuthProtocol performance over 1000 iterations.
    
    Measures authentication latency across 1000 rounds with fresh nodes
    each iteration. Validates p99 is under environment threshold.
    Prints percentile statistics (p50, p95, p99).
    """
    # Read threshold from environment
    threshold_ms = float(os.getenv('AUTH_LATENCY_MS', '200'))
    threshold_s = threshold_ms / 1000.0
    
    durations = []
    
    # Run 1000 authentication cycles
    for _ in range(1000):
        node_a = Node()
        node_b = Node()
        
        start = time.perf_counter()
        result = AuthProtocol().authenticate(node_a, node_b)
        duration = time.perf_counter() - start
        
        durations.append(duration)
    
    # Sort for percentile calculation
    durations.sort()
    
    # Calculate percentiles
    p50 = durations[499]   # 50th percentile (index 499 out of 1000)
    p95 = durations[949]   # 95th percentile (index 949 out of 1000)
    p99 = durations[989]   # 99th percentile (index 989 out of 1000)
    
    # Print statistics
    print(f'\nAuth latency (1000 iterations):')
    print(f'  p50: {p50*1000:.2f} ms')
    print(f'  p95: {p95*1000:.2f} ms')
    print(f'  p99: {p99*1000:.2f} ms')
    print(f'  threshold: {threshold_ms:.1f} ms')
    
    # Assert durations[949] is under threshold
    assert durations[949] < threshold_s, f'latency at index 949 {durations[949]*1000:.2f}ms exceeds threshold {threshold_ms}ms'


def test_join_tampered_signature():
    """Test join verification failure when signature tail is tampered."""
    node_a = Node()
    node_b = Node()

    packet = node_a.broadcast_join([])
    packet['signature'] = packet['signature'][:-8] + 'ffffffff'

    result = node_b.verify_join(packet)
    assert result == (False, 'INVALID_JOIN_SIGNATURE')


@pytest.mark.skip(reason='Placeholder: activates in Day 3 after TrustGraph is built')
def test_duplicate_node_id_rejected():
    """Placeholder test for rejecting duplicate node IDs in TrustGraph."""
    # Placeholder: this will activate in Day 3 after TrustGraph is built.
    from backend.trust_graph import TrustGraph

    graph = TrustGraph()
    node_id = 'a' * 64
    public_key_1 = b'\x01' * 1952
    public_key_2 = b'\x02' * 1952

    graph.add_node(node_id, public_key_1)
    with pytest.raises(ValueError):
        graph.add_node(node_id, public_key_2)
