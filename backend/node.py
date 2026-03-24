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
