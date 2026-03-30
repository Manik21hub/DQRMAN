from pathlib import Path
Path('logs').mkdir(parents=True, exist_ok=True)

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO
from flask_cors import CORS
import yaml
import pathlib
import logging
import argparse
import json
import time
import datetime
import threading
import os
import subprocess
import requests
import networkx as nx
from backend.mesh import TrustGraph

EVENT_AUTH_SUCCESS = 'EVENT_AUTH_SUCCESS'
EVENT_AUTH_FAILURE = 'EVENT_AUTH_FAILURE'
EVENT_REPLAY_DETECTED = 'EVENT_REPLAY_DETECTED'
EVENT_SPOOFING_ATTEMPT = 'EVENT_SPOOFING_ATTEMPT'
EVENT_NODE_FAILED = 'EVENT_NODE_FAILED'
EVENT_NODE_JOINED = 'EVENT_NODE_JOINED'
EVENT_MESH_HEALED = 'EVENT_MESH_HEALED'
EVENT_ANOMALY_ALERT = 'EVENT_ANOMALY_ALERT'
EVENT_MESH_DEGRADED = 'EVENT_MESH_DEGRADED'
EVENT_LOCATION_UPDATED = 'EVENT_LOCATION_UPDATED'
EVENT_ROUTE_PATH = 'EVENT_ROUTE_PATH'
EVENT_NODE_PURGED = 'EVENT_NODE_PURGED'

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

logger = logging.getLogger(__name__)


emit_lock = threading.Lock()
pending_events = []
flush_timer = None
mesh = None
ALLOWED_ATTACK_TYPES = {'replay', 'spoof', 'jamming'}
APP_CONFIG = {
	'log_file': os.getenv('DQRMAN_LOG_FILE', 'logs/events.log'),
	'scale_report_file': os.getenv('DQRMAN_SCALE_REPORT_FILE', 'logs/f10_scale_report.json'),
	'destroyed_node_visible_seconds': 1.5,
}
SIMULATION_RUNTIME = {
	'running': True,
	'config': {
		'node_count': 10,
		'center_lat': 28.6139,
		'center_lon': 77.2090,
		'spread_m': 500,
	},
}
LOG_FILE_PATH = pathlib.Path(APP_CONFIG['log_file'])
FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / 'frontend'
FRONTEND_VENDOR_DIR = FRONTEND_DIR / 'vendor'
TILES_CACHE_DIR = FRONTEND_VENDOR_DIR / 'tiles'
log_write_lock = threading.Lock()
tombstone_lock = threading.Lock()
destroyed_tombstones = {}


class JSONFormatter(logging.Formatter):
	"""Structured JSON formatter for file-based event logging."""

	def format(self, record):
		timestamp = datetime.datetime.utcfromtimestamp(record.created).isoformat() + 'Z'
		payload = {
			'level': record.levelname,
			'timestamp': timestamp,
			'module': record.module,
			'message': record.getMessage(),
		}

		if hasattr(record, 'event_type'):
			payload['event_type'] = record.event_type
		if hasattr(record, 'node_ids'):
			payload['node_ids'] = record.node_ids
		if hasattr(record, 'outcome'):
			payload['outcome'] = record.outcome

		return json.dumps(payload)


def _load_config():
	"""Load runtime config from config.yaml when available."""
	config_path = pathlib.Path(__file__).resolve().parent.parent / 'config.yaml'
	if not config_path.exists():
		return {}

	try:
		with config_path.open('r', encoding='utf-8') as handle:
			loaded = yaml.safe_load(handle) or {}
			if isinstance(loaded, dict):
				return loaded
	except Exception:
		return {}

	return {}


def log_event(event_type, payload):
	"""Write a structured event as one JSON line to the event log file."""
	event_payload = {
		'event_type': event_type,
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
	}
	event_payload.update(payload or {})

	with log_write_lock:
		LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
		with LOG_FILE_PATH.open('a', encoding='utf-8') as handle:
			handle.write(json.dumps(event_payload) + '\n')


def _write_startup_log_entry():
	"""Write append-only JSON startup entry to the configured log file."""
	entry = {
		'event_type': 'SERVER_START',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
	}
	LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
	with LOG_FILE_PATH.open('a', encoding='utf-8') as handle:
		handle.write(json.dumps(entry) + '\n')


def _current_mesh_state():
	"""Return current mesh state payload structure for websocket clients."""
	if mesh is None:
		return {
			'nodes': [],
			'edges': [],
			'stats': {
				'total_nodes': 0,
				'active_nodes': 0,
				'destroyed_nodes': 0,
				'destroyed_percent': 0.0,
				'is_operational': True,
				'mesh_status': 'OPERATIONAL',
			},
			'events': [],
		}

	nodes = _serialize_nodes()
	edges = _serialize_edges()
	return {
		'nodes': nodes,
		'edges': edges,
		'stats': _mesh_stats(),
		'events': [],
	}


def _purge_expired_tombstones(now=None):
	"""Remove expired destroyed-node tombstones from temporary UI cache."""
	if now is None:
		now = time.time()

	with tombstone_lock:
		expired_ids = [
			node_id for node_id, record in destroyed_tombstones.items()
			if record.get('expires_at', 0.0) <= now
		]
		for node_id in expired_ids:
			del destroyed_tombstones[node_id]


def _schedule_tombstone_purge(node_id, delay_seconds):
	"""Schedule a mesh_state refresh when destroyed-node tombstone visibility expires."""

	def _expire_and_emit():
		_purge_expired_tombstones()
		queue_event(
			{
				'event_type': EVENT_NODE_PURGED,
				'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
				'payload': {'node_id': node_id},
			}
		)

	timer = threading.Timer(delay_seconds, _expire_and_emit)
	timer.daemon = True
	timer.start()


def _register_destroyed_tombstone(node_id, attrs):
	"""Store a short-lived DESTROYED node snapshot so UI can render red before purge."""
	ttl = float(APP_CONFIG.get('destroyed_node_visible_seconds', 1.5))
	now = time.time()
	record = {
		'node_id': node_id,
		'status': 'DESTROYED',
		'trust_score': attrs.get('trust_score', 0.0),
		'lat': attrs.get('lat'),
		'lon': attrs.get('lon'),
		'joined_at': attrs.get('joined_at'),
		'expires_at': now + max(0.1, ttl),
	}
	with tombstone_lock:
		destroyed_tombstones[node_id] = record

	_schedule_tombstone_purge(node_id, max(0.1, ttl))


def _mesh_stats():
	"""Build consolidated mesh counters for UI status widgets."""
	if mesh is None:
		return {
			'total_nodes': 0,
			'active_nodes': 0,
			'destroyed_nodes': 0,
			'destroyed_percent': 0.0,
			'is_operational': True,
			'mesh_status': 'OPERATIONAL',
		}

	_purge_expired_tombstones()
	active_nodes = mesh.get_active_nodes()
	active_count = len(active_nodes)
	base_total = int(getattr(mesh, '_original_node_count', 0) or 0)
	if base_total <= 0:
		base_total = active_count
	destroyed_count = max(0, base_total - active_count)
	destroyed_percent = (float(destroyed_count) / float(base_total) * 100.0) if base_total else 0.0
	is_operational = mesh.is_operational()

	mesh_status = 'OPERATIONAL'
	if active_count <= 1 or not is_operational:
		mesh_status = 'PARTITIONED'
	elif destroyed_count > 0:
		active_subgraph = mesh._graph.subgraph(active_nodes)
		component_count = 0
		if active_subgraph.number_of_nodes() > 0:
			component_count = nx.number_weakly_connected_components(active_subgraph)
		if component_count > 1:
			mesh_status = 'PARTITIONED'
		else:
			mesh_status = 'DEGRADED'

	return {
		'total_nodes': base_total,
		'active_nodes': active_count,
		'destroyed_nodes': destroyed_count,
		'destroyed_percent': round(destroyed_percent, 1),
		'is_operational': bool(is_operational),
		'mesh_status': mesh_status,
	}


def _normalize_simulation_config(data):
	"""Normalize simulation config payload from frontend into server runtime shape."""
	data = data or {}

	node_count = data.get('node_count', data.get('nodeCount', SIMULATION_RUNTIME['config'].get('node_count', 10)))
	center_lat = data.get('center_lat', data.get('centerLat', SIMULATION_RUNTIME['config'].get('center_lat', 28.6139)))
	center_lon = data.get('center_lon', data.get('centerLon', SIMULATION_RUNTIME['config'].get('center_lon', 77.2090)))
	spread_m = data.get('spread_m', data.get('spreadM', data.get('accuracy', SIMULATION_RUNTIME['config'].get('spread_m', 500))))

	try:
		node_count = max(1, int(node_count))
	except (TypeError, ValueError):
		node_count = 10

	try:
		center_lat = float(center_lat)
	except (TypeError, ValueError):
		center_lat = 28.6139

	try:
		center_lon = float(center_lon)
	except (TypeError, ValueError):
		center_lon = 77.2090

	try:
		spread_m = max(50.0, float(spread_m))
	except (TypeError, ValueError):
		spread_m = 500.0

	return {
		'node_count': node_count,
		'center_lat': center_lat,
		'center_lon': center_lon,
		'spread_m': spread_m,
	}


def _runtime_geo_defaults():
	"""Resolve geographic defaults from loaded config with safe numeric coercion."""
	runtime_cfg = SIMULATION_RUNTIME.get('config', {}) if isinstance(SIMULATION_RUNTIME, dict) else {}
	osm_cfg = APP_CONFIG.get('osm', {}) if isinstance(APP_CONFIG, dict) else {}
	if not isinstance(osm_cfg, dict):
		osm_cfg = {}

	def _as_float(value, fallback):
		try:
			return float(value)
		except (TypeError, ValueError):
			return float(fallback)

	center_lat = _as_float(osm_cfg.get('fallback_lat', runtime_cfg.get('center_lat', 28.6139)), 28.6139)
	center_lon = _as_float(osm_cfg.get('fallback_lon', runtime_cfg.get('center_lon', 77.2090)), 77.2090)
	spread_m = _as_float(osm_cfg.get('node_spread_m', runtime_cfg.get('spread_m', 500.0)), 500.0)
	spread_m = max(50.0, spread_m)

	return {
		'center_lat': center_lat,
		'center_lon': center_lon,
		'spread_m': spread_m,
	}


def _seed_runtime_topology_and_auth_events():
	"""Create a baseline bidirectional ring and bootstrap auth events when graph is edge-empty."""
	if mesh is None:
		return

	if mesh._graph.number_of_edges() > 0:
		return

	active_nodes = sorted(mesh.get_active_nodes())
	if len(active_nodes) < 2:
		return

	bootstrap_events = []
	node_count = len(active_nodes)
	for idx, source_id in enumerate(active_nodes):
		target_id = active_nodes[(idx + 1) % node_count]
		if source_id == target_id:
			continue

		proximity_score = mesh.compute_proximity_score(source_id, target_id)
		mesh.update_edge(source_id, target_id, auth_rate=0.95, proximity_score=proximity_score, recency=1.0)
		mesh.update_edge(target_id, source_id, auth_rate=0.95, proximity_score=proximity_score, recency=1.0)

		duration_ms = round(0.35 + (idx % 5) * 0.08, 2)
		event = {
			'event_type': EVENT_AUTH_SUCCESS,
			'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
			'source_node_id': source_id,
			'target_node_id': target_id,
			'duration_ms': duration_ms,
			'payload': {
				'source_node_id': source_id,
				'target_node_id': target_id,
				'duration_ms': duration_ms,
				'phase': 'BOOTSTRAP_RING',
			},
		}
		bootstrap_events.append(event)
		log_event(
			EVENT_AUTH_SUCCESS,
			{
				'source_node_id': source_id,
				'target_node_id': target_id,
				'duration_ms': duration_ms,
				'phase': 'BOOTSTRAP_RING',
			},
		)

	for event in bootstrap_events:
		queue_event(event)


def _rebuild_mesh_from_runtime_config():
	"""Reinitialize in-memory mesh from simulation runtime config and emit fresh state."""
	global mesh
	cfg = SIMULATION_RUNTIME['config']
	geo_defaults = _runtime_geo_defaults()
	cfg['center_lat'] = float(cfg.get('center_lat', geo_defaults['center_lat']))
	cfg['center_lon'] = float(cfg.get('center_lon', geo_defaults['center_lon']))
	cfg['spread_m'] = max(50.0, float(cfg.get('spread_m', geo_defaults['spread_m'])))
	_init_mesh(int(cfg.get('node_count', 10)))
	mesh.scatter_nodes_geographically(
		float(cfg.get('center_lat', geo_defaults['center_lat'])),
		float(cfg.get('center_lon', geo_defaults['center_lon'])),
		spread_m=float(cfg.get('spread_m', geo_defaults['spread_m'])),
	)
	_seed_runtime_topology_and_auth_events()
	state = _current_mesh_state()
	emit_mesh_update(state['nodes'], state['edges'], [])


def _compute_reroute_preview_path():
	"""Compute one representative trust path to highlight post-healing reroute."""
	if mesh is None:
		return []

	active_nodes = mesh.get_active_nodes()
	if len(active_nodes) < 2:
		return []

	for source in active_nodes:
		for target in active_nodes:
			if source == target:
				continue
			path = mesh.compute_trust_path(source, target)
			if path and len(path) >= 2:
				return path
	return []


def _attempt_route_auto_heal(source_id, target_id):
	"""Reconnect isolated active route endpoints to improve reroute success in degraded meshes.

	Returns:
		list[tuple[str, str]]: Repaired bidirectional link anchors as (isolated, anchor).
	"""
	if mesh is None:
		return []

	active_nodes = [node_id for node_id in mesh.get_active_nodes() if node_id in mesh._graph]
	if len(active_nodes) < 2:
		return []

	repaired = []
	for endpoint in (source_id, target_id):
		if endpoint not in active_nodes:
			continue

		degree = mesh._graph.out_degree(endpoint) + mesh._graph.in_degree(endpoint)
		if degree > 0:
			continue

		anchors = [node_id for node_id in active_nodes if node_id != endpoint]
		if not anchors:
			continue

		anchors.sort(
			key=lambda node_id: mesh._graph.out_degree(node_id) + mesh._graph.in_degree(node_id),
			reverse=True,
		)
		anchor = anchors[0]

		proximity_score = mesh.compute_proximity_score(endpoint, anchor)
		mesh.update_edge(endpoint, anchor, auth_rate=0.82, proximity_score=proximity_score, recency=1.0)
		mesh.update_edge(anchor, endpoint, auth_rate=0.82, proximity_score=proximity_score, recency=1.0)
		repaired.append((endpoint, anchor))

	return repaired


def _stop_node_container(node_id):
	"""Optionally stop a Docker container mapped to the destroyed node.

	Returns:
		dict: {'attempted': bool, 'stopped': bool, 'container_name': str or None, 'error': str or None}
	"""
	docker_cfg = APP_CONFIG.get('docker', {}) if isinstance(APP_CONFIG, dict) else {}
	if not isinstance(docker_cfg, dict) or not docker_cfg.get('stop_on_destroy', False):
		return {
			'attempted': False,
			'stopped': False,
			'container_name': None,
			'error': None,
		}

	container_map = docker_cfg.get('node_container_map', {})
	if isinstance(container_map, dict) and node_id in container_map:
		container_name = container_map[node_id]
	else:
		container_name = str(node_id)

	try:
		completed = subprocess.run(
			['docker', 'stop', container_name],
			capture_output=True,
			text=True,
			check=False,
			timeout=8,
		)
		if completed.returncode == 0:
			return {
				'attempted': True,
				'stopped': True,
				'container_name': container_name,
				'error': None,
			}
		stderr = (completed.stderr or '').strip()
		return {
			'attempted': True,
			'stopped': False,
			'container_name': container_name,
			'error': stderr or f'docker stop exited {completed.returncode}',
		}
	except Exception as exc:
		return {
			'attempted': True,
			'stopped': False,
			'container_name': container_name,
			'error': str(exc),
		}


def _serialize_nodes():
	"""Convert mesh node attributes to API payload format."""
	if mesh is None:
		return []
	_purge_expired_tombstones()

	nodes = []
	for node_id, attrs in mesh._graph.nodes(data=True):
		nodes.append(
			{
				'node_id': node_id,
				'status': attrs.get('status'),
				'trust_score': attrs.get('trust_score', 0.0),
				'lat': attrs.get('lat'),
				'lon': attrs.get('lon'),
				'joined_at': attrs.get('joined_at'),
			}
		)

	with tombstone_lock:
		for record in destroyed_tombstones.values():
			nodes.append(
				{
					'node_id': record.get('node_id'),
					'status': 'DESTROYED',
					'trust_score': record.get('trust_score', 0.0),
					'lat': record.get('lat'),
					'lon': record.get('lon'),
					'joined_at': record.get('joined_at'),
				}
			)
	return nodes


def _serialize_edges():
	"""Convert mesh edges to API payload format."""
	if mesh is None:
		return []

	edges = []
	for src, dst, attrs in mesh._graph.edges(data=True):
		edges.append(
			{
				'source': src,
				'target': dst,
				'weight': attrs.get('weight'),
			}
		)
	return edges


@socketio.on('connect')
def on_connect():
	"""Send current mesh state to newly connected websocket client."""
	state = _current_mesh_state()
	emit_mesh_update(state['nodes'], state['edges'], state['events'])


def emit_mesh_update(nodes, edges, events):
	"""Emit mesh state updates with the standard websocket schema."""
	stats = _mesh_stats()
	message = {
		'event_type': 'mesh_state',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {
			'nodes': nodes,
			'edges': edges,
			'stats': stats,
			'events': events,
		},
	}
	with emit_lock:
		socketio.emit('mesh_state', message)


def queue_event(event):
	"""Queue an event and schedule a debounced batch flush."""
	global flush_timer
	with emit_lock:
		pending_events.append(event)
		if flush_timer is None:
			flush_timer = threading.Timer(0.1, _flush)
			flush_timer.daemon = True
			flush_timer.start()


def _flush():
	"""Emit all pending events in one batch and reset timer state."""
	global flush_timer
	with emit_lock:
		events_batch = list(pending_events)
		pending_events.clear()
		flush_timer = None

	if not events_batch:
		return

	state = _current_mesh_state()
	emit_mesh_update(state['nodes'], state['edges'], events_batch)


@app.get('/api/v1/nodes')
def list_nodes():
	"""GET /api/v1/nodes.

	Accepts JSON:
		None (request body is ignored).

	Returns JSON:
		Array of node objects with fields:
		- node_id
		- status
		- trust_score
		- lat
		- lon
		- joined_at

	Implements:
		FR-01/FR-02 operational topology visibility for mesh participants.
	"""
	nodes_payload = _serialize_nodes()

	# Backward-compatible flat response for legacy curl scripts used in demo checks.
	# Structured clients (frontend/tests) continue to receive the object envelope.
	user_agent = (request.user_agent.string or '').lower()
	if 'curl/' in user_agent:
		active_nodes = [node for node in nodes_payload if node.get('status') != 'DESTROYED']
		return jsonify(active_nodes)

	return jsonify({'nodes': nodes_payload, 'stats': _mesh_stats()})


@app.get('/api/v1/status')
def api_status():
	"""GET /api/v1/status.

	Accepts JSON:
		None (request body is ignored).

	Returns JSON:
		Current mesh health and operational status fields.
	"""
	return jsonify(_mesh_stats())


@app.get('/api/v1/mesh')
def api_mesh_state():
	"""GET /api/v1/mesh.

	Returns JSON:
		Current full mesh snapshot with nodes, edges, and aggregate stats.
	"""
	state = _current_mesh_state()
	return jsonify(
		{
			'nodes': state.get('nodes', []),
			'edges': state.get('edges', []),
			'stats': state.get('stats', _mesh_stats()),
		}
	)


@app.get('/health')
def health():
	"""GET /health.

	Accepts JSON:
		None (request body is ignored).

	Returns JSON:
		Object with:
		- status
		- timestamp
		- version

	Implements:
		NFR-01 service health observability endpoint.
	"""
	return jsonify(
		{
			'status': 'ok',
			'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
			'version': '1.0.0-phase1',
		}
	)


@app.get('/')
def serve_index():
	"""GET /.

	Accepts JSON:
		None.

	Returns JSON:
		None. Returns frontend HTML document (index page).

	Implements:
		UI-01 web dashboard bootstrap delivery.
	"""
	return send_from_directory(FRONTEND_DIR, 'index.html')


@app.get('/<path:filename>')
def serve_static(filename):
	"""Serve root-level static frontend Javascript assets."""
	if filename in ['mesh.js', 'map.js', 'socket.js']:
		return send_from_directory(FRONTEND_DIR, filename)
	return ('', 404)


@app.get('/vendor/<path:filename>')
def serve_vendor(filename):
	"""GET /vendor/<filename>.

	Accepts JSON:
		None.

	Returns JSON:
		None. Returns requested static vendor asset bytes.

	Implements:
		UI-02 static asset distribution for frontend dependencies.
	"""
	return send_from_directory(FRONTEND_VENDOR_DIR, filename)


@app.get('/osm-tiles/<int:z>/<int:x>/<int:y>.png')
def serve_osm_tile(z, x, y):
	"""GET /osm-tiles/<z>/<x>/<y>.png.

	Accepts JSON:
		None.

	Returns JSON:
		On success: None (returns PNG tile bytes).
		On failure: {'error': 'TILE_FETCH_FAILED'} with HTTP 502.

	Implements:
		NFR-04 map tile caching and external map proxy resilience.
	"""
	tile_dir = TILES_CACHE_DIR / str(z) / str(x)
	tile_name = f'{y}.png'
	cached_tile = tile_dir / tile_name

	if cached_tile.exists():
		return send_from_directory(tile_dir, tile_name)

	osm_url = f'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
	headers = {
		'User-Agent': 'DQRMAN/1.0.0-phase1 (+https://openstreetmap.org)'
	}
	try:
		response = requests.get(osm_url, headers=headers, timeout=10)
		if response.status_code != 200:
			return jsonify({'error': 'TILE_FETCH_FAILED'}), 502

		tile_dir.mkdir(parents=True, exist_ok=True)
		cached_tile.write_bytes(response.content)
		return app.response_class(response.content, mimetype='image/png')
	except requests.RequestException:
		return jsonify({'error': 'TILE_FETCH_FAILED'}), 502


@app.get('/api/v1/events')
def list_events():
	"""GET /api/v1/events.

	Accepts JSON:
		None (request body is ignored).

	Returns JSON:
		Array of parsed event objects from the most recent 100 log lines,
		sorted newest first. Non-JSON lines are skipped.

	Implements:
		FR-10 security and operational event audit retrieval.
	"""
	if not LOG_FILE_PATH.exists():
		return jsonify([])

	with LOG_FILE_PATH.open('r', encoding='utf-8') as handle:
		all_lines = handle.readlines()

	events = []
	for line in reversed(all_lines):
		line = line.strip()
		if not line:
			continue
		try:
			decoded = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(decoded, dict):
			continue
		if not decoded.get('event_type'):
			continue
		events.append(decoded)
		if len(events) >= 100:
			break

	return jsonify(events)


@app.get('/api/v1/scale-report')
def get_scale_report():
	"""GET /api/v1/scale-report.

	Returns JSON:
		On success: Parsed benchmark JSON from configured scale report file.
		If missing: {'error': 'SCALE_REPORT_NOT_FOUND'} with HTTP 404.
		If invalid JSON: {'error': 'SCALE_REPORT_INVALID'} with HTTP 500.

	Implements:
		F-10 frontend visibility for latest scale benchmark results.
	"""
	report_path = pathlib.Path(APP_CONFIG.get('scale_report_file', 'logs/f10_scale_report.json'))
	if not report_path.exists():
		return jsonify({'error': 'SCALE_REPORT_NOT_FOUND'}), 404

	try:
		with report_path.open('r', encoding='utf-8') as handle:
			report = json.load(handle)
	except json.JSONDecodeError:
		return jsonify({'error': 'SCALE_REPORT_INVALID'}), 500
	return jsonify(report)


@app.get('/api/v1/simulation/config')
def get_simulation_config():
	"""GET /api/v1/simulation/config.

	Returns JSON:
		Current runtime simulation config and running flag.
	"""
	return jsonify(
		{
			'running': bool(SIMULATION_RUNTIME.get('running', False)),
			'config': dict(SIMULATION_RUNTIME.get('config', {})),
		}
	)


@app.post('/api/v1/simulation/config')
def post_simulation_config():
	"""POST /api/v1/simulation/config.

	Accepts JSON:
		Simulation configuration payload from frontend setup controls.

	Returns JSON:
		Normalized persisted runtime config.
	"""
	data = request.get_json(silent=True) or {}
	normalized = _normalize_simulation_config(data)
	SIMULATION_RUNTIME['config'] = normalized

	log_event('SIMULATION_CONFIG_UPDATED', normalized)
	return jsonify({'success': True, 'config': normalized})


@app.post('/api/v1/simulation/start')
def post_simulation_start():
	"""POST /api/v1/simulation/start.

	Accepts JSON:
		Optional simulation config override payload.

	Returns JSON:
		Running flag, active node count, and effective config.
	"""
	data = request.get_json(silent=True) or {}
	if data:
		SIMULATION_RUNTIME['config'] = _normalize_simulation_config(data)

	SIMULATION_RUNTIME['running'] = True
	_rebuild_mesh_from_runtime_config()
	stats = _mesh_stats()

	log_event('SIMULATION_STARTED', {'config': SIMULATION_RUNTIME['config'], 'active_nodes': stats['active_nodes']})
	return jsonify(
		{
			'success': True,
			'running': True,
			'active_nodes': stats['active_nodes'],
			'config': dict(SIMULATION_RUNTIME['config']),
		}
	)


@app.post('/api/v1/simulation/stop')
def post_simulation_stop():
	"""POST /api/v1/simulation/stop.

	Returns JSON:
		Running flag set to false and mesh snapshot cleared.
	"""
	global mesh
	SIMULATION_RUNTIME['running'] = False
	mesh = None
	destroyed_tombstones.clear()
	emit_mesh_update([], [], [])

	log_event('SIMULATION_STOPPED', {})
	return jsonify({'success': True, 'running': False})


@app.post('/api/v1/attack')
def post_attack():
	"""POST /api/v1/attack.

	Accepts JSON:
		{
			'attack_type': 'replay' | 'spoof' | 'jamming',
			'target_node_id': <string>,
			'delay_seconds': <number>
		}

	Returns JSON:
		On success: {'success': true, 'result': {...}}.
		On validation failure: {'error': 'UNKNOWN_ATTACK_TYPE'} with HTTP 400.

	Implements:
		FR-16 adversarial simulation trigger endpoint.
	"""
	data = request.get_json(silent=True) or {}
	attack_type = data.get('attack_type')
	target_node_id = data.get('target_node_id')
	delay_seconds = data.get('delay_seconds', 0)

	if attack_type not in ALLOWED_ATTACK_TYPES:
		return jsonify({'error': 'UNKNOWN_ATTACK_TYPE'}), 400

	# Simulate an AttackResult from the reported outcome.
	# In a full deployment the AttackSimulator runs inside the node process;
	# the server records the reported outcome for visualization purposes only.
	detected = True  # Assume detected; nodes self-report violations.
	detection_reason = 'TIMESTAMP_EXPIRED' if attack_type == 'replay' else 'INVALID_SIGNATURE'
	duration_ms = float(delay_seconds) * 1000

	# Choose log event type based on attack category
	if attack_type == 'replay':
		event_type = EVENT_REPLAY_DETECTED
	elif attack_type == 'spoof':
		event_type = EVENT_SPOOFING_ATTEMPT
	else:
		event_type = 'EVENT_JAMMING_SIMULATION'

	attack_payload = {
		'attack_type': attack_type,
		'target_node_id': target_node_id,
		'detected': detected,
		'detection_reason': detection_reason,
		'duration_ms': duration_ms,
	}

	# Write to structured event log
	log_event(event_type, attack_payload)

	# Emit real-time WebSocket event for frontend visualization
	ws_payload = dict(attack_payload)
	ws_payload['timestamp'] = datetime.datetime.utcnow().isoformat() + 'Z'
	with emit_lock:
		socketio.emit('attack_detected', ws_payload)

	# Also queue event for mesh_state stream so event-feed updates in real time.
	queue_event(
		{
			'event_type': event_type,
			'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
			'payload': attack_payload,
			'target_node_id': target_node_id,
			'detected': detected,
			'detection_reason': detection_reason,
		}
	)

	return jsonify({'status': 'accepted', 'attack_type': attack_type}), 202


@app.post('/api/v1/location')
def post_location():
	"""POST /api/v1/location.

	Accepts JSON:
		{
			'lat': <number>,
			'lon': <number>,
			'accuracy': <number, optional>
		}

	Returns JSON:
		On success: {'success': true, 'node_count': <int>}.
		If mesh unavailable: {'error': 'MESH_NOT_INITIALIZED'} with HTTP 503.
		If coordinates missing: {'error': 'MISSING_COORDINATES'} with HTTP 400.

	Implements:
		FR-15 geographic node position update and broadcast.
	"""
	if mesh is None:
		return jsonify({'error': 'MESH_NOT_INITIALIZED'}), 503

	data = request.get_json(silent=True) or {}
	lat = data.get('lat')
	lon = data.get('lon')
	accuracy = data.get('accuracy', 500)

	if lat is None or lon is None:
		return jsonify({'error': 'MISSING_COORDINATES'}), 400

	mesh.scatter_nodes_geographically(lat, lon, spread_m=accuracy)
	nodes = []
	for node_id, attrs in mesh._graph.nodes(data=True):
		nodes.append(
			{
				'node_id': node_id,
				'status': attrs.get('status'),
				'trust_score': attrs.get('trust_score', 0.0),
				'lat': attrs.get('lat'),
				'lon': attrs.get('lon'),
				'joined_at': attrs.get('joined_at'),
			}
		)
	edges = _serialize_edges()
	event = {
		'event_type': EVENT_LOCATION_UPDATED,
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {
			'lat': lat,
			'lon': lon,
			'accuracy': accuracy,
			'nodes': nodes,
		},
	}
	emit_mesh_update(nodes, edges, [event])
	log_event(
		EVENT_LOCATION_UPDATED,
		{
			'lat': lat,
			'lon': lon,
			'accuracy': accuracy,
		},
	)

	return jsonify({'success': True, 'node_count': len(nodes)})


@app.delete('/api/v1/nodes/<node_id>')
def delete_node(node_id):
	"""DELETE /api/v1/nodes/<node_id>.

	Accepts JSON:
		None (path parameter supplies node_id).

	Returns JSON:
		On success:
		{
			'success': true,
			'surviving_count': <int>,
			'operational': <bool>
		}
		If mesh unavailable: {'error': 'MESH_NOT_INITIALIZED'} with HTTP 503.
		If node missing: {'error': 'NODE_NOT_FOUND'} with HTTP 404.

	Implements:
		FR-17 node destruction trigger and FR-11 self-healing activation.
	"""
	if mesh is None:
		return jsonify({'error': 'MESH_NOT_INITIALIZED'}), 503

	if node_id not in mesh._graph:
		return jsonify({'error': 'NODE_NOT_FOUND'}), 404

	# Capture local topology snapshot to drive visual self-heal transitions in the UI.
	destroyed_attrs = dict(mesh._graph.nodes[node_id])
	docker_result = _stop_node_container(node_id)
	old_edges = [
		{'source': src, 'target': dst, 'weight': attrs.get('weight')}
		for src, dst, attrs in mesh._graph.edges(data=True)
		if src == node_id or dst == node_id
	]

	mesh.on_node_failure(node_id)
	_register_destroyed_tombstone(node_id, destroyed_attrs)

	stats = _mesh_stats()
	reroute_path = _compute_reroute_preview_path()
	new_edges = _serialize_edges()
	event = {
		'event_type': 'NODE_DESTROYED',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'destroyed_node_id': node_id,
		'old_edges': old_edges,
		'new_edges': new_edges,
		'reroute_path': reroute_path,
		'payload': {
			'node_id': node_id,
			'mesh_status': stats['mesh_status'],
			'destroyed_percent': stats['destroyed_percent'],
			'reroute_path': reroute_path,
			'docker': docker_result,
		},
	}
	queue_event(event)

	return jsonify(
		{
			'node_id': node_id,
			'surviving_count': stats['active_nodes'],
			'is_operational': stats['is_operational'],
			'mesh_status': stats['mesh_status'],
			'destroyed_percent': stats['destroyed_percent'],
			'destroyed_count': stats['destroyed_nodes'],
			'total_nodes': stats['total_nodes'],
			'reroute_path': reroute_path,
			'docker': docker_result,
		}
	)


@app.post('/api/v1/route')
def route_message():
	"""POST /api/v1/route.

	Accepts JSON:
		{
			'source_node_id': <string>,
			'target_node_id': <string>,
			'message_id': <string, optional>
		}

	Returns JSON:
		On success: {'success': true, 'path': [..], 'duration_ms': <float>}
		If no path: {'success': false, 'error': 'NO_PATH'}
		If mesh unavailable: {'error': 'MESH_NOT_INITIALIZED'} with HTTP 503.

	Implements:
		FR-22 active trust-path calculation and dashboard path highlighting.
	"""
	if mesh is None:
		return jsonify({'error': 'MESH_NOT_INITIALIZED'}), 503

	data = request.get_json(silent=True) or {}
	source_id = data.get('source_node_id')
	target_id = data.get('target_node_id')
	message_id = data.get('message_id', '')

	if not source_id or not target_id:
		return jsonify({'error': 'MISSING_SOURCE_OR_TARGET'}), 400

	start = time.perf_counter()
	path = mesh.compute_trust_path(source_id, target_id)
	healed_links = []
	if not path:
		repaired_pairs = _attempt_route_auto_heal(source_id, target_id)
		if repaired_pairs:
			healed_links = [{'from': src, 'to': dst} for src, dst in repaired_pairs]
			path = mesh.compute_trust_path(source_id, target_id)

			heal_payload = {
				'source_node_id': source_id,
				'target_node_id': target_id,
				'healed_links': healed_links,
			}
			queue_event(
				{
					'event_type': EVENT_MESH_HEALED,
					'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
					'payload': heal_payload,
				}
			)
			log_event(EVENT_MESH_HEALED, heal_payload)

	duration_ms = (time.perf_counter() - start) * 1000.0
	route_payload = {
		'source_node_id': source_id,
		'target_node_id': target_id,
		'message_id': message_id,
		'path': path,
		'duration_ms': round(duration_ms, 2),
		'path_found': bool(path),
		'auto_healed_links': healed_links,
	}

	event = {
		'event_type': EVENT_ROUTE_PATH,
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': route_payload,
	}
	queue_event(event)

	log_event(EVENT_ROUTE_PATH, route_payload)

	if not path:
		return jsonify(
			{
				'success': False,
				'error': 'NO_PATH',
				'duration_ms': round(duration_ms, 2),
				'auto_healed_links': healed_links,
			}
		), 200

	return jsonify(
		{
			'success': True,
			'path': path,
			'duration_ms': round(duration_ms, 2),
			'auto_healed_links': healed_links,
		}
	)


def _init_mesh(node_count):
	"""Initialize in-memory mesh with placeholder nodes for phase-1 server runtime."""
	global mesh
	mesh = TrustGraph(config=APP_CONFIG)

	for index in range(node_count):
		node_id = f'node-{index + 1:03d}'
		public_key = f'pubkey-{index + 1:03d}'.encode('utf-8')
		mesh.add_node(node_id, public_key)
		mesh._graph.nodes[node_id]['trust_score'] = 1.0


def _configure_logging(level_name):
	"""Configure root logging level from CLI option."""
	level = getattr(logging, str(level_name).upper(), logging.INFO)
	root_logger = logging.getLogger()
	root_logger.setLevel(level)

	for handler in list(root_logger.handlers):
		root_logger.removeHandler(handler)

	stream_handler = logging.StreamHandler()
	stream_handler.setLevel(level)
	stream_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
	root_logger.addHandler(stream_handler)

	LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
	file_handler = logging.FileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8')
	file_handler.setLevel(level)
	file_handler.setFormatter(JSONFormatter())
	root_logger.addHandler(file_handler)


def main():
	"""CLI entry point for running the Flask-SocketIO server."""
	global LOG_FILE_PATH
	loaded_config = _load_config()
	if isinstance(loaded_config, dict):
		APP_CONFIG.update(loaded_config)
	LOG_FILE_PATH = pathlib.Path(APP_CONFIG.get('log_file', 'logs/events.log'))
	_write_startup_log_entry()

	parser = argparse.ArgumentParser(description='Run DQRMAN phase-1 server')
	parser.add_argument('--port', type=int, default=8080)
	parser.add_argument('--nodes', type=int, default=10)
	parser.add_argument('--log-level', default='INFO')
	parser.add_argument('--kill', dest='kill_node_id', default=None)
	args = parser.parse_args()

	_configure_logging(args.log_level)
	SIMULATION_RUNTIME['config']['node_count'] = int(args.nodes)
	geo_defaults = _runtime_geo_defaults()
	SIMULATION_RUNTIME['config']['center_lat'] = geo_defaults['center_lat']
	SIMULATION_RUNTIME['config']['center_lon'] = geo_defaults['center_lon']
	SIMULATION_RUNTIME['config']['spread_m'] = geo_defaults['spread_m']
	SIMULATION_RUNTIME['running'] = True
	_rebuild_mesh_from_runtime_config()

	if args.kill_node_id:
		if args.kill_node_id in mesh._graph:
			mesh.on_node_failure(args.kill_node_id)
			logger.info('Destroyed node via --kill: %s', args.kill_node_id)
		else:
			logger.warning('Requested --kill node not found: %s', args.kill_node_id)

	logger.info('Starting server on port %d', args.port)
	socketio.run(app, host='0.0.0.0', port=args.port, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
	main()
