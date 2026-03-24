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
    INITIALIZING = enum.auto()
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
        """Initialize empty nonce cache."""
        self._nonces = {}
    
    def contains(self, nonce):
        """Check if nonce exists and is not expired.
        
        Evicts expired entries before checking membership.
        
        Args:
            nonce: Bytes object representing the nonce.
            
        Returns:
            bool: True if nonce exists and has not expired, False otherwise.
        """
        now = time.time()
        # Evict expired entries
        self._nonces = {k: v for k, v in self._nonces.items() if v > now}
        return nonce.hex() in self._nonces
    
    def add(self, nonce, ttl):
        """Add a nonce with given TTL.
        
        Args:
            nonce: Bytes object representing the nonce.
            ttl: Time-to-live in seconds. Expiry is set to now + ttl.
        """
        self._nonces[nonce.hex()] = time.time() + ttl


class AuthProtocol:
    """Authentication protocol for peer-to-peer node verification.
    
    Implements mutual authentication challenge-response mechanism with
    nonce-based replay protection and timestamp validation.
    """
    
    def authenticate(self, node_a, node_b):
        """Authenticate node_b to node_a using challenge-response protocol.
        
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
        time_sync_window = node_a._config.get('time_sync_window', 5.0)
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
        """Initialize a new network node.
        
        Args:
            config: Optional configuration dictionary containing 'dilithium_variant'.
                   Defaults to 'ML-DSA-65' if not provided.
                   
        Raises:
            CryptoError: If cryptographic module initialization fails.
        """
        from backend.crypto import CryptoModule
        
        if config is None:
            config = {}
        
        algorithm = config.get('dilithium_variant', 'ML-DSA-65')
        crypto = CryptoModule(algorithm=algorithm)
        
        self.public_key, self._private_key = crypto.generate_keypair()
        self.node_id = crypto.derive_node_id(self.public_key)
        self.state = NodeState.INITIALIZING
        self.trust_table = {}
        self._lock = threading.Lock()
        self._algorithm = algorithm
        self._config = config
        self.crypto = crypto
        self.nonce_cache = NonceCache()
    
    def transition_to(self, new_state):
        """Transition node to a new state.
        
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
        """Broadcast join message to neighbours and transition to JOINING state.
        
        Builds a cryptographically signed join packet containing this node's
        identity and public key, sends it as JSON to all neighbours, and 
        transitions to JOINING state.
        
        Args:
            neighbours: List of neighbour nodes to send join packet to.
            
        Returns:
            The join packet dictionary containing node_id, public_key, signature,
            and timestamp.
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
        """Verify a join packet from another node.
        
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
        """Create an authentication challenge for peer verification.
        
        Implements SRS Section 8.2 Steps 1-3: generates a fresh nonce,
        timestamps it, and signs the combined message.
        
        Returns:
            Dictionary with node_id, public_key, nonce, timestamp, and signature.
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
        """Verify an authentication challenge from a peer node.
        
        Implements SRS Section 8.2 Steps 4-6: validates timestamp freshness,
        checks for nonce replay, and verifies cryptographic signature.
        
        Args:
            challenge: Dictionary with node_id, public_key, nonce, timestamp, signature.
            
        Returns:
            Tuple of (success: bool, error: str or None).
            If valid: (True, None)
            If invalid: (False, error_reason)
        """
        # Step 1: Check state
        if self.state in (NodeState.QUARANTINED, NodeState.DESTROYED, NodeState.INITIALIZING):
            return (False, self.state.name)
        
        # Step 2: Check timestamp freshness
        now = time.time()
        time_sync_window = self._config.get('time_sync_window', 5.0)
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
        
        # Step 5: Add nonce to cache
        self.nonce_cache.add(nonce_bytes, time_sync_window)
        
        return (True, None)
