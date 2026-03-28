window.DQRMAN = window.DQRMAN || {};

(function startApp(ns) {
  let authCount = 0;
  let blockedCount = 0;

  function classifyCounters(events) {
    authCount = events.filter((evt) => String(evt.event_type || '').toUpperCase().includes('AUTH')).length;
    blockedCount = events.filter((evt) => {
      const e = String(evt.event_type || '').toUpperCase();
      return e.includes('REPLAY') || e.includes('SPOOF') || e.includes('JAMMING') || e.includes('DESTROYED');
    }).length;
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
      wsPill.className = `npill ${state.transport.wsConnected ? 'ok' : 'err'}`;
      wsPill.innerHTML = `<div class="d"></div>${state.transport.wsConnected ? `${blockedCount} ALERTS` : 'WS DOWN'}`;
    }
  }

  function updateHealthBadge(ok) {
    const el = document.getElementById('pill-mesh');
    if (!el) return;
    el.className = `npill ${ok ? 'ok' : 'err'}`;
    el.innerHTML = `<div class="d"></div>${ok ? 'MESH ACTIVE' : 'SERVICE DOWN'}`;
  }

  async function initialLoad() {
    try {
      const [healthResp, nodesResp, eventsResp] = await Promise.all([
        ns.api.getHealth(),
        ns.api.getNodes(),
        ns.api.getEvents(),
      ]);

      const ok = String(healthResp && healthResp.status || '').toLowerCase() === 'ok';
      updateHealthBadge(ok);

      const nodes = Array.isArray(nodesResp && nodesResp.nodes) ? nodesResp.nodes : [];
      const events = Array.isArray(eventsResp)
        ? eventsResp
        : Array.isArray(eventsResp && eventsResp.events)
          ? eventsResp.events
          : [];

      classifyCounters(events);
      ns.state.setMeshSnapshot(nodes, [], nodesResp && nodesResp.stats ? nodesResp.stats : null);
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

    setInterval(() => {
      const clock = document.getElementById('utc-clock');
      if (clock) clock.textContent = ns.utils.toUTCClock();
    }, 1000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    mountShell();
    ns.socketClient.connect();
    ns.state.subscribe((state, reason) => {
      if (reason === 'events') classifyCounters(state.events);
      updateGlobalStatus(state);
    });
    initialLoad();
  });
})(window.DQRMAN);
