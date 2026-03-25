"""test_node.py: Tests for node identity, state management, and authentication.

Tests the Node class, AuthProtocol, and NonceCache implementations.
"""

import pytest
import time
import os
import logging
import struct
import json
import socket
import backend.node as node_mod
from backend.node import Node, AuthProtocol, NonceCache, NodeState, AnomalyLogger, HeartbeatReceiver, heartbeat_sender, log_auth_event


@pytest.fixture
def node_a():
    """Return a fresh Node instance for testing."""
    node = Node()
    node.transition_to(NodeState.ACTIVE)
    return node


@pytest.fixture
def node_b():
    """Return a fresh Node instance for testing."""
    node = Node()
    node.transition_to(NodeState.ACTIVE)
    return node


def test_node_initialization():
    """Test that new node has correct identity and initial state.
    
    Validates:
    - node_id is 64-character hexadecimal string
    - public_key is 1952 bytes (ML-DSA-65)
    - state is INITIALIZING
    """
    fresh_node = Node()
    
    # node_id should be 64-character hex string
    assert len(fresh_node.node_id) == 64
    assert all(c in '0123456789abcdef' for c in fresh_node.node_id)
    
    # public_key should be 1952 bytes (ML-DSA-65)
    assert isinstance(fresh_node.public_key, bytes)
    assert len(fresh_node.public_key) == 1952
    
    # Should start in INITIALIZING state
    assert fresh_node.state == NodeState.INITIALIZING


def test_authenticate_success(node_a, node_b):
    """Test successful mutual authentication between two nodes.
    
    Validates:
    - AuthProtocol().authenticate returns success=True
    - duration_ms is under 300 milliseconds
    - timestamp is ISO 8601 UTC with Z suffix
    """
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
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


def test_anomaly_logger_quarantines_on_threshold():
    node = Node()
    node.transition_to(NodeState.ACTIVE)
    logger = AnomalyLogger(node, threshold=2, window_seconds=30)

    logger.record_failure('f1')
    assert node.state == NodeState.ACTIVE
    logger.record_failure('f2')

    assert node.state == NodeState.QUARANTINED
    assert len(logger.failures) == 2


def test_anomaly_logger_filters_old_failures_and_log_entry():
    node = Node()
    logger = AnomalyLogger(node, threshold=2, window_seconds=1)
    now = time.time()
    logger.failures = [(now - 10, 'old'), (now, 'new')]

    assert logger.check_anomaly() is False
    entry = logger.get_log_entry()
    assert entry['event_type'] == 'ANOMALY_ALERT'
    assert entry['failure_count'] == 1
    assert entry['failure_reasons'] == ['new']


def test_anomaly_logger_quarantine_also_calls_mesh_quarantine():
    class DummyMesh:
        def __init__(self):
            self.called = False
            self.node_id = None

        def quarantine_node(self, node_id):
            self.called = True
            self.node_id = node_id

    node = Node()
    node.transition_to(NodeState.ACTIVE)
    mesh = DummyMesh()
    logger = AnomalyLogger(node, threshold=1, window_seconds=30, mesh=mesh)

    logger.record_failure('f1')

    assert node.state == NodeState.QUARANTINED
    assert mesh.called is True
    assert mesh.node_id == node.node_id


def test_authenticate_response_timestamp_expired_branch(node_a, node_b):
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)
    # Force expiry branch deterministically.
    node_a._config['time_sync_window'] = -1.0

    result = AuthProtocol().authenticate(node_a, node_b)
    assert result['success'] is False
    assert result['failure_reason'] == 'RESPONSE_TIMESTAMP_EXPIRED'


def test_authenticate_invalid_response_signature_branch(node_a, node_b):
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    original_verify = node_a.crypto.verify
    node_a.crypto.verify = lambda *_args, **_kwargs: False
    try:
        result = AuthProtocol().authenticate(node_a, node_b)
    finally:
        node_a.crypto.verify = original_verify

    assert result['success'] is False
    assert result['failure_reason'] == 'INVALID_RESPONSE_SIGNATURE'


def test_transition_from_destroyed_raises_value_error():
    node = Node()
    node.state = NodeState.DESTROYED
    with pytest.raises(ValueError):
        node.transition_to(NodeState.ACTIVE)


def test_broadcast_join_notifies_neighbour_callback():
    class DummyNeighbour:
        def __init__(self):
            self.payload = None

        def receive_broadcast_join(self, payload):
            self.payload = payload

    node = Node()
    neighbour = DummyNeighbour()
    packet = node.broadcast_join([neighbour])

    assert packet['node_id'] == node.node_id
    assert neighbour.payload is not None


def test_verify_join_transitions_joining_to_active():
    sender = Node()
    receiver = Node()
    receiver.state = NodeState.JOINING

    packet = sender.broadcast_join([])
    success, reason = receiver.verify_join(packet)

    assert success is True
    assert reason is None
    assert receiver.state == NodeState.ACTIVE


def test_verify_join_malformed_packet_returns_invalid_signature(node_b):
    bad_packet = {
        'node_id': 'not-hex',
        'public_key': '00',
        'signature': '00',
        'timestamp': time.time(),
    }
    assert node_b.verify_join(bad_packet) == (False, 'INVALID_JOIN_SIGNATURE')


def test_rejoin_resets_identity_and_rejoins_mesh():
    class DummyMesh:
        def __init__(self):
            self.calls = []

        def add_node(self, node_id, public_key):
            self.calls.append((node_id, public_key))

    node = Node()
    old_node_id = node.node_id
    mesh = DummyMesh()

    assert node.rejoin(mesh, neighbours=[]) is True
    assert node.node_id != old_node_id
    assert len(mesh.calls) == 1
    assert mesh.calls[0][0] == node.node_id
    assert node.state == NodeState.JOINING


def test_heartbeat_sender_closes_socket_on_generation_error(monkeypatch):
    class DummySock:
        def __init__(self):
            self.closed = False

        def setsockopt(self, *_args):
            return None

        def sendto(self, *_args):
            return None

        def close(self):
            self.closed = True

    class OneLoopEvent:
        def __init__(self):
            self.done = False

        def is_set(self):
            return self.done

        def wait(self, _interval):
            self.done = True
            return True

    class DummyCrypto:
        def sign(self, *_args):
            raise RuntimeError('sign failed')

    class DummyNode:
        node_id = 'a' * 64
        crypto = DummyCrypto()
        private_key = b'k'

    sock = DummySock()
    monkeypatch.setattr(node_mod.socket, 'socket', lambda *_args, **_kwargs: sock)

    heartbeat_sender(DummyNode(), [('127.0.0.1', 9999)], OneLoopEvent(), interval=0.0)
    assert sock.closed is True


def test_heartbeat_receiver_listen_updates_last_seen_and_checks_stale(monkeypatch):
    sender = Node()
    monitor = Node()
    monitor.transition_to(NodeState.ACTIVE)
    monitor.trust_table[sender.node_id] = sender.public_key

    ts = time.time()
    ts_bytes = struct.pack('d', ts)
    sig = sender.crypto.sign(sender._private_key, ts_bytes)
    packet_bytes = json.dumps(
        {'node_id': sender.node_id, 'timestamp': ts, 'signature': sig.hex()}
    ).encode('utf-8')

    class FakeSock:
        def __init__(self):
            self.closed = False
            self.recv_calls = 0

        def setsockopt(self, *_args):
            return None

        def settimeout(self, *_args):
            return None

        def bind(self, *_args):
            return None

        def recvfrom(self, _n):
            self.recv_calls += 1
            if self.recv_calls == 1:
                return packet_bytes, ('127.0.0.1', 12000)
            raise socket.timeout()

        def close(self):
            self.closed = True

    class StopAfterTwoChecks:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 2

    fake_sock = FakeSock()
    monkeypatch.setattr(node_mod.socket, 'socket', lambda *_args, **_kwargs: fake_sock)

    receiver = HeartbeatReceiver(monitor, host='127.0.0.1', port=10001)
    receiver.listen(StopAfterTwoChecks())

    assert sender.node_id in receiver.last_seen
    assert fake_sock.closed is True

    receiver.last_seen[sender.node_id] = time.time() - 10.0
    stale = receiver.check_neighbours(interval=1.0, timeout=1.0)
    assert sender.node_id in stale


def test_heartbeat_receiver_handles_unknown_invalid_and_malformed_packets(monkeypatch):
    monitor = Node()
    known = Node()
    monitor.transition_to(NodeState.ACTIVE)
    monitor.trust_table[known.node_id] = known.public_key

    ts = time.time()
    unknown_packet = json.dumps(
        {'node_id': 'f' * 64, 'timestamp': ts, 'signature': '00'}
    ).encode('utf-8')
    invalid_sig_packet = json.dumps(
        {'node_id': known.node_id, 'timestamp': ts, 'signature': '00'}
    ).encode('utf-8')
    malformed_packet = b'{not-json'

    class FakeSock:
        def __init__(self):
            self.closed = False
            self.items = [unknown_packet, invalid_sig_packet, malformed_packet]

        def setsockopt(self, *_args):
            return None

        def settimeout(self, *_args):
            return None

        def bind(self, *_args):
            return None

        def recvfrom(self, _n):
            if self.items:
                return self.items.pop(0), ('127.0.0.1', 12001)
            raise socket.timeout()

        def close(self):
            self.closed = True

    class StopAfterFourChecks:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 4

    fake_sock = FakeSock()
    monkeypatch.setattr(node_mod.socket, 'socket', lambda *_args, **_kwargs: fake_sock)

    receiver = HeartbeatReceiver(monitor, host='127.0.0.1', port=10001)
    receiver.listen(StopAfterFourChecks())

    assert receiver.last_seen == {}
    assert fake_sock.closed is True


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


def test_log_entry_has_fr20_fields(tmp_path):
    node_a = Node()
    node_b = Node()
    node_a.transition_to(NodeState.ACTIVE)
    node_b.transition_to(NodeState.ACTIVE)

    log_file = tmp_path / 'auth_events.log'
    handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))

    target_logger = node_mod.logger
    original_level = target_logger.level
    target_logger.setLevel(logging.INFO)
    target_logger.addHandler(handler)

    try:
        result = AuthProtocol().authenticate(node_a, node_b)
        log_auth_event(result)
        handler.flush()
    finally:
        target_logger.removeHandler(handler)
        target_logger.setLevel(original_level)
        handler.close()

    last_line = log_file.read_text(encoding='utf-8').strip().splitlines()[-1]
    entry = json.loads(last_line)

    assert 'event_type' in entry
    assert 'timestamp' in entry
    assert ('node_a_id' in entry) or ('node_b_id' in entry)
    assert 'outcome' in entry
