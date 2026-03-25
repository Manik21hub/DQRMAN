import eventlet
eventlet.monkey_patch()

from flask import Flask
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


def _current_mesh_state():
	"""Return current mesh state payload structure for websocket clients."""
	return {
		'nodes': [],
		'edges': [],
		'events': [],
	}


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
