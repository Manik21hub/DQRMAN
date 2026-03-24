"""mesh.py: Distributed trust mesh including graph routing, self-healing, and geographic proximity.

This module implements the core mesh network topology, node discovery, dynamic routing,
and distributed consensus mechanisms for the DQRMAN peer-to-peer mesh network.
"""

import networkx as nx
import time
import logging
import threading
import math
import json
import struct
import socket
import random


# Module-level logger
logger = logging.getLogger(__name__)


class TrustGraph:
    """Distributed trust graph tracking node relationships and reachability."""

    def __init__(self):
        """Initialize a new trust graph.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self._graph = nx.DiGraph()
        self._trust_table = {}
        self._path_cache = {}
        self._original_node_count = 0
        self._paths_dirty = False
        self._reroute_timer = None
        self._lock = threading.Lock()

    def __len__(self):
        """Return the number of nodes in the graph.

        Args:
            None.

        Returns:
            int: Number of nodes in the graph.

        Raises:
            None.
        """
        return len(self._graph)

    def get_active_nodes(self):
        """Get list of node IDs with ACTIVE status.

        Args:
            None.

        Returns:
            list: Node IDs where status attribute is 'ACTIVE'.

        Raises:
            None.
        """
        active = []
        for node_id in self._graph.nodes():
            node_data = self._graph.nodes[node_id]
            if node_data.get('status') == 'ACTIVE':
                active.append(node_id)
        return active

    def add_node(self, node_id, public_key):
        """Add a node to the trust graph or verify its registration.

        Registers a new node with its public key. If the node already exists
        with a different public key, raises ValueError to prevent key confusion.

        Args:
            node_id: String identifier for the node.
            public_key: Bytes containing the node's public key material.

        Returns:
            None.

        Raises:
            ValueError: If node_id is registered with a different public_key.
        """
        with self._lock:
            # Check if node already registered with different key
            if node_id in self._trust_table:
                if self._trust_table[node_id] != public_key:
                    raise ValueError(
                        f'Node {node_id} already registered with a different public key'
                    )
                # Node exists with same key, return without action
                return

            # Add node to graph
            self._graph.add_node(
                node_id,
                public_key=public_key,
                joined_at=time.time(),
                status='ACTIVE',
                lat=0.0,
                lon=0.0,
            )

            # Update trust table
            self._trust_table[node_id] = public_key

            # Update node count tracking
            self._original_node_count = max(self._original_node_count, len(self._graph))

            # Mark paths dirty
            self._paths_dirty = True
