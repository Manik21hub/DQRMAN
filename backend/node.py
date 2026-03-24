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
    
    def transition_to(self, new_state):
        """Transition node to a new state.
        
        Args:
            new_state: Target NodeState value.
        """
        with self._lock:
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
