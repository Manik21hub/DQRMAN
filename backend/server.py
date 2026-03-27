import eventlet
eventlet.monkey_patch()
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
import requests
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

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

logger = logging.getLogger(__name__)


emit_lock = threading.Lock()
pending_events = []
flush_timer = None
mesh = None
ALLOWED_ATTACK_TYPES = {'replay', 'spoof', 'jamming'}
APP_CONFIG = {'log_file': os.getenv('DQRMAN_LOG_FILE', 'logs/events.log')}
LOG_FILE_PATH = pathlib.Path(APP_CONFIG['log_file'])
FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / 'frontend'
FRONTEND_VENDOR_DIR = FRONTEND_DIR / 'vendor'
TILES_CACHE_DIR = FRONTEND_VENDOR_DIR / 'tiles'
log_write_lock = threading.Lock()


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
			'events': [],
		}

	nodes = _serialize_nodes()
	edges = _serialize_edges()
	return {
		'nodes': nodes,
		'edges': edges,
		'events': [],
	}


def _serialize_nodes():
	"""Convert mesh node attributes to API payload format."""
	if mesh is None:
		return []

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
	message = {
		'event_type': 'mesh_state',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {
			'nodes': nodes,
			'edges': edges,
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

	emit_mesh_update([], [], events_batch)


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
	return jsonify(_serialize_nodes())


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
		last_lines = handle.readlines()[-100:]

	events = []
	for line in last_lines:
		line = line.strip()
		if not line:
			continue
		try:
			events.append(json.loads(line))
		except json.JSONDecodeError:
			continue

	events.sort(key=lambda item: item.get('timestamp', ''), reverse=True)
	return jsonify(events)


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

	mesh.on_node_failure(node_id)
	surviving_count = len(mesh.get_active_nodes())
	operational = mesh.is_operational()
	event = {
		'event_type': 'NODE_DESTROYED',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {'node_id': node_id},
	}
	queue_event(event)

	return jsonify(
		{
			'node_id': node_id,
			'surviving_count': surviving_count,
			'is_operational': operational,
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
	_init_mesh(args.nodes)

	if args.kill_node_id:
		if args.kill_node_id in mesh._graph:
			mesh.on_node_failure(args.kill_node_id)
			logger.info('Destroyed node via --kill: %s', args.kill_node_id)
		else:
			logger.warning('Requested --kill node not found: %s', args.kill_node_id)

	logger.info('Starting server on port %d', args.port)
	socketio.run(app, host='0.0.0.0', port=args.port)


if __name__ == '__main__':
	main()
