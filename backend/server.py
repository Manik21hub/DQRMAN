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
