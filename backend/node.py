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
