window.DQRMAN = window.DQRMAN || {};

(function initApi(ns) {
  async function request(path, options) {
    const res = await fetch(path, options || {});
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_err) {
      data = null;
    }
    if (!res.ok) {
      const msg = (data && data.error) || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function jsonPost(path, payload) {
    return request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
  }

  ns.api = {
    getHealth: () => request('/health'),
    getNodes: () => request('/api/v1/nodes'),
    getEvents: () => request('/api/v1/events'),
    getScaleReport: () => request('/api/v1/scale-report'),
    getSimulationConfig: () => request('/api/v1/simulation/config'),
    setSimulationConfig: (payload) => jsonPost('/api/v1/simulation/config', payload),
    startSimulation: (payload) => jsonPost('/api/v1/simulation/start', payload),
    stopSimulation: () => jsonPost('/api/v1/simulation/stop', {}),
    runAttack: (attack_type, target_node_id, delay_seconds) =>
      jsonPost('/api/v1/attack', { attack_type, target_node_id, delay_seconds: Number(delay_seconds || 0) }),
    reroute: (source_node_id, destination_node_id, message_id) =>
      jsonPost('/api/v1/route', {
        source_node_id,
        target_node_id: destination_node_id,
        message_id: message_id || '',
      }),
    setLocation: (node_id, lat, lon, accuracy) => jsonPost('/api/v1/location', { node_id, lat, lon, accuracy }),
    deleteNode: (nodeId) => request(`/api/v1/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' }),
  };
})(window.DQRMAN);
