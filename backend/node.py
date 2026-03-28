"""node.py: Node identity and state management for distributed mesh network.

This module provides core data structures for managing node identity and lifecycle
state within the DQRMAN distributed network.

Requirements:
    FR-06: Node identity stores cryptographic public key material
    FR-07: Node state machine tracks lifecycle from initialization to destruction
"""

import dataclasses
import enum
import time
import datetime
import threading
import struct
import socket
import json
import collections
import logging

# Module-level logger
logger = logging.getLogger(__name__)


def _config_value(config, key, default=None, section=None):
    """Read config value from flat key or optional nested section."""
    if not isinstance(config, dict):
        return default
    if key in config:
        return config.get(key, default)
    if section and isinstance(config.get(section), dict):
        return config[section].get(key, default)
    return default


def _build_auth_message(nonce, timestamp, extra=b''):
    """Build an authentication message from nonce, timestamp, and optional extra data.
    
    Combines cryptographic components in a standardized format:
    message = nonce + struct.pack('d', timestamp) + extra
    
    Args:
        nonce: Bytes object representing the challenge nonce.
        timestamp: Float representing the message timestamp (packed as C double).
        extra: Optional bytes to append after timestamp. Defaults to empty bytes.
               For challenge creation, extra is empty. For step 8 responses,
               extra must be nonce_A bytes to bind the response to the specific
               challenge it is answering.
               
    Returns:
        Bytes: Concatenated message (nonce + packed_timestamp + extra)
    """
    return nonce + struct.pack('d', timestamp) + extra


def log_auth_event(result):
    """Log a mutual authentication event in structured JSON format."""
    log_entry = {
        'event_type': 'MUTUAL_AUTH',
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'node_a_id': result.get('node_a_id'),
        'node_b_id': result.get('node_b_id'),
        'outcome': 'SUCCESS' if result.get('success') is True else 'FAILURE',
        'failure_reason': result.get('failure_reason') or None,
        'duration_ms': round(float(result.get('duration_ms', 0.0)), 2),
    }
    logger.info(json.dumps(log_entry))


@dataclasses.dataclass
class NodeIdentity:
    """Represents the cryptographic identity of a network node.
    
    Attributes:
        node_id: Unique 64-character hexadecimal identifier derived from public key
        public_key: 1952-byte ML-DSA-65 public key material
        created_at: Unix timestamp of node identity creation
    """
    node_id: str
    public_key: bytes
    created_at: float


class NodeState(enum.Enum):
    """Enumeration of node lifecycle states in the mesh network.
    
    States are ordered by typical progression: initialization → cluster membership →
    active operation → potential degradation → quarantine → termination.
    """
    UNVERIFIED = enum.auto()
    JOINING = enum.auto()
    ACTIVE = enum.auto()
    HEALING = enum.auto()
    ISOLATED = enum.auto()
    QUARANTINED = enum.auto()
    DESTROYED = enum.auto()


class NonceCache:
    """Cache of nonces with TTL-based expiration to prevent replay attacks.
    
    Nonces are stored using their hex representation (.hex()) rather than str()
    to ensure proper comparison and prevent bypass vulnerabilities. This is
    CRITICAL: str(b'\\xab') produces the string 'b\\xab' whereas b'\\xab'.hex()
    produces 'ab'. Using str() would allow attackers to craft nonces that bypass
    the cache check by exploiting this representation mismatch.
    """
    
    def __init__(self):
        """Initialize an empty nonce cache.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self._cache = {}

    def clear_expired(self):
        """Remove expired nonce entries from the cache.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        now = time.time()
        # Keep only entries that expire in the future.
        self._cache = {k: v for k, v in self._cache.items() if v > now}
    
    def contains(self, nonce):
        """Check whether a nonce exists and is not expired.
        
        Evicts expired entries before checking membership.
        
        Args:
            nonce: Bytes object representing the nonce.
            
        Returns:
            bool: True if nonce exists and has not expired, False otherwise.

        Raises:
            None.
        """
        self.clear_expired()
        return nonce.hex() in self._cache
    
    def add(self, nonce, ttl):
        """Add a nonce with a time-to-live value.
        
        Args:
            nonce: Bytes object representing the nonce.
            ttl: Time-to-live in seconds. Expiry is set to now + ttl.

        Returns:
            None.

        Raises:
            None.
        """
        self._cache[nonce.hex()] = time.time() + ttl


class AnomalyLogger:
    """Tracks recent authentication failures and quarantines anomalous nodes."""

    def __init__(self, node, threshold=None, window_seconds=None, mesh=None):
        """Initialize anomaly tracking state for a monitored node.

        Args:
            node: Node instance being monitored.
            threshold: Number of recent failures required to trigger anomaly.
            window_seconds: Sliding time window used for anomaly detection.
            mesh: Optional mesh reference used for topology-level quarantine.

        Returns:
            None.

        Raises:
            None.
        """
        self.node = node
        cfg = getattr(node, '_config', {})
        if threshold is None:
            threshold = _config_value(cfg, 'anomaly_threshold', 5, section='security')
        if window_seconds is None:
            window_seconds = _config_value(cfg, 'anomaly_window', 30, section='security')
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.mesh = mesh
        self.failures = []

    def record_failure(self, reason):
        """Record a failure and quarantine the node when anomaly criteria are met.

        Args:
            reason: Failure reason string to record.

        Returns:
            None.

        Raises:
            ValueError: Propagated if node.transition_to rejects the transition.
        """
        self.failures.append((time.time(), reason))
        if self.check_anomaly() and self.node.state not in (
            NodeState.QUARANTINED,
            NodeState.DESTROYED,
        ):
            self.node.transition_to(NodeState.QUARANTINED)
            if self.mesh is not None:
                self.mesh.quarantine_node(self.node.node_id)

    def check_anomaly(self):
        """Evaluate whether recent failures meet or exceed anomaly threshold.

        Filters self.failures by time window: only entries where the elapsed
        time since the failure is within self.window_seconds are considered.
        Does NOT mutate self.failures — use clear_expired for pruning.

        Args:
            None.

        Returns:
            bool: True when recent failure count is at least threshold.

        Raises:
            None.
        """
        now = time.time()
        recent = [(ts, r) for ts, r in self.failures if now - ts <= self.window_seconds]
        return len(recent) >= self.threshold

    def get_log_entry(self):
        """Build structured anomaly alert payload for logging.

        Args:
            None.

        Returns:
            dict: Alert payload with event type, node, failure counts, reasons, and timestamps.

        Raises:
            None.
        """
        now = time.time()
        recent = [(ts, r) for ts, r in self.failures if now - ts <= self.window_seconds]
        return {
            'event_type': 'ANOMALY_ALERT',
            'node_id': self.node.node_id,
            'failure_count': len(recent),
            'failure_reasons': [reason for _, reason in recent],
            'timestamps': [ts for ts, _ in recent],
        }


class AuthProtocol:
    """Authentication protocol for peer-to-peer node verification.
    
    Implements mutual authentication challenge-response mechanism with
    nonce-based replay protection and timestamp validation.
    """
    
    def authenticate(self, node_a, node_b):
        """Authenticate two peers per SRS Section 8.2 challenge-response flow.
        
        Steps 1-3: node_a creates and sends challenge.
        Steps 4-6: node_b verifies challenge and accepts/rejects.
        Steps 7-9: node_b generates response containing nonce commitment.
        Steps 10-11: node_a verifies response signature and freshness.
        Step 12: Return authentication result with timing and node IDs.
        
        Args:
            node_a: First node (challenge creator).
            node_b: Second node (challenge responder).
            
        Returns:
            Dictionary with keys:
                - success (bool): Whether authentication succeeded
                - failure_reason (str or None): Reason if failed
                - duration_ms (float): Elapsed time in milliseconds
                - timestamp (str): ISO 8601 UTC timestamp with Z
                - node_a_id (str): node_a.node_id
                - node_b_id (str): node_b.node_id

        Raises:
            None.
        """
        start_time = time.perf_counter()
        
        # Steps 1-3: Create challenge
        ch = node_a.create_challenge()
        
        # Steps 4-6: Verify challenge
        success, failure_reason = node_b.verify_challenge(ch)
        if not success:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result = {
                'success': False,
                'failure_reason': failure_reason,
                'duration_ms': elapsed_ms,
                'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                'node_a_id': node_a.node_id,
                'node_b_id': node_b.node_id
            }
            log_auth_event(result)
            return result
        
        # Steps 7-9: Generate response
        nonce_b = node_b.crypto.generate_nonce()
        ts_b = time.time()
        nonce_a_bytes = bytes.fromhex(ch['nonce'])
        
        # CRITICAL: message structure is nonce_b + timestamp_packed + nonce_a
        response_message = _build_auth_message(nonce_b, ts_b, nonce_a_bytes)
        response_signature = node_b.crypto.sign(node_b._private_key, response_message)
        
        # Steps 10-11: Verify response
        time_sync_window = _config_value(node_a._config, 'time_sync_window', 5.0, section='network')
        if abs(time.time() - ts_b) > time_sync_window:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result = {
                'success': False,
                'failure_reason': 'RESPONSE_TIMESTAMP_EXPIRED',
                'duration_ms': elapsed_ms,
                'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                'node_a_id': node_a.node_id,
                'node_b_id': node_b.node_id
            }
            log_auth_event(result)
            return result
        
        # Rebuild and verify response message
        verify_message = _build_auth_message(nonce_b, ts_b, nonce_a_bytes)
        if not node_a.crypto.verify(node_b.public_key, verify_message, response_signature):
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result = {
                'success': False,
                'failure_reason': 'INVALID_RESPONSE_SIGNATURE',
                'duration_ms': elapsed_ms,
                'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                'node_a_id': node_a.node_id,
                'node_b_id': node_b.node_id
            }
            log_auth_event(result)
            return result
        
        # Step 12: Return success result
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        result = {
            'success': True,
            'failure_reason': None,
            'duration_ms': elapsed_ms,
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
            'node_a_id': node_a.node_id,
            'node_b_id': node_b.node_id
        }
        log_auth_event(result)
        return result

    def update_mesh_trust(self, auth_result, mesh):
        """Update mesh trust scores based on authentication result.

        Refreshes trust scores in the mesh graph based on successful or failed
        authentication between two nodes. This enables the mesh to prefer routing
        through nodes that consistently authenticate successfully.

        Args:
            auth_result: Dictionary from authenticate() with 'success', 'node_a_id', 'node_b_id'.
            mesh: TrustGraph instance to update (optional, returns early if None).

        Returns:
            None.

        Raises:
            None.
        """
        if mesh is None:
            return

        try:
            success = auth_result.get('success', False)
            node_a_id = auth_result.get('node_a_id')
            node_b_id = auth_result.get('node_b_id')

            if not all([node_a_id, node_b_id]):
                return

            # Update trust in both directions (mutual authentication)
            mesh.refresh_trust_score(node_a_id, node_b_id, auth_success=success)
            mesh.refresh_trust_score(node_b_id, node_a_id, auth_success=success)

            logger.info(
                f'Mesh trust updated: {node_a_id[:8]} ↔ {node_b_id[:8]} '
                f'{"✓" if success else "✗"}'
            )

        except Exception as e:
            logger.error(f'Error updating mesh trust: {e}')


class Node:
    """Represents a node in the DQRMAN distributed mesh network.
    
    Manages node identity, cryptographic keys, state, and trust relationships with
    peer nodes in the network.
    
    Attributes:
        node_id: Unique identifier derived from public key
        public_key: ML-DSA-65 public key for cryptographic verification
        state: Current lifecycle state in the mesh
        trust_table: Dictionary mapping peer node_ids to their public keys
    """
    
    def __init__(self, config=None):
        """Initialize node cryptographic identity and state for FR-01 bootstrap.
        
        Args:
            config: Optional configuration dictionary containing 'dilithium_variant'
                   and 'port' (default 9000).
                   Defaults to 'ML-DSA-65' if not provided.
                   
        Raises:
            CryptoError: If cryptographic module initialization fails.
        """
        from backend.crypto import CryptoModule
        from backend.protocol import NodeTCPServer
        
        if config is None:
            config = {}
        
        algorithm = _config_value(config, 'dilithium_variant', 'ML-DSA-65', section='crypto')
        crypto = CryptoModule(algorithm=algorithm)
        
        self.public_key, self._private_key = crypto.generate_keypair()
        self.node_id = crypto.derive_node_id(self.public_key)
        self.state = NodeState.UNVERIFIED
        self.lat = float(_config_value(config, 'lat', 0.0, section='location'))
        self.lon = float(_config_value(config, 'lon', 0.0, section='location'))
        self.trust_table = {}
        self._lock = threading.Lock()
        self._algorithm = algorithm
        self._config = config
        self.crypto = crypto
        self.nonce_cache = NonceCache()
        
        # Initialize TCP server
        self.port = _config_value(config, 'port', 9000, section='server')
        self.tcp_server = NodeTCPServer('0.0.0.0', self.port)
        self.tcp_server.start()
        
        logger.info('TCP server started on port %d', self.port)
    
    def transition_to(self, new_state):
        """Transition the node to a new lifecycle state.
        
        Args:
            new_state: Target NodeState value.
            
        Raises:
            ValueError: If current state is DESTROYED (terminal state).
        """
        with self._lock:
            if self.state == NodeState.DESTROYED:
                raise ValueError('DESTROYED is a terminal state with no exit')
            
            old_state = self.state
            logger.info(
                f'Node {self.node_id[:8]} transitioning from {old_state.name} to {new_state.name}'
            )
            self.state = new_state
    
    def broadcast_join(self, neighbours):
        """Broadcast a signed join packet to satisfy FR-02 neighbour discovery.
        
        Builds a cryptographically signed join packet containing this node's
        identity and public key, sends it as JSON to all neighbours, and 
        transitions to JOINING state.
        
        Args:
            neighbours: List of neighbour nodes to send join packet to.
            
        Returns:
            The join packet dictionary containing node_id, public_key, signature,
            and timestamp.

        Raises:
            CryptoError: If signature generation fails.
        """
        from backend.crypto import CryptoModule
        
        # Build message: concatenate node_id and public_key as bytes
        node_id_bytes = bytes.fromhex(self.node_id)
        message = node_id_bytes + self.public_key
        
        # Sign the message
        crypto = CryptoModule(algorithm=self._algorithm)
        signature = crypto.sign(self._private_key, message)
        
        # Create packet
        packet = {
            'node_id': self.node_id,
            'public_key': self.public_key.hex(),
            'signature': signature.hex(),
            'timestamp': time.time()
        }
        
        # Send JSON packet to each neighbour
        packet_json = json.dumps(packet)
        for neighbour in neighbours:
            if hasattr(neighbour, 'receive_broadcast_join'):
                neighbour.receive_broadcast_join(packet_json)
        
        # Transition to JOINING state
        self.transition_to(NodeState.JOINING)
        
        return packet
    
    def verify_join(self, packet):
        """Verify a received join packet and update trust state.
        
        Validates the cryptographic signature of a join packet. If valid,
        adds the sender to the trust table and transitions to ACTIVE if
        currently in JOINING state.
        
        Args:
            packet: Join packet dictionary with keys: node_id, public_key,
                   signature, timestamp.
                   
        Returns:
            Tuple of (success: bool, error: str or None).
            If valid: (True, None)
            If invalid: (False, 'INVALID_JOIN_SIGNATURE')

        Raises:
            None.
        """
        from backend.crypto import CryptoModule
        
        try:
            # Reconstruct message from packet
            node_id_bytes = bytes.fromhex(packet['node_id'])
            public_key_bytes = bytes.fromhex(packet['public_key'])
            signature_bytes = bytes.fromhex(packet['signature'])
            
            message = node_id_bytes + public_key_bytes
            
            # Verify signature
            crypto = CryptoModule(algorithm=self._algorithm)
            if not crypto.verify(public_key_bytes, message, signature_bytes):
                return (False, 'INVALID_JOIN_SIGNATURE')
            
            # Signature is valid - add to trust table
            with self._lock:
                self.trust_table[packet['node_id']] = public_key_bytes
                
                # Transition to ACTIVE if in JOINING state
                if self.state == NodeState.JOINING:
                    self.state = NodeState.ACTIVE
            
            return (True, None)
        except Exception:
            return (False, 'INVALID_JOIN_SIGNATURE')
    
    def create_challenge(self):
        """Create a signed authentication challenge for peer verification.
        
        Implements SRS Section 8.2 Steps 1-3: generates a fresh nonce,
        timestamps it, and signs the combined message.
        
        Returns:
            Dictionary with node_id, public_key, nonce, timestamp, and signature.

        Raises:
            CryptoError: If challenge signing fails.
        """
        # Step 1-2: Generate fresh nonce and timestamp
        nonce = self.crypto.generate_nonce()
        timestamp = time.time()
        
        # Step 3: Build message and sign
        message = _build_auth_message(nonce, timestamp)
        signature = self.crypto.sign(self._private_key, message)
        
        return {
            'node_id': self.node_id,
            'public_key': self.public_key.hex(),
            'nonce': nonce.hex(),
            'timestamp': timestamp,
            'signature': signature.hex()
        }
    
    def verify_challenge(self, challenge):
        """Verify challenge freshness and signature for FR-04 FR-05 FR-06.
        
        Implements SRS Section 8.2 Steps 4-6: validates timestamp freshness,
        checks for nonce replay, and verifies cryptographic signature.
        
        Args:
            challenge: Dictionary with node_id, public_key, nonce, timestamp, signature.
            
        Returns:
            Tuple of (success: bool, error: str or None).
            If valid: (True, None)
            If invalid: (False, error_reason)

        Raises:
            None.
        """
        # Step 1: Check state
        if self.state in (NodeState.QUARANTINED, NodeState.DESTROYED, NodeState.UNVERIFIED):
            return (False, self.state.name)
        
        # Step 2: Check timestamp freshness
        now = time.time()
        time_sync_window = _config_value(self._config, 'time_sync_window', 5.0, section='network')
        if abs(now - challenge['timestamp']) > time_sync_window:
            return (False, 'TIMESTAMP_EXPIRED')
        
        # Step 3: Check for nonce replay
        nonce_bytes = bytes.fromhex(challenge['nonce'])
        if self.nonce_cache.contains(nonce_bytes):
            return (False, 'DUPLICATE_NONCE')
        
        # Step 4: Verify signature
        message = nonce_bytes + struct.pack('d', challenge['timestamp'])
        public_key_bytes = bytes.fromhex(challenge['public_key'])
        signature_bytes = bytes.fromhex(challenge['signature'])
        
        if not self.crypto.verify(public_key_bytes, message, signature_bytes):
            return (False, 'INVALID_SIGNATURE')
        
        # Step 4b: KEY BINDING VALIDATION - Prevent key substitution attacks per F-02
        # Requirement: Node B checks that the public key matches the expected Node ID.
        # Derive node_id from provided public_key and verify it matches the claimed node_id.
        derived_node_id = self.crypto.derive_node_id(public_key_bytes)
        if derived_node_id != challenge['node_id']:
            return (False, 'KEY_BINDING_MISMATCH')
        
        # Step 5: Add nonce to cache
        self.nonce_cache.add(nonce_bytes, time_sync_window)
        
        return (True, None)

    def rejoin(self, mesh, neighbours):
        """Rejoin the mesh after a crash for NFR-13 recovery handling.

        Args:
            mesh: Mesh object supporting add_node(node_id, public_key).
            neighbours: List of neighbour nodes to receive join broadcast.

        Returns:
            bool: True when rejoin sequence completes.

        Raises:
            ValueError: If a terminal-state transition is attempted during rejoin.
        """
        self.public_key, self._private_key = self.crypto.generate_keypair()
        self.node_id = self.crypto.derive_node_id(self.public_key)
        self.nonce_cache = NonceCache()
        self.state = NodeState.UNVERIFIED

        mesh.add_node(self.node_id, self.public_key)
        self.broadcast_join(neighbours)
        self.transition_to(NodeState.JOINING)

        logger.info('Node %s rejoined after a crash', self.node_id[:8])
        return True

    def shutdown(self):
        """Gracefully shut down the node and its resources.

        Signals the heartbeat sender thread to stop, waits for it to join
        with a 2-second timeout, closes the TCP server if active, and
        transitions the node to DESTROYED state.

        This method is idempotent and safe to call even if some resources
        were never initialized.

        Returns:
            None.
        """
        # Signal heartbeat sender thread to stop
        if hasattr(self, '_heartbeat_stop'):
            self._heartbeat_stop.set()

        # Wait for heartbeat thread to terminate
        if hasattr(self, '_hb_thread'):
            self._hb_thread.join(timeout=2.0)

        # Close TCP server if it exists
        if hasattr(self, 'tcp_server'):
            if hasattr(self.tcp_server, 'server_close'):
                self.tcp_server.server_close()
            elif hasattr(self.tcp_server, 'stop'):
                self.tcp_server.stop()

        # Transition to terminal state
        self.transition_to(NodeState.DESTROYED)

        logger.info('Node %s shut down cleanly', self.node_id[:8])

    def send_to_peer(self, host, port, message_type, payload):
        """Send a signed message to a peer node via TCP.

        Constructs a wire-protocol frame containing the message type, node ID,
        payload, and cryptographic signature, then sends it to the specified
        host and port using TCP.

        Args:
            host: Target peer hostname or IP address.
            port: Target peer TCP port.
            message_type: Integer message type code (0x01-0x06).
            payload: Bytes to include in the message payload.

        Returns:
            None.

        Raises:
            None (exceptions are logged).
        """
        from backend.protocol import pack_frame, NodeTCPClient

        try:
            # Sign the payload with this node's private key
            signature = self.crypto.sign(self._private_key, payload)

            # Pack the frame with message type, sender node_id, payload, and signature
            frame = pack_frame(message_type, self.node_id, payload, signature)

            # Send frame via TCP client
            client = NodeTCPClient(host, port)
            client.send_frame(frame)
        except Exception as e:
            logger.warning(f'Failed to send message to {host}:{port}: {e}')


def heartbeat_sender(node, neighbours, stop_event, interval=None):
    """Send periodic heartbeat messages to mesh neighbours.

    Runs a background loop that sends signed heartbeat packets to all
    neighbours at regular intervals. Each heartbeat includes a timestamp
    and signature for peer verification. Designed to run in a separate
    thread with graceful shutdown via stop_event.

    Args:
        node: Node instance with crypto capability and private key.
        neighbours: List of (host, port) tuples for destination peers.
        stop_event: threading.Event for clean shutdown signaling.
        interval: Seconds between heartbeats. If None, reads from
              node config key network.heartbeat_interval with default 1.0.

    Returns:
        None.

    Raises:
        None.
    """
    if interval is None:
        interval = _config_value(getattr(node, '_config', {}), 'heartbeat_interval', 1.0, section='network')

    # Open UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        while not stop_event.is_set():
            try:
                # Get current timestamp
                timestamp = time.time()
                ts_bytes = struct.pack('d', timestamp)

                # Sign timestamp with node's private key
                signature = node.crypto.sign(node._private_key, ts_bytes)

                # Build JSON packet
                packet = {
                    'node_id': node.node_id,
                    'timestamp': timestamp,
                    'signature': signature.hex(),
                }
                packet_bytes = json.dumps(packet).encode('utf-8')

                # Send to each neighbour
                for host, port in neighbours:
                    try:
                        sock.sendto(packet_bytes, (host, port))
                    except Exception as e:
                        logger.warning(
                            f'Heartbeat send to {host}:{port} failed: {e}'
                        )

            except Exception as e:
                logger.error(f'Heartbeat generation error: {e}')

            # Wait for interval or stop_event
            stop_event.wait(interval)

    finally:
        sock.close()
        logger.info(f'Heartbeat sender for {node.node_id[:8]} stopped')


class HeartbeatReceiver:
    """Receive and verify signed heartbeat messages from mesh neighbours.

    Monitors UDP heartbeat packets sent by peer nodes, validates signatures,
    and tracks last-seen times for liveness detection. Supports jamming
    simulation for testing network robustness.
    """

    def __init__(self, monitoring_node, host='127.0.0.1', port=10001, mesh=None):
        """Initialize heartbeat receiver.

        Args:
            monitoring_node: Node instance with trust_table and crypto capability.
            host: Host address to bind UDP socket (default '127.0.0.1').
            port: Port number to bind UDP socket (default 10001).
            mesh: Optional TrustGraph instance for automatic self-heal when
                  neighbours become stale.

        Returns:
            None.

        Raises:
            None.
        """
        self.node = monitoring_node
        self.host = host
        self.port = port
        self.mesh = mesh
        self.socket = None
        self.last_seen = {}
        self.jamming_active = False

    def listen(self, stop_event):
        """Listen for heartbeat packets and verify signatures.

        Receives UDP packets containing signed heartbeats from peer nodes.
        Validates each heartbeat's signature against the peer's public key
        from the trust table. Updates last_seen timestamp for valid packets.

        Can be run in a separate thread. Graceful shutdown via stop_event.

        Args:
            stop_event: threading.Event for shutdown signaling.

        Returns:
            None.

        Raises:
            None.
        """
        # Bind UDP socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.settimeout(1.0)

        try:
            self.socket.bind((self.host, self.port))
            logger.info(f'Heartbeat receiver listening on {self.host}:{self.port}')
        except OSError as e:
            logger.error(f'Failed to bind heartbeat socket: {e}')
            return

        try:
            while not stop_event.is_set():
                try:
                    # Receive packet
                    data, addr = self.socket.recvfrom(4096)

                    # Skip if jamming is active
                    if self.jamming_active:
                        continue

                    try:
                        # Parse JSON packet
                        packet = json.loads(data.decode('utf-8'))
                        sender_id = packet.get('node_id')
                        timestamp = packet.get('timestamp')
                        signature_hex = packet.get('signature')

                        if not all([sender_id, timestamp is not None, signature_hex]):
                            continue

                        # Pack timestamp as bytes
                        ts_bytes = struct.pack('d', timestamp)
                        signature_bytes = bytes.fromhex(signature_hex)

                        # Get sender's public key from trust table
                        if sender_id not in self.node.trust_table:
                            logger.debug(f'Heartbeat from unknown node {sender_id[:8]}')
                            continue

                        public_key = self.node.trust_table[sender_id]

                        # Verify signature
                        if not self.node.crypto.verify(public_key, ts_bytes, signature_bytes):
                            logger.warning(f'Heartbeat signature invalid from {sender_id[:8]}')
                            continue

                        # Update last_seen
                        self.last_seen[sender_id] = time.time()
                        logger.debug(f'Heartbeat from {sender_id[:8]} verified')

                    except Exception as e:
                        logger.debug(f'Heartbeat parse error from {addr}: {e}')
                        continue

                except socket.timeout:
                    # On receive timeout, optionally run automatic stale-neighbour healing.
                    if self.mesh is not None:
                        hb_interval = _config_value(
                            getattr(self.node, '_config', {}),
                            'heartbeat_interval',
                            1.0,
                            section='network',
                        )
                        self.self_heal_stale_neighbours(self.mesh, interval=hb_interval)
                except Exception as e:
                    if not stop_event.is_set():
                        logger.error(f'Heartbeat receive error: {e}')

        finally:
            if self.socket:
                self.socket.close()
            logger.info(f'Heartbeat receiver stopped')

    def check_neighbours(self, interval, timeout=None):
        """Check for neighbours with stale heartbeats.

        Identifies peer nodes that have not sent heartbeats within the
        specified time window. Useful for detecting network failures or
        partitions.

        Args:
            interval: Seconds to treat as heartbeat interval.
            timeout: Multiplier for timeout. If None, reads from
                     node config key network.heartbeat_timeout with default 3.
                     Total timeout
                     is interval * timeout.

        Returns:
            list: Node IDs whose last_seen is older than interval * timeout.

        Raises:
            None.
        """
        if timeout is None:
            timeout = _config_value(getattr(self.node, '_config', {}), 'heartbeat_timeout', 3, section='network')

        now = time.time()
        threshold = interval * timeout
        stale = []

        for node_id, last_time in self.last_seen.items():
            if now - last_time > threshold:
                stale.append(node_id)

        return stale

    def self_heal_stale_neighbours(self, mesh, interval, timeout=None):
        """Auto-remove stale neighbours from mesh based on heartbeat timeout.

        Detects neighbours with stale heartbeats and triggers mesh self-healing
        by calling mesh.on_node_failure for each stale node. This provides
        automatic recovery without manual intervention.

        Args:
            mesh: TrustGraph-like object exposing on_node_failure(node_id).
            interval: Heartbeat interval in seconds.
            timeout: Timeout multiplier. If None, defaults to config value
                     network.heartbeat_timeout (default 3).

        Returns:
            list: Node IDs removed due to stale heartbeats.

        Raises:
            None.
        """
        stale = self.check_neighbours(interval=interval, timeout=timeout)
        removed = []

        for node_id in stale:
            try:
                if node_id in getattr(mesh, '_graph', {}):
                    mesh.on_node_failure(node_id)
                    removed.append(node_id)
                self.last_seen.pop(node_id, None)
            except Exception as e:
                logger.warning(f'Failed self-heal for stale node {node_id[:8]}: {e}')

        return removed
