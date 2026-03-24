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


def _compute_proximity_score(lat1, lon1, lat2, lon2):
    """Compute proximity score based on geographic distance.

    Uses Euclidean distance on latitude/longitude grid normalized to 0-1 range.
    Closer nodes (smaller distance) receive higher proximity scores.

    Args:
        lat1: Latitude of first node (degrees).
        lon1: Longitude of first node (degrees).
        lat2: Latitude of second node (degrees).
        lon2: Longitude of second node (degrees).

    Returns:
        float: Proximity score in range [0.0, 1.0] where 1.0 is maximum proximity.

    Raises:
        None.
    """
    # Simple Euclidean distance on lat/lon grid
    # Max distance ~180 degrees, normalize to 0-1
    distance = math.sqrt((lat2 - lat1) ** 2 + (lon2 - lon1) ** 2)
    # Normalize: distance of 180 degrees = score 0, distance 0 = score 1
    max_distance = 180.0
    return max(0.0, 1.0 - (distance / max_distance))


def _compute_edge_weight(auth_rate, proximity_score, recency):
    """Compute edge weight from authentication rate, proximity, and recency.

    Produces weighted combination of three factors that influence trust and routing
    quality: 50% authentication success, 30% geographic proximity, 20% recency.
    All inputs are clamped to [0.0, 1.0] range before computation.

    Args:
        auth_rate: Authentication success rate (0-1), will be clamped.
        proximity_score: Geographic proximity score (0-1), will be clamped.
        recency: Recency score (0-1), will be clamped.

    Returns:
        float: Edge weight in range [0.0, 1.0].

    Raises:
        None.
    """
    # Clamp all values to [0.0, 1.0]
    auth_rate = max(0.0, min(1.0, auth_rate))
    proximity_score = max(0.0, min(1.0, proximity_score))
    recency = max(0.0, min(1.0, recency))
    # Weighted sum: 0.5*auth + 0.3*proximity + 0.2*recency
    return 0.5 * auth_rate + 0.3 * proximity_score + 0.2 * recency


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

    def update_edge(self, from_id, to_id, auth_rate, proximity_score=None, recency=1.0):
        """Update a directed edge in the trust graph.

        Creates or updates a directed edge from from_id to to_id with computed weight.
        The edge weight combines authentication success rate, geographic proximity, and
        recency to reflect trust and routing quality.

        Args:
            from_id: Source node identifier.
            to_id: Destination node identifier.
            auth_rate: Authentication success rate (0-1), will be clamped.
            proximity_score: Geographic proximity score (0-1). If None and both nodes have
                             lat/lon attributes, computed automatically. Default None.
            recency: Recency score (0-1), default 1.0, will be clamped.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            # Compute proximity_score if not provided
            if proximity_score is None:
                from_data = self._graph.nodes.get(from_id, {})
                to_data = self._graph.nodes.get(to_id, {})
                if (from_data.get('lat') is not None and from_data.get('lon') is not None and
                    to_data.get('lat') is not None and to_data.get('lon') is not None):
                    proximity_score = _compute_proximity_score(
                        from_data['lat'], from_data['lon'],
                        to_data['lat'], to_data['lon']
                    )
                else:
                    proximity_score = 0.5

            # Compute edge weight
            weight = _compute_edge_weight(auth_rate, proximity_score, recency)

            # Add directed edge with weight
            self._graph.add_edge(from_id, to_id, weight=weight)

            # Mark path cache dirty
            self._paths_dirty = True
