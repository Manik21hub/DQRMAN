import eventlet
eventlet.monkey_patch()

from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS
import yaml
import pathlib
import logging
import json
import time
import datetime
import threading
import os
import requests

# Ensure log directory exists before configuring any file handlers.
pathlib.Path('logs').mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

logger = logging.getLogger(__name__)


emit_lock = threading.Lock()
pending_events = []
flush_timer = None
mesh = None
ALLOWED_ATTACK_TYPES = {'replay', 'spoof', 'jamming'}
LOG_FILE_PATH = pathlib.Path(os.getenv('DQRMAN_LOG_FILE', 'logs/events.log'))


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
	"""Return all mesh nodes with lifecycle and location metadata."""
	return jsonify(_serialize_nodes())


@app.get('/api/v1/events')
def list_events():
	"""Return last 100 JSON log lines sorted newest first."""
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
	"""Register an attack simulation request."""
	data = request.get_json(silent=True) or {}
	attack_type = data.get('attack_type')
	target_node_id = data.get('target_node_id')
	delay_seconds = data.get('delay_seconds', 0)

	if attack_type not in ALLOWED_ATTACK_TYPES:
		return jsonify({'error': 'UNKNOWN_ATTACK_TYPE'}), 400

	event = {
		'event_type': 'ATTACK_REQUESTED',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {
			'attack_type': attack_type,
			'target_node_id': target_node_id,
			'delay_seconds': delay_seconds,
		},
	}
	queue_event(event)
	return jsonify({'success': True, 'attack': event['payload']})


@app.post('/api/v1/location')
def post_location():
	"""Scatter mesh nodes around provided coordinates and emit updates."""
	if mesh is None:
		return jsonify({'error': 'MESH_NOT_INITIALIZED'}), 503

	data = request.get_json(silent=True) or {}
	lat = data.get('lat')
	lon = data.get('lon')
	accuracy = data.get('accuracy', 500)

	if lat is None or lon is None:
		return jsonify({'error': 'MISSING_COORDINATES'}), 400

	mesh.scatter_nodes_geographically(lat, lon, spread_m=accuracy)
	nodes = _serialize_nodes()
	edges = _serialize_edges()
	event = {
		'event_type': 'NODE_POSITION_UPDATE',
		'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
		'payload': {
			'lat': lat,
			'lon': lon,
			'accuracy': accuracy,
		},
	}
	emit_mesh_update(nodes, edges, [event])

	return jsonify({'success': True, 'node_count': len(nodes)})


@app.delete('/api/v1/nodes/<node_id>')
def delete_node(node_id):
	"""Mark node as destroyed, emit event, and report survivability."""
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
			'success': True,
			'surviving_count': surviving_count,
			'operational': operational,
		}
	)
