class MeshSocket {
  constructor(url, handlers) {
    this.url = url;
    const callbacks = handlers || {};
    this.onMessage = typeof callbacks.onMessage === 'function' ? callbacks.onMessage : function () {};
    this.onConnect = typeof callbacks.onConnect === 'function' ? callbacks.onConnect : function () {};
    this.onDisconnect = typeof callbacks.onDisconnect === 'function' ? callbacks.onDisconnect : function () {};
    // Assignable callback for direct attack_detected socket events
    this.onAttackDetected = typeof callbacks.onAttackDetected === 'function' ? callbacks.onAttackDetected : function () {};

    this.socket = null;
  }

  connect() {
    if (typeof window.io !== 'function') {
      this.onDisconnect();
      return;
    }

    this.socket = window.io(this.url, {
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 30000,
    });

    this.socket.on('connect', () => {
      this.onConnect();
    });

    this.socket.on('disconnect', () => {
      this.onDisconnect();
    });

    this.socket.on('connect_error', () => {
      this.onDisconnect();
    });

    this.socket.on('mesh_state', (message) => {
      const payload = message && message.payload ? message.payload : {};
      let latencyMs = null;
      if (message && message.timestamp) {
        const ts = Date.parse(message.timestamp);
        if (!Number.isNaN(ts)) {
          latencyMs = Date.now() - ts;
        }
      }
      this.onMessage({
        nodes: Array.isArray(payload.nodes) ? payload.nodes : [],
        edges: Array.isArray(payload.edges) ? payload.edges : [],
        stats: payload && typeof payload.stats === 'object' ? payload.stats : null,
        events: Array.isArray(payload.events) ? payload.events : [],
        _latency_ms: latencyMs,
      });
    });

    // Forward server-emitted attack_detected events to the registered callback
    this.socket.on('attack_detected', (data) => {
      this.onAttackDetected(data);
    });
  }
}

window.MeshSocket = MeshSocket;
