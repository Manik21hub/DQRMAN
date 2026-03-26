class MeshSocket {
  constructor(url, handlers) {
    this.url = url;
    const callbacks = handlers || {};
    this.onMessage = typeof callbacks.onMessage === 'function' ? callbacks.onMessage : function () {};
    this.onConnect = typeof callbacks.onConnect === 'function' ? callbacks.onConnect : function () {};
    this.onDisconnect = typeof callbacks.onDisconnect === 'function' ? callbacks.onDisconnect : function () {};

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
      this.onMessage({
        nodes: Array.isArray(payload.nodes) ? payload.nodes : [],
        edges: Array.isArray(payload.edges) ? payload.edges : [],
        events: Array.isArray(payload.events) ? payload.events : [],
      });
    });
  }
}

window.MeshSocket = MeshSocket;
