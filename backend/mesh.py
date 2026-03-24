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
