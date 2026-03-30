window.DQRMAN = window.DQRMAN || {};

(function startApp(ns) {
  let authCount = 0;
  let blockedCount = 0;
  let serverClockOffsetMs = 0;
  let snapshotPollTimer = null;
  let clockSyncTimer = null;

  function classifyCounters(events) {
    authCount = events.filter((evt) => String(evt.event_type || '').toUpperCase().includes('AUTH')).length;
    blockedCount = events.filter((evt) => {
      const e = String(evt.event_type || '').toUpperCase();
      return e.includes('REPLAY') || e.includes('SPOOF') || e.includes('JAMMING') || e.includes('DESTROYED');
    }).length;
  }

  function normalizeNodesEnvelope(nodesResp) {
    const nodes = Array.isArray(nodesResp && nodesResp.nodes)
      ? nodesResp.nodes
      : Array.isArray(nodesResp)
        ? nodesResp
        : [];

    const stats = (nodesResp && typeof nodesResp === 'object' && !Array.isArray(nodesResp) && nodesResp.stats)
      ? nodesResp.stats
      : null;

    return { nodes, stats };
  }

  function normalizeMeshEnvelope(meshResp) {
    const nodes = Array.isArray(meshResp && meshResp.nodes) ? meshResp.nodes : [];
    const edges = Array.isArray(meshResp && meshResp.edges) ? meshResp.edges : [];
    const stats = (meshResp && typeof meshResp === 'object' && meshResp.stats) ? meshResp.stats : null;
    return { nodes, edges, stats };
  }

  function normalizeEventsEnvelope(eventsResp) {
    if (Array.isArray(eventsResp)) return eventsResp;
    if (eventsResp && Array.isArray(eventsResp.events)) return eventsResp.events;
    return [];
  }

  function syncClockOffset(serverTimestamp) {
    if (!serverTimestamp) return;
    const serverMs = Date.parse(serverTimestamp);
    if (!Number.isFinite(serverMs)) return;
    serverClockOffsetMs = serverMs - Date.now();
  }

  function updateGlobalStatus(state) {
    const meshText = document.getElementById('sb-mesh');
    const nodesText = document.getElementById('sb-nodes');
    const latText = document.getElementById('sb-latency');
    const authText = document.getElementById('sb-auth');
    const blockText = document.getElementById('sb-blocked');
    const wsPill = document.getElementById('pill-alerts');

    if (meshText) {
      meshText.className = `val ${state.stats.meshStatus === 'OPERATIONAL' ? 'ok' : state.stats.meshStatus === 'DEGRADED' ? 'warn' : 'err'}`;
      meshText.textContent = state.stats.meshStatus;
    }
    if (nodesText) nodesText.textContent = `${state.stats.active} active`;
    if (latText) latText.textContent = ns.utils.formatLatencyMs(state.stats.avgLatency || 0);
    if (authText) authText.textContent = authCount.toLocaleString();
    if (blockText) blockText.textContent = blockedCount.toLocaleString();
    if (wsPill) {
      const hasActiveMesh = Number(state.stats.active || 0) > 0;
      if (state.transport.wsConnected) {
        wsPill.className = 'npill ok';
        wsPill.innerHTML = `<div class="d"></div>${blockedCount} ALERTS`;
      } else if (hasActiveMesh) {
        wsPill.className = 'npill warn';
        wsPill.innerHTML = '<div class="d"></div>POLL MODE';
      } else {
        wsPill.className = 'npill err';
        wsPill.innerHTML = '<div class="d"></div>WS DOWN';
      }
    }
  }

  function updateHealthBadge(ok) {
    const el = document.getElementById('pill-mesh');
    if (!el) return;
    el.className = `npill ${ok ? 'ok' : 'err'}`;
    el.innerHTML = `<div class="d"></div>${ok ? 'MESH ACTIVE' : 'SERVICE DOWN'}`;
  }

  async function fetchMeshSnapshot() {
    try {
      const meshResp = await ns.api.getMesh();
      return normalizeMeshEnvelope(meshResp);
    } catch (_err) {
      // Backward compatibility path for older servers without /api/v1/mesh.
      const nodesResp = await ns.api.getNodes();
      const normalizedNodes = normalizeNodesEnvelope(nodesResp);
      return {
        nodes: normalizedNodes.nodes,
        edges: [],
        stats: normalizedNodes.stats,
      };
    }
  }

  async function refreshSnapshot(force) {
    const wsConnected = ns.state.getState().transport.wsConnected;
    if (!force && wsConnected) return;

    try {
      const [meshSnapshot, eventsResp] = await Promise.all([
        fetchMeshSnapshot(),
        ns.api.getEvents(),
      ]);
      ns.state.setMeshSnapshot(meshSnapshot.nodes, meshSnapshot.edges, meshSnapshot.stats);

      const events = normalizeEventsEnvelope(eventsResp);
      classifyCounters(events);
      ns.state.appendEvents(events);
    } catch (err) {
      ns.state.setError(err.message || 'Snapshot refresh failed');
    }
  }

  async function syncServerClock() {
    try {
      const health = await ns.api.getHealth();
      syncClockOffset(health && health.timestamp);
      const ok = String((health && health.status) || '').toLowerCase() === 'ok';
      updateHealthBadge(ok);
    } catch (_err) {
      // Keep using the last known offset when health probe is unavailable.
    }
  }

  async function initialLoad() {
    try {
      const [healthResp, meshSnapshot, eventsResp] = await Promise.all([
        ns.api.getHealth(),
        fetchMeshSnapshot(),
        ns.api.getEvents(),
      ]);

      const ok = String(healthResp && healthResp.status || '').toLowerCase() === 'ok';
      updateHealthBadge(ok);
      syncClockOffset(healthResp && healthResp.timestamp);

      const events = normalizeEventsEnvelope(eventsResp);

      classifyCounters(events);
      ns.state.setMeshSnapshot(meshSnapshot.nodes, meshSnapshot.edges, meshSnapshot.stats);
      ns.state.appendEvents(events);

      try {
        const simCfg = await ns.api.getSimulationConfig();
        if (simCfg && simCfg.config && simCfg.config.node_count) {
          ns.state.getState().simulation.nodeCount = Number(simCfg.config.node_count);
        }
      } catch (_err) {
        // Optional endpoint; silently continue.
      }

      try {
        const scale = await ns.api.getScaleReport();
        const box = document.getElementById('scale-hint');
        if (box && scale && typeof scale === 'object') {
          const peak = scale.max_nodes || (scale.scale_report && scale.scale_report.max_nodes) || '--';
          box.textContent = `F10 scale peak ${peak}`;
        }
      } catch (_err) {
        const box = document.getElementById('scale-hint');
        if (box) box.textContent = 'F10 scale report unavailable';
      }
    } catch (err) {
      ns.state.setError(err.message || 'Initial API bootstrap failed');
    }
  }

  function startBackgroundSync() {
    if (!snapshotPollTimer) {
      snapshotPollTimer = setInterval(() => {
        refreshSnapshot(false);
      }, 4000);
    }

    if (!clockSyncTimer) {
      clockSyncTimer = setInterval(() => {
        syncServerClock();
      }, 30000);
    }
  }

  function mountShell() {
    const root = document.getElementById('app-root');
    root.innerHTML = `
      <nav class="nav">
        <div class="brand">
          <div class="brand-ring"></div>
          <div>
            <div class="brand-name">DQRMAN</div>
            <div class="brand-sub">Distributed Quantum-Resistant Mesh Auth Network</div>
          </div>
        </div>
        <div class="nav-tabs" id="top-tabs"></div>
        <div class="nav-pills">
          <div id="pill-mesh" class="npill ok"><div class="d"></div>MESH ACTIVE</div>
          <div id="pill-algo" class="npill ok"><div class="d"></div>ML-DSA-65</div>
          <div id="pill-alerts" class="npill warn"><div class="d"></div>0 ALERTS</div>
        </div>
      </nav>

      <main class="main-shell">
        <section id="screen-map" class="screen active" data-screen="map"></section>
        <section id="screen-setup" class="screen" data-screen="setup"></section>
        <section id="screen-attacks" class="screen" data-screen="attacks"></section>
        <section id="screen-events" class="screen" data-screen="events"></section>
        <section id="screen-node" class="screen" data-screen="node"></section>
      </main>

      <div class="sbar">
        <div class="si"><span class="lbl">Protocol</span><span class="val ok">ML-DSA-65</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl">Mesh</span><span id="sb-mesh" class="val ok">OPERATIONAL</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl">Nodes</span><span id="sb-nodes" class="val">0 active</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl">Latency</span><span id="sb-latency" class="val">--</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl">Auth</span><span id="sb-auth" class="val ok">0</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl">Blocked</span><span id="sb-blocked" class="val err">0</span></div>
        <div class="ssep"></div>
        <div class="si"><span class="lbl" id="scale-hint">F10 scale pending</span></div>
        <div class="ssep sr"></div>
        <div class="si"><span id="utc-clock" class="val"></span></div>
      </div>
    `;

    ns.router.mountTabBar(document.getElementById('top-tabs'));
    ns.screen1.mount(document.getElementById('screen-map'));
    ns.screen2.mount(document.getElementById('screen-setup'));
    ns.screen3.mount(document.getElementById('screen-attacks'));
    ns.screen4.mount(document.getElementById('screen-events'));
    ns.screen5.mount(document.getElementById('screen-node'));

    const clock = document.getElementById('utc-clock');
    if (clock) clock.textContent = ns.utils.toUTCClock(serverClockOffsetMs);

    setInterval(() => {
      const clock = document.getElementById('utc-clock');
      if (clock) clock.textContent = ns.utils.toUTCClock(serverClockOffsetMs);
    }, 1000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    mountShell();
    ns.socketClient.connect();
    ns.state.subscribe((state, reason) => {
      if (reason === 'events') classifyCounters(state.events);
      if (reason === 'ws' && !state.transport.wsConnected) {
        refreshSnapshot(true);
      }
      updateGlobalStatus(state);
    });
    initialLoad();
    startBackgroundSync();
  });
})(window.DQRMAN);
