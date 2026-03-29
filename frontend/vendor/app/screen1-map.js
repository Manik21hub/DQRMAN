window.DQRMAN = window.DQRMAN || {};

(function initScreen1(ns) {
  let map;
  let markers;
  let links;
  const markerById = new Map();
  const linkByKey = new Map();

  function tone(status) {
    return ns.utils.statusTone(status);
  }

  function nodeLabel(nodeId) {
    const raw = String(nodeId || '---');
    const digits = raw.replace(/\D+/g, '');
    if (digits) return digits.slice(-3).padStart(3, '0');
    return raw.slice(-3).toUpperCase();
  }

  function markerHtml(node, selected) {
    const t = tone(node.status);
    return `<div class="mesh-node mesh-node-${t}${selected ? ' is-selected' : ''}" role="button" aria-label="${ns.utils.esc(node.node_id)}">
      <div class="mesh-node-halo"></div>
      <div class="mesh-node-core">${ns.utils.esc(nodeLabel(node.node_id))}</div>
    </div>`;
  }

  function ensureMap() {
    if (map) return;
    map = window.L.map('leafmap', { center: [28.6139, 77.209], zoom: 13, zoomControl: false, attributionControl: false });
    window.L.tileLayer('/osm-tiles/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
    window.L.control.zoom({ position: 'bottomright' }).addTo(map);
    markers = window.L.layerGroup().addTo(map);
    links = window.L.layerGroup().addTo(map);
  }

  function renderNodeList(state) {
    const nodes = Array.from(state.nodes.values());
    const wrap = document.getElementById('nlist');
    if (!wrap) return;

    const rows = nodes.map((node) => {
      const t = tone(node.status);
      const selected = state.selectedNodeId === node.node_id ? 'selected' : '';
      return `<div class="nitem ${selected} ${t === 'err' ? 'compromised' : ''}" data-node-id="${ns.utils.esc(node.node_id)}">
        <div class="ndot ${t}"></div>
        <div class="ninfo">
          <div class="nid">${ns.utils.esc(node.node_id)}</div>
          <div class="nmeta">${ns.utils.esc(node.status || 'UNKNOWN')} | Trust ${ns.utils.formatTrust(node.trust_score || 0)}</div>
        </div>
      </div>`;
    });

    wrap.innerHTML = rows.join('') || '<div class="empty-state">No nodes online.</div>';

    wrap.querySelectorAll('.nitem').forEach((el) => {
      el.addEventListener('click', () => {
        ns.state.setSelectedNode(el.dataset.nodeId);
        ns.router.activate('node');
      });
    });
  }

  function renderMap(state) {
    ensureMap();
    const nodes = Array.from(state.nodes.values()).filter((node) => Number.isFinite(Number(node.lat)) && Number.isFinite(Number(node.lon)));
    const edges = Array.from(state.edges.values());

    const activeIds = new Set(nodes.map((node) => node.node_id));
    markerById.forEach((marker, nodeId) => {
      if (!activeIds.has(nodeId)) {
        markers.removeLayer(marker);
        markerById.delete(nodeId);
      }
    });

    nodes.forEach((node) => {
      const selected = state.selectedNodeId === node.node_id;
      const icon = window.L.divIcon({
        className: 'mesh-node-icon',
        html: markerHtml(node, selected),
        iconSize: [48, 48],
        iconAnchor: [24, 24],
      });

      let marker = markerById.get(node.node_id);
      if (!marker) {
        marker = window.L.marker([Number(node.lat), Number(node.lon)], { icon });
        marker.addTo(markers);
        marker.on('click', () => {
          ns.state.setSelectedNode(node.node_id);
          ns.router.activate('node');
        });
        markerById.set(node.node_id, marker);
      } else {
        marker.setLatLng([Number(node.lat), Number(node.lon)]);
        marker.setIcon(icon);
      }
    });

    const keepLinks = new Set();
    edges.forEach((edge) => {
      const a = state.nodes.get(edge.source);
      const b = state.nodes.get(edge.target);
      if (!a || !b) return;
      if (!Number.isFinite(Number(a.lat)) || !Number.isFinite(Number(a.lon))) return;
      if (!Number.isFinite(Number(b.lat)) || !Number.isFinite(Number(b.lon))) return;

      const key = `${edge.source}|${edge.target}`;
      keepLinks.add(key);

      const ta = tone(a.status);
      const tb = tone(b.status);
      const weight = Number(edge.weight || 0);
      const c = ta === 'err' || tb === 'err'
        ? '#5b657a'
        : ta === 'warn' || tb === 'warn' || weight < 0.55
          ? '#D4A847'
          : '#52B788';
      // Keep mesh links visibly dotted for all node-to-node connections.
      const dash = ta === 'err' || tb === 'err' ? '1 10' : '1 8';

      let line = linkByKey.get(key);
      if (!line) {
        line = window.L.polyline([[Number(a.lat), Number(a.lon)], [Number(b.lat), Number(b.lon)]], {
          color: c,
          weight: 1.7,
          opacity: ta === 'err' || tb === 'err' ? 0.6 : 0.75,
          dashArray: dash,
          lineCap: 'round',
          lineJoin: 'round',
        }).addTo(links);
        linkByKey.set(key, line);
      } else {
        line.setLatLngs([[Number(a.lat), Number(a.lon)], [Number(b.lat), Number(b.lon)]]);
        line.setStyle({
          color: c,
          dashArray: dash,
          opacity: ta === 'err' || tb === 'err' ? 0.6 : 0.75,
        });
      }
    });

    linkByKey.forEach((line, key) => {
      if (!keepLinks.has(key)) {
        links.removeLayer(line);
        linkByKey.delete(key);
      }
    });

    if (nodes.length) {
      const bounds = window.L.latLngBounds(nodes.map((n) => [Number(n.lat), Number(n.lon)]));
      if (bounds.isValid()) map.fitBounds(bounds.pad(0.28));
    }
  }

  function renderEventFeed(state) {
    const feed = document.getElementById('efeed');
    if (!feed) return;
    const rows = state.events.slice(0, 120).map((event) => {
      const sev = ns.utils.severityFromEvent(event.event_type);
      const cls = sev === 'Alert' ? 'err' : sev === 'Warning' ? 'warn' : 'info';
      const target = event.target_node_id || event.node_id || (event.payload && (event.payload.target_node_id || event.payload.node_id)) || '--';
      return `<div class="eitem ${cls}">
        <div class="etime">${ns.utils.esc(ns.utils.formatTimestamp(event.timestamp || event.time))}</div>
        <div class="emsg">${ns.utils.esc(event.event_type || 'EVENT')} • ${ns.utils.esc(target)}</div>
      </div>`;
    });
    feed.innerHTML = rows.join('') || '<div class="empty-state">No events yet.</div>';
  }

  function renderMetrics(state) {
    const total = state.nodes.size;
    const active = state.stats.active;
    const destroyed = state.stats.destroyed;
    const authEvents = state.events.filter((evt) => String(evt.event_type || '').toUpperCase().includes('AUTH')).length;
    const blocked = state.events.filter((evt) => {
      const e = String(evt.event_type || '').toUpperCase();
      return e.includes('REPLAY') || e.includes('SPOOF') || e.includes('JAMMING') || e.includes('DESTROYED');
    }).length;
    const trustScore = Math.max(0, Math.min(100, Math.round(state.stats.healthPercent || 0)));

    const put = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    put('lc', `${active} / ${total}`);
    put('sa', String(active));
    put('se', String(state.edges.size));
    put('sauth', authEvents.toLocaleString());
    put('satk', String(blocked));
    put('tpct', `${trustScore}%`);
    const fill = document.getElementById('tbar');
    if (fill) fill.style.width = `${trustScore}%`;

    const latency = state.stats.avgLatency > 0 ? Math.round(state.stats.avgLatency) : '--';
    const lat = document.getElementById('mo-lat');
    if (lat) lat.innerHTML = `${latency}<span style="font-size:9px">ms</span>`;

    put('mo-nd', String(active));
  }

  function render(state) {
    renderNodeList(state);
    renderMap(state);
    renderEventFeed(state);
    renderMetrics(state);
  }

  function mount(container) {
    container.innerHTML = `
      <div class="map-sl">
        <div class="panel-hdr"><span class="phdr-title"><span class="acc">◈</span>Node Registry</span><span id="lc" class="mono label">0 / 0</span></div>
        <div class="mstats">
          <div class="mstat"><div id="sa" class="v ok">0</div><div class="l">Active Nodes</div></div>
          <div class="mstat"><div id="se" class="v">0</div><div class="l">Trust Edges</div></div>
          <div class="mstat"><div id="sauth" class="v ok">0</div><div class="l">Auth Events</div></div>
          <div class="mstat"><div id="satk" class="v err">0</div><div class="l">Blocked</div></div>
        </div>
        <div class="tbar-wrap">
          <div class="tbar-labels"><span>Mesh Trust Score</span><span id="tpct" style="color:var(--ok)">0%</span></div>
          <div class="tbar-track"><div id="tbar" class="tbar-fill" style="width:0%"></div></div>
        </div>
        <div id="nlist" class="nlist"></div>
      </div>

      <div class="map-center">
        <div class="map-ovh">
          <div class="mosm"><div id="mo-lat" class="n">--<span style="font-size:9px">ms</span></div><div class="l">Avg Latency</div></div>
          <div class="mos-sep"></div>
          <div class="mosm"><div class="n ok">100%</div><div class="l">Replay Det</div></div>
          <div class="mos-sep"></div>
          <div class="mosm"><div class="n">99.8%</div><div class="l">Uptime</div></div>
          <div class="mos-sep"></div>
          <div class="mosm"><div id="mo-nd" class="n warn">0</div><div class="l">Nodes</div></div>
        </div>
        <div id="leafmap"></div>
      </div>

      <div class="map-sr">
        <div class="panel-hdr"><span class="phdr-title"><span class="acc">◈</span>Live Events</span><span class="label">real-time</span></div>
        <div id="efeed" class="efeed"></div>
      </div>
    `;

    ns.state.subscribe((state, reason) => {
      if (reason === 'mesh_state' || reason === 'events' || reason === 'selection' || reason === 'ws') {
        render(state);
      }
    });

    render(ns.state.getState());
  }

  ns.screen1 = { mount };
})(window.DQRMAN);
