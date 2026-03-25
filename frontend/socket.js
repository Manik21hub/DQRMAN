class MeshSocket {
  constructor(url, onMessage, onConnect, onDisconnect) {
    this.url = url;
    this.onMessage = typeof onMessage === 'function' ? onMessage : function () {};
    this.onConnect = typeof onConnect === 'function' ? onConnect : function () {};
    this.onDisconnect = typeof onDisconnect === 'function' ? onDisconnect : function () {};

    this.ws = null;
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 30000;
    this.reconnectTimer = null;
  }

  connect() {
    this.ws = new WebSocket(this.url);

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.onMessage(data);
      } catch (_err) {
        // Ignore malformed messages to keep socket flow alive.
      }
    };

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.onConnect();
    };

    this.ws.onclose = () => {
      this.onDisconnect();
      this.reconnect();
    };

    this.ws.onerror = () => {
      this.onDisconnect();
      this.reconnect();
    };
  }

  reconnect() {
    clearTimeout(this.reconnectTimer);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, this.reconnectDelay);

    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
  }
}

window.MeshSocket = MeshSocket;
