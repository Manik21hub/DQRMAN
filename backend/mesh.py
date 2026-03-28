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


def _config_value(config, key, default=None, section=None):
    """Read config value from flat key or optional nested section."""
    if not isinstance(config, dict):
        return default
    if key in config:
        return config.get(key, default)
    if section and isinstance(config.get(section), dict):
        return config[section].get(key, default)
    return default


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

    def __init__(self, config=None):
        """Initialize a new trust graph.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self._config = config or {}
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

            # Add directed edge with weight and metadata
            self._graph.add_edge(
                from_id, to_id,
                weight=weight,
                proximity_score=proximity_score,
                last_auth_time=time.time(),
                auth_success_count=0,
                auth_attempt_count=0,
            )

            # Mark path cache dirty
            self._paths_dirty = True

    def set_node_coords(self, node_id, lat, lon):
        """Set geographic coordinates for a node.

        Updates the latitude and longitude attributes for a node in the graph.
        These coordinates are used for proximity calculations and geographic routing.

        Args:
            node_id: Node identifier in the graph.
            lat: Latitude in degrees (float).
            lon: Longitude in degrees (float).

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            if node_id in self._graph:
                self._graph.nodes[node_id]['lat'] = float(lat)
                self._graph.nodes[node_id]['lon'] = float(lon)

    def compute_proximity_score(self, node_id_1, node_id_2):
        """Compute proximity score between two nodes using Haversine distance.

        Calculates great-circle distance between two nodes on Earth using the
        Haversine formula, then converts to a normalized proximity score where
        co-located nodes score 1.0, nodes 500m apart score 0.5, and farther
        nodes score proportionally lower.

        Args:
            node_id_1: First node identifier.
            node_id_2: Second node identifier.

        Returns:
            float: Proximity score in range [0.0, 1.0].

        Raises:
            None.
        """
        with self._lock:
            node_1_data = self._graph.nodes.get(node_id_1, {})
            node_2_data = self._graph.nodes.get(node_id_2, {})

            lat1 = node_1_data.get('lat', 0.0)
            lon1 = node_1_data.get('lon', 0.0)
            lat2 = node_2_data.get('lat', 0.0)
            lon2 = node_2_data.get('lon', 0.0)

            # Convert to radians
            lat1_rad = math.radians(lat1)
            lat2_rad = math.radians(lat2)
            delta_lat = math.radians(lat2 - lat1)
            delta_lon = math.radians(lon2 - lon1)

            # Haversine formula
            a = (math.sin(delta_lat / 2) ** 2 +
                 math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
            c = 2 * math.asin(math.sqrt(a))
            earth_radius_m = 6371000  # meters
            distance_m = earth_radius_m * c

            # Proximity score: 1.0 at 0m, 0.5 at 500m, decreasing thereafter
            proximity = 1.0 / (1.0 + distance_m / 500.0)
            return proximity

    def scatter_nodes_geographically(self, center_lat, center_lon, spread_m=500):
        """Assign random geographic coordinates to all active nodes.

        Distributes nodes around a geographic center point within a specified
        radius, using random bearing and distance for each node. Useful for
        testing mesh topology with geographic distribution.

        Args:
            center_lat: Center latitude in degrees (float).
            center_lon: Center longitude in degrees (float).
            spread_m: Maximum distance from center in meters (float), default 500.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            active_nodes = [
                node_id for node_id in self._graph.nodes()
                if self._graph.nodes[node_id].get('status') == 'ACTIVE'
            ]

            for node_id in active_nodes:
                # Random bearing [0, 2π]
                bearing = random.uniform(0, 2 * math.pi)
                # Random distance [50, spread_m] meters
                distance_m = random.uniform(50, spread_m)

                # Convert bearing and distance to lat/lon offsets
                # For small distances, approximate using degrees per meter
                # 1 degree of latitude ≈ 111,000 meters
                lat_offset = (distance_m * math.cos(bearing)) / 111000.0

                # Longitude offset depends on latitude
                center_lat_rad = math.radians(center_lat)
                lon_offset = (distance_m * math.sin(bearing)) / (111000.0 * math.cos(center_lat_rad))

                # Set node coordinates
                node_lat = center_lat + lat_offset
                node_lon = center_lon + lon_offset
                self._graph.nodes[node_id]['lat'] = node_lat
                self._graph.nodes[node_id]['lon'] = node_lon

    def compute_trust_path(self, source, target):
        """Compute shortest trust path from source to target node.

        Finds the path between two nodes that maximizes trust by using Dijkstra's
        algorithm with inverted edge weights. Higher-trust edges (higher weight)
        become lower-cost and are preferred in the path.

        The method caches results for efficiency, invalidating the cache when
        the network topology changes (_paths_dirty flag).

        Args:
            source: Source node identifier.
            target: Target node identifier.

        Returns:
            list: Ordered list of node IDs from source to target (inclusive).
                  Returns empty list if path does not exist or nodes are invalid.

        Raises:
            None.
        """
        with self._lock:
            # Check cache validity
            cache_key = (source, target)
            if cache_key in self._path_cache:
                cached_path, cached_time = self._path_cache[cache_key]
                age = time.time() - cached_time
                if not self._paths_dirty and age < 0.5:
                    return cached_path

            # Validate nodes exist
            if source not in self._graph or target not in self._graph:
                return []

            try:
                # Weight function: invert edge weights so high-trust edges (high weight)
                # become low-cost and are preferred by Dijkstra's algorithm.
                # A weight of 0.9 becomes cost 0.1 (preferred), while 0.1 becomes cost 0.9.
                def weight_fn(u, v, d):
                    edge_weight = d.get('weight', 0.5)
                    return 1.0 - edge_weight

                start_time = time.time()
                path = nx.dijkstra_path(self._graph, source, target, weight=weight_fn)
                elapsed_ms = (time.time() - start_time) * 1000

                trust_path_timeout = _config_value(
                    self._config,
                    'trust_path_timeout',
                    100,
                    section='network',
                )
                if elapsed_ms > trust_path_timeout:
                    logger.warning(
                        f'compute_trust_path {source[:8]} to {target[:8]} took {elapsed_ms:.1f}ms'
                    )

                # Cache the result with timestamp
                self._path_cache[cache_key] = (path, time.time())
                return path

            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return []

    def propagate_mesh_state(self, signing_node, changed_nodes, changed_edges):
        """Propagate mesh state changes to other nodes.

        Creates a signed state delta packet containing changes to node list and
        edge list. The packet is signed using the signing node's private key
        and includes the sender's identity and public key for verification.

        Args:
            signing_node: Node object with crypto capability and private key.
            changed_nodes: List of node IDs that changed (added/removed/modified).
            changed_edges: List of edge tuples (from_id, to_id) that changed.

        Returns:
            bytes: JSON-encoded packet with signature and metadata.

        Raises:
            None.
        """
        # Build delta dictionary
        delta = {
            'event_type': 'MESH_STATE_DELTA',
            'changed_nodes': changed_nodes,
            'changed_edges': changed_edges,
            'timestamp': time.time(),
            'node_count': len(self._graph),
        }

        # Convert to JSON bytes
        json_bytes = json.dumps(delta).encode('utf-8')

        # Sign using signing node's crypto module and private key
        signature = signing_node.crypto.sign(json_bytes, signing_node.private_key)

        # Build packet
        packet = {
            'data': json_bytes.hex(),
            'sender_id': signing_node.node_id,
            'sender_pubkey': signing_node.public_key.hex(),
            'signature': signature.hex(),
        }

        # Return as JSON bytes
        return json.dumps(packet).encode('utf-8')

    def verify_propagation(self, packet_bytes, expected_sender):
        """Verify a mesh state propagation packet.

        Validates the sender, checks that the sender's public key is registered
        in the trust table, and verifies the Dilithium signature on the contained
        state delta.

        Args:
            packet_bytes: JSON-encoded packet bytes from propagate_mesh_state.
            expected_sender: Expected sender node ID for validation.

        Returns:
            tuple: (success: bool, delta_dict: dict). Success is True only if
                   sender exists, is trusted, and signature verifies. delta_dict
                   is the parsed state delta on success, empty dict on failure.

        Raises:
            None.
        """
        try:
            # Parse packet
            packet = json.loads(packet_bytes.decode('utf-8'))
            sender_id = packet.get('sender_id')
            sender_pubkey_hex = packet.get('sender_pubkey')
            signature_hex = packet.get('signature')
            data_hex = packet.get('data')

            # Validate sender matches expected
            if sender_id != expected_sender:
                return (False, {})

            # Check sender is in trust table
            if sender_id not in self._trust_table:
                return (False, {})

            # Reconstruct data and signature bytes
            json_bytes = bytes.fromhex(data_hex)
            signature = bytes.fromhex(signature_hex)
            sender_pubkey = bytes.fromhex(sender_pubkey_hex)

            # Verify public key matches trust table
            if sender_pubkey != self._trust_table[sender_id]:
                return (False, {})

            # Verify signature using CryptoModule
            from backend.crypto import CryptoModule
            crypto = CryptoModule()
            if not crypto.verify(sender_pubkey, json_bytes, signature):
                return (False, {})

            # Parse and return delta
            delta = json.loads(json_bytes.decode('utf-8'))
            return (True, delta)

        except Exception:
            return (False, {})

    def is_operational(self):
        """Check if the mesh is operational based on node availability.

        Determines operational status by comparing active nodes to the
        original node count at network bootstrap. Requires at least 20%
        of original nodes to remain active for continued operation.

        Args:
            None.

        Returns:
            bool: True if active nodes / original_node_count >= 0.20,
                  or if original_node_count is 0. False if below threshold
                  with critical log message.

        Raises:
            None.
        """
        with self._lock:
            # Handle bootstrap case
            if self._original_node_count == 0:
                return True

            active_nodes = self.get_active_nodes()
            active_count = len(active_nodes)
            availability = active_count / self._original_node_count

            survivability_threshold = _config_value(
                self._config,
                'survivability_threshold',
                0.2,
                section='network',
            )
            if availability < survivability_threshold:
                logger.critical(
                    f'Mesh degraded: {active_count}/{self._original_node_count} '
                    f'nodes active ({availability*100:.1f}%)'
                )
                return False

            return True

    def on_node_failure(self, node_id):
        """Handle a node failure: mark destroyed, clean cache, trigger reroute.

        Processes node failure by setting node status to DESTROYED, removing
        related entries from the path cache, and triggering path recalculation
        with debounce to handle cascade failures gracefully.

        Args:
            node_id: Node identifier that has failed.

        Returns:
            dict: Event dictionary with failure details.

        Raises:
            None.
        """
        with self._lock:
            # Set node status to destroyed
            if node_id in self._graph:
                self._graph.nodes[node_id]['status'] = 'DESTROYED'

            # Remove path cache entries containing this node
            keys_to_remove = [
                key for key in self._path_cache
                if node_id in key  # key is (source, target) tuple
            ]
            for key in keys_to_remove:
                del self._path_cache[key]

            # Cancel existing reroute timer only if it is still running.
            if self._reroute_timer and self._reroute_timer.is_alive():
                self._reroute_timer.cancel()

            # Create debounce timer: 100ms delay before reroute
            self._reroute_timer = threading.Timer(
                0.1,
                self._reroute_paths,
                args=(node_id,)
            )
            self._reroute_timer.start()

            # Build event dictionary
            event = {
                'event_type': 'NODE_FAILURE',
                'node_id': node_id,
                'timestamp': time.time(),
                'active_count': len(self.get_active_nodes()),
            }

            return event

    def _reroute_paths(self, failed_node_id):
        """Recalculate paths after node failure.

        Helper method triggered by debounced timer after node failure.
        Evaluates network health, cleans cache, and marks for reroute.
        Returns early if fewer than 2 nodes survive to prevent
        rerouting in a severed network partition.

        Args:
            failed_node_id: Node ID that triggered reroute.

        Returns:
            None.

        Raises:
            None.
        """
        start_time = time.time()

        with self._lock:
            # Get list of surviving active nodes
            survivors = self.get_active_nodes()
            survivor_count = len(survivors)

            # Return early if mesh is too small to reroute
            if survivor_count < 2:
                logger.warning(
                    f'Reroute cancelled: {survivor_count} survivors remaining '
                    f'(minimum 2 required)'
                )
                return

            # Remove path cache entries containing failed node
            keys_to_remove = [
                key for key in self._path_cache
                if failed_node_id in key  # key is (source, target) tuple
            ]
            for key in keys_to_remove:
                del self._path_cache[key]

            # Mark paths dirty for recalculation
            self._paths_dirty = True

            # Measure elapsed time
            elapsed_ms = (time.time() - start_time) * 1000

            # Log warning if operation took too long
            if elapsed_ms > 2000:
                logger.warning(
                    f'Reroute took {elapsed_ms:.1f}ms (threshold 2000ms)'
                )

            # Log reroute completion
            logger.info(
                f'Rerouted paths after failure of {failed_node_id[:8]}: '
                f'{survivor_count} survivors remain, reroute took {elapsed_ms:.1f}ms'
            )

    def quarantine_node(self, node_id):
        """Isolate a compromised or misbehaving node from the mesh.

        Sets node status to QUARANTINED and removes all direct edges to/from it.
        The node remains in the graph for forensics but is isolated from routing.
        Unaffected nodes continue operating normally. Useful for handling
        detected Byzantine failures or anomalies.

        Args:
            node_id: Node identifier to quarantine.

        Returns:
            list: Node IDs of direct neighbours that were affected (had edges removed).

        Raises:
            None.
        """
        with self._lock:
            # Set node status to quarantined
            if node_id in self._graph:
                self._graph.nodes[node_id]['status'] = 'QUARANTINED'

            # Collect all direct neighbours (predecessors and successors)
            predecessors = list(self._graph.predecessors(node_id))
            successors = list(self._graph.successors(node_id))
            neighbours = list(set(predecessors + successors))

            # Build list of edges to remove
            edges_to_remove = []
            # Remove edges FROM quarantined node (outgoing)
            for successor in successors:
                edges_to_remove.append((node_id, successor))
            # Remove edges TO quarantined node (incoming)
            for predecessor in predecessors:
                edges_to_remove.append((predecessor, node_id))

            # Remove all edges
            self._graph.remove_edges_from(edges_to_remove)

            # Mark paths dirty
            self._paths_dirty = True

            # Count non-adjacent nodes
            all_nodes = set(self._graph.nodes())
            adjacent_nodes = set(neighbours + [node_id])
            non_adjacent_nodes = all_nodes - adjacent_nodes
            non_adjacent_count = len(non_adjacent_nodes)

            # Log quarantine action
            logger.warning(
                f'Quarantined node {node_id[:8]}: '
                f'{len(neighbours)} direct neighbours affected, '
                f'{non_adjacent_count} nodes completely unaffected'
            )

            return neighbours

    def route_message(self, message, routing_node, path, destination):
        """Route a message through the mesh with hop-based re-signing.

        Takes a message, routes it through the trust path, and signs/re-signs
        at each hop. Each intermediate node verifies the previous hop's signature
        and adds their own signature before forwarding. This creates a chain of
        trust across the path, ensuring all hops verify the previous link.

        Args:
            message: Bytes containing the message payload.
            routing_node: Node object with crypto capability and private key for signing.
            path: List of node IDs from source to destination (inclusive).
            destination: Target node ID (should be path[-1]).

        Returns:
            dict: Routed message packet with keys:
                - message: Original message as hex string
                - source: Sender node_id (path[0])
                - destination: Target node_id (path[-1])
                - path: List of all node IDs in path
                - signatures: List of (node_id, signature_hex) tuples for each hop
                - timestamp: Time of routing
                - hops_count: Number of hops in path

        Raises:
            None.
        """
        with self._lock:
            # Validate path
            if not path or len(path) < 2:
                logger.error('Invalid path: must have at least source and destination')
                return {}

            signatures = []
            current_message = message

            # Sign at each hop
            for idx, node_id in enumerate(path):
                # Build message for this hop: include path up to this node and original message
                hop_data = {
                    'message': message.hex(),
                    'path': path[:idx + 1],  # Path accumulated so far
                    'timestamp': time.time(),
                }
                hop_bytes = json.dumps(hop_data).encode('utf-8')

                # Sign using routing node's key
                signature = routing_node.crypto.sign(hop_bytes, routing_node._private_key)
                signatures.append((node_id, signature.hex()))

                current_message = hop_bytes

            # Build final routed packet
            packet = {
                'message': message.hex(),
                'source': path[0],
                'destination': path[-1],
                'path': path,
                'signatures': signatures,  # List of (node_id, signature) tuples
                'timestamp': time.time(),
                'hops_count': len(path),
            }

            logger.info(
                f'Routed message from {path[0][:8]} to {path[-1][:8]} '
                f'via {len(path) - 2} intermediate hops'
            )

            return packet

    def verify_message_route(self, packet_bytes):
        """Verify a routed message packet with chain of hop signatures.

        Validates the complete signature chain across all hops in the message
        path. Each hop's signature is verified using the previous hop's public key,
        ensuring all intermediate nodes properly signed and forwarded the message.

        Args:
            packet_bytes: JSON-encoded routed message packet from route_message.

        Returns:
            tuple: (success: bool, message_bytes: bytes). Success is True only if
                   all signatures verify. message_bytes contains the original message
                   on success, otherwise empty bytes.

        Raises:
            None.
        """
        try:
            # Parse packet
            packet = json.loads(packet_bytes.decode('utf-8'))
            message_hex = packet.get('message')
            source = packet.get('source')
            destination = packet.get('destination')
            path = packet.get('path', [])
            signatures = packet.get('signatures', [])

            if not all([message_hex, source, destination, path, signatures]):
                logger.warning('Invalid packet structure')
                return (False, b'')

            # Validate path integrity
            if len(signatures) != len(path):
                logger.warning(f'Signature count ({len(signatures)}) != path length ({len(path)})')
                return (False, b'')

            # Verify each signature in the chain
            from backend.crypto import CryptoModule
            crypto = CryptoModule()

            for idx, (node_id, sig_hex) in enumerate(signatures):
                # Verify node_id matches path
                if node_id != path[idx]:
                    logger.warning(f'Node mismatch at hop {idx}: {node_id[:8]} != {path[idx][:8]}')
                    return (False, b'')

                # Get node's public key from trust table
                if node_id not in self._trust_table:
                    logger.warning(f'Node {node_id[:8]} not in trust table')
                    return (False, b'')

                public_key = self._trust_table[node_id]
                signature = bytes.fromhex(sig_hex)

                # Build the message that was signed at this hop
                hop_data = {
                    'message': message_hex,
                    'path': path[:idx + 1],
                    'timestamp': packet.get('timestamp'),
                }
                hop_bytes = json.dumps(hop_data).encode('utf-8')

                # Verify signature
                if not crypto.verify(public_key, hop_bytes, signature):
                    logger.warning(f'Signature verification failed at hop {idx} ({node_id[:8]})')
                    return (False, b'')

            # All signatures verified
            message_bytes = bytes.fromhex(message_hex)
            logger.info(
                f'Message route verified: {source[:8]} → {destination[:8]} '
                f'via {len(path)} hops, all {len(signatures)} signatures valid'
            )

            return (True, message_bytes)

        except Exception as e:
            logger.error(f'Error verifying message route: {e}')
            return (False, b'')

    def refresh_trust_score(self, from_id, to_id, auth_success=True):
        """Refresh trust score on successful authentication.

        Updates the edge weight between two nodes based on authentication
        result. Success increases trust (higher weight), failure decreases it.
        Also records the timestamp to enable age-based decay.

        Args:
            from_id: Source node identifier.
            to_id: Destination node identifier.
            auth_success: Boolean indicating authentication success (default True).

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            if from_id not in self._graph or to_id not in self._graph:
                logger.warning(f'Cannot refresh trust: one or both nodes missing')
                return

            # Get current edge data
            if not self._graph.has_edge(from_id, to_id):
                # Create new edge if it doesn't exist
                logger.info(f'Creating new trust edge: {from_id[:8]} → {to_id[:8]}')
                self._graph.add_edge(from_id, to_id, weight=0.5, last_auth_time=time.time(),
                                    auth_success_count=1, auth_attempt_count=1)
                return

            edge_data = self._graph[from_id][to_id]
            current_weight = edge_data.get('weight', 0.5)
            last_auth_time = edge_data.get('last_auth_time', time.time())
            auth_success_count = edge_data.get('auth_success_count', 0)
            auth_attempt_count = edge_data.get('auth_attempt_count', 0)

            # Update counters
            auth_attempt_count += 1
            if auth_success:
                auth_success_count += 1

            # Calculate new auth_rate and update weight
            auth_rate = auth_success_count / auth_attempt_count if auth_attempt_count > 0 else 0.5
            proximity_score = edge_data.get('proximity_score', 0.5)
            recency = 1.0  # Fresh authentication

            new_weight = _compute_edge_weight(auth_rate, proximity_score, recency)

            # Update edge with new weight and timestamp
            self._graph[from_id][to_id].update({
                'weight': new_weight,
                'last_auth_time': time.time(),
                'auth_success_count': auth_success_count,
                'auth_attempt_count': auth_attempt_count,
                'proximity_score': proximity_score,
            })

            # Mark paths dirty for recalculation
            self._paths_dirty = True

            logger.info(
                f'Refreshed trust: {from_id[:8]} → {to_id[:8]} '
                f'auth_rate={auth_rate:.2%} new_weight={new_weight:.3f}'
            )

    def decay_stale_trust_edges(self, max_age_seconds=600):
        """Decay trust scores for edges with stale authentication.

        Reduces trust weight (edge cost) for edges that haven't been refreshed
        with successful authentication within the specified time window. Trust
        gradually decays, meaning nodes that stop authenticating lose routing
        preference. This prevents permanently trusting dead or silent nodes.

        Args:
            max_age_seconds: Maximum age before edge begins decaying (default 600s).

        Returns:
            list: List of (from_id, to_id) tuples that were decayed.

        Raises:
            None.
        """
        with self._lock:
            current_time = time.time()
            decayed_edges = []

            for from_id, to_id in self._graph.edges():
                edge_data = self._graph[from_id][to_id]
                last_auth_time = edge_data.get('last_auth_time', 0)
                age = current_time - last_auth_time

                # Skip if edge is fresh
                if age < max_age_seconds:
                    continue

                # Calculate decay factor: 0.5 at max_age, approaching 0 thereafter
                # decay_factor = 0.5 ^ (age / max_age_seconds)
                decay_factor = 0.5 ** (age / max_age_seconds)
                current_weight = edge_data.get('weight', 0.5)
                old_weight = current_weight
                new_weight = current_weight * decay_factor

                # Update edge weight
                self._graph[from_id][to_id]['weight'] = new_weight

                decayed_edges.append((from_id, to_id))
                logger.debug(
                    f'Decayed trust edge: {from_id[:8]} → {to_id[:8]} '
                    f'age={age:.0f}s weight {old_weight:.3f} → {new_weight:.3f} '
                    f'(decay_factor={decay_factor:.3f})'
                )

            # Mark paths dirty if any edges decayed
            if decayed_edges:
                self._paths_dirty = True
                logger.info(f'Decayed {len(decayed_edges)} stale trust edges')

            return decayed_edges

    def get_edge_stats(self, from_id, to_id):
        """Get detailed statistics for an edge including trust metrics.

        Retrieves authentication history, current weight, recency, and other
        trust metrics for a specific directed edge.

        Args:
            from_id: Source node identifier.
            to_id: Destination node identifier.

        Returns:
            dict: Edge statistics with keys: weight, auth_rate, auth_success_count,
                  auth_attempt_count, last_auth_time, proximity_score, age_seconds.
                  Returns empty dict if edge doesn't exist.

        Raises:
            None.
        """
        with self._lock:
            if not self._graph.has_edge(from_id, to_id):
                return {}

            edge_data = self._graph[from_id][to_id]
            last_auth_time = edge_data.get('last_auth_time', 0)
            age = time.time() - last_auth_time

            auth_success_count = edge_data.get('auth_success_count', 0)
            auth_attempt_count = edge_data.get('auth_attempt_count', 0)
            auth_rate = auth_success_count / auth_attempt_count if auth_attempt_count > 0 else 0.0

            return {
                'weight': edge_data.get('weight', 0.5),
                'auth_rate': auth_rate,
                'auth_success_count': auth_success_count,
                'auth_attempt_count': auth_attempt_count,
                'last_auth_time': last_auth_time,
                'proximity_score': edge_data.get('proximity_score', 0.0),
                'age_seconds': age,
            }
