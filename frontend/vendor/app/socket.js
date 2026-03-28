window.DQRMAN = window.DQRMAN || {};

(function initSocket(ns) {
  function connect() {
    if (typeof window.io !== 'function') {
      ns.state.setWsStatus(false, 'socket.io missing');
      return;
    }

    const socket = window.io(window.location.origin, {
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 10000,
    });

    socket.on('connect', () => {
      ns.state.setWsStatus(true, 'connected');
    });

    socket.on('disconnect', () => {
      ns.state.setWsStatus(false, 'disconnected');
    });

    socket.on('connect_error', () => {
      ns.state.setWsStatus(false, 'reconnecting');
    });

    socket.on('mesh_state', (message) => {
      const payload = message && message.payload ? message.payload : {};
      ns.state.setMeshSnapshot(
        Array.isArray(payload.nodes) ? payload.nodes : [],
        Array.isArray(payload.edges) ? payload.edges : [],
        payload && typeof payload.stats === 'object' ? payload.stats : null
      );
      if (Array.isArray(payload.events) && payload.events.length > 0) {
        ns.state.appendEvents(payload.events);
      }
    });

    socket.on('attack_detected', (event) => {
      const attackType = String(event && event.attack_type ? event.attack_type : '').toLowerCase();
      const normalizedType = attackType === 'replay'
        ? 'EVENT_REPLAY_DETECTED'
        : attackType === 'spoof'
          ? 'EVENT_SPOOFING_ATTEMPT'
          : attackType === 'jamming'
            ? 'EVENT_JAMMING_SIMULATION'
            : 'EVENT_ATTACK_DETECTED';

      ns.state.appendEvents({
        event_type: normalizedType,
        timestamp: (event && event.timestamp) || new Date().toISOString(),
        target_node_id: event && event.target_node_id,
        detection_reason: event && event.detection_reason,
        payload: {
          attack_type: event && event.attack_type,
          target_node_id: event && event.target_node_id,
          detected: event && event.detected,
          detection_reason: event && event.detection_reason,
          duration_ms: event && event.duration_ms,
        },
      });
    });

    ns.socket = socket;
  }

  ns.socketClient = { connect };
})(window.DQRMAN);
