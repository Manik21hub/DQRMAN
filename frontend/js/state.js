window.DQRMAN = window.DQRMAN || {};

(function initState(ns) {
  const listeners = new Set();

  const state = {
    simulation: {
      running: false,
      nodeCount: 0,
      config: {},
    },
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
      meshStatus: 'OPERATIONAL',
      healthPercent: 100,
    },
    transport: {
      wsConnected: false,
      wsStatusText: 'reconnecting',
    },
  };

  function _edgeKey(source, target) {
    return `${source}::${target}`;
  }

  function _recomputeStats() {
    const nodes = Array.from(state.nodes.values());
    const active = nodes.filter((n) => n.status === 'ACTIVE').length;
    const destroyed = nodes.filter((n) => n.status === 'DESTROYED').length;
    const quarantined = nodes.filter((n) => n.status === 'QUARANTINED').length;
    const total = Math.max(nodes.length, 1);

    const authEvents = state.events.filter((e) => String(e.event_type || '').includes('AUTH')).slice(0, 50);
    const latencies = authEvents
      .map((e) => Number(e.duration_ms || (e.payload && e.payload.duration_ms)))
      .filter((x) => Number.isFinite(x));
    const avgLatency = latencies.length
      ? latencies.reduce((a, b) => a + b, 0) / latencies.length
      : 0;

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
        // Isolate listener failures.
      }
    });
  }

  function setMeshSnapshot(nodes, edges, serverStats) {
    state.nodes.clear();
    (nodes || []).forEach((node) => {
      state.nodes.set(node.node_id, node);
    });

    state.edges.clear();
    (edges || []).forEach((edge) => {
      const source = typeof edge.source === 'object' ? edge.source.node_id : edge.source;
      const target = typeof edge.target === 'object' ? edge.target.node_id : edge.target;
      if (!source || !target) return;
      state.edges.set(_edgeKey(source, target), {
        source,
        target,
        weight: Number(edge.weight || 0),
      });
    });

    state.simulation.running = state.nodes.size > 0;
    state.simulation.nodeCount = state.nodes.size;

    if (serverStats && typeof serverStats === 'object') {
      state.stats.active = Number(serverStats.active_nodes || 0);
      state.stats.destroyed = Number(serverStats.destroyed_nodes || 0);
      state.stats.meshStatus = String(serverStats.mesh_status || 'OPERATIONAL').toUpperCase();
      state.stats.healthPercent = Math.round(100 - Number(serverStats.destroyed_percent || 0));
    }

    _recomputeStats();
    notify('mesh_state');
  }

  function upsertNode(node) {
    if (!node || !node.node_id) return;
    state.nodes.set(node.node_id, node);
    _recomputeStats();
    notify('node_update');
  }

  function removeNode(nodeId) {
    state.nodes.delete(nodeId);
    _recomputeStats();
    notify('node_removed');
  }

  function appendEvents(events) {
    const incoming = Array.isArray(events) ? events : [events];
    incoming.forEach((event) => {
      if (!event) return;
      state.events.unshift(event);
    });

    if (state.events.length > 500) {
      state.events.length = 500;
    }

    _recomputeStats();
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

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  ns.state = {
    getState() {
      return state;
    },
    subscribe,
    setMeshSnapshot,
    upsertNode,
    removeNode,
    appendEvents,
    setSelectedNode,
    setWsStatus,
    notify,
  };
})(window.DQRMAN);
