window.DQRMAN = window.DQRMAN || {};

(function initState(ns) {
  const listeners = new Set();
  const eventKeys = new Set();

  const state = {
    simulation: { running: false, nodeCount: 0, config: {} },
    nodes: new Map(),
    edges: new Map(),
    events: [],
    selectedNodeId: null,
    stats: {
      active: 0,
      destroyed: 0,
      quarantined: 0,
      authsPerSec: 0,
      avgLatency: 0,
      meshStatus: 'UNKNOWN',
      healthPercent: 100,
    },
    transport: { wsConnected: false, wsStatusText: 'reconnecting' },
    ui: { activeTab: 'map', lastError: '' },
  };

  function edgeKey(source, target) {
    return `${source}::${target}`;
  }

  function eventKey(event) {
    if (!event || typeof event !== 'object') return 'invalid-event';
    const eventType = String(event.event_type || 'EVENT');
    const timestamp = String(event.timestamp || event.time || '');
    const nodeId = String(event.node_id || event.target_node_id || '');
    const peerId = String(event.peer_node_id || '');
    const payloadHash = JSON.stringify(event.payload || {});
    return `${eventType}|${timestamp}|${nodeId}|${peerId}|${payloadHash}`;
  }

  function rebuildEventIndex() {
    eventKeys.clear();
    state.events.forEach((event) => {
      eventKeys.add(eventKey(event));
    });
  }

  function recomputeStats() {
    const nodes = Array.from(state.nodes.values());
    const active = nodes.filter((n) => n.status === 'ACTIVE').length;
    const destroyed = nodes.filter((n) => n.status === 'DESTROYED').length;
    const quarantined = nodes.filter((n) => n.status === 'QUARANTINED').length;
    const total = Math.max(nodes.length, 1);
    const latencies = state.events
      .map((e) => Number(e.duration_ms || (e.payload && e.payload.duration_ms)))
      .filter((x) => Number.isFinite(x))
      .slice(0, 200);
    const avgLatency = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0;

    state.stats.active = active;
    state.stats.destroyed = destroyed;
    state.stats.quarantined = quarantined;
    state.stats.avgLatency = avgLatency;
    state.stats.healthPercent = Math.round((active / total) * 100);
  }

  function notify(reason) {
    listeners.forEach((listener) => {
      try {
        listener(state, reason);
      } catch (_err) {
        // Keep the stream alive if a subscriber fails.
      }
    });
  }

  function setMeshSnapshot(nodes, edges, serverStats) {
    state.nodes.clear();
    (nodes || []).forEach((node) => {
      if (node && node.node_id) state.nodes.set(node.node_id, node);
    });

    state.edges.clear();
    (edges || []).forEach((edge) => {
      const source = typeof edge.source === 'object' ? edge.source.node_id : edge.source;
      const target = typeof edge.target === 'object' ? edge.target.node_id : edge.target;
      if (!source || !target) return;
      state.edges.set(edgeKey(source, target), { source, target, weight: Number(edge.weight || 0) });
    });

    state.simulation.running = state.nodes.size > 0;
    state.simulation.nodeCount = state.nodes.size;

    if (serverStats && typeof serverStats === 'object') {
      state.stats.active = Number(serverStats.active_nodes || 0);
      state.stats.destroyed = Number(serverStats.destroyed_nodes || 0);
      state.stats.meshStatus = String(serverStats.mesh_status || 'OPERATIONAL').toUpperCase();
      state.stats.healthPercent = Math.max(0, Math.min(100, Math.round(100 - Number(serverStats.destroyed_percent || 0))));
    }

    recomputeStats();
    notify('mesh_state');
  }

  function appendEvents(events) {
    const incoming = Array.isArray(events) ? events : [events];
    incoming.forEach((event) => {
      if (!event) return;
      const key = eventKey(event);
      if (eventKeys.has(key)) return;
      eventKeys.add(key);
      state.events.unshift(event);
    });
    if (state.events.length > 500) state.events.length = 500;
    rebuildEventIndex();
    recomputeStats();
    notify('events');
  }

  function setSelectedNode(nodeId) {
    state.selectedNodeId = nodeId || null;
    notify('selection');
  }

  function setWsStatus(connected, text) {
    state.transport.wsConnected = Boolean(connected);
    state.transport.wsStatusText = text;
    notify('ws');
  }

  function setTab(tabId) {
    state.ui.activeTab = tabId;
    notify('tab');
  }

  function setError(msg) {
    state.ui.lastError = msg || '';
    notify('error');
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  ns.state = {
    getState: () => state,
    subscribe,
    setMeshSnapshot,
    appendEvents,
    setSelectedNode,
    setWsStatus,
    setTab,
    setError,
    notify,
  };
})(window.DQRMAN);
