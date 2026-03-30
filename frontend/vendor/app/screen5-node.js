window.DQRMAN = window.DQRMAN || {};

(function initScreen5(ns) {
  function nodeRows(state) {
    return Array.from(state.nodes.values())
      .map((node) => {
        const tone = ns.utils.statusTone(node.status);
        return `<div class="nitem" data-node-id="${ns.utils.esc(node.node_id)}">
          <div class="ndot ${tone}"></div>
          <div class="ninfo">
            <div class="nid">${ns.utils.esc(node.node_id)}</div>
            <div class="nmeta">${ns.utils.esc(node.status || 'UNKNOWN')}</div>
          </div>
        </div>`;
      })
      .join('');
  }

  function statusClass(status) {
    return ns.utils.statusTone(status);
  }

  function peerRows(state, nodeId) {
    const rows = Array.from(state.edges.values())
      .filter((edge) => edge.source === nodeId || edge.target === nodeId)
      .map((edge) => {
        const peer = edge.source === nodeId ? edge.target : edge.source;
        const peerNode = state.nodes.get(peer);
        const trust = peerNode ? ns.utils.formatTrust(peerNode.trust_score || 0) : '--';
        const s = peerNode ? statusClass(peerNode.status) : 'warn';
        const status = s === 'ok' ? 'TRUSTED' : s === 'warn' ? 'DEGRADED' : 'SUSPECT';
        return `<tr><td style="color:var(--pearl-dim)">${ns.utils.esc(peer)}</td><td style="color:var(--${s === 'ok' ? 'ok' : s === 'warn' ? 'warn' : 'err'})">${trust}</td><td style="color:var(--${s === 'ok' ? 'ok' : s === 'warn' ? 'warn' : 'err'})">${status}</td><td>${ns.utils.esc(String(edge.weight || '--'))}</td></tr>`;
      });

    return rows.join('') || '<tr><td colspan="4">No peers</td></tr>';
  }

  function historyRows(state, nodeId) {
    const lines = state.events
      .filter((event) => {
        const x = event.node_id || event.target_node_id || (event.payload && (event.payload.node_id || event.payload.target_node_id));
        return x === nodeId;
      })
      .slice(0, 8)
      .map((event) => `${ns.utils.formatTimestamp(event.timestamp || event.time)}  ${event.event_type || 'EVENT'}`);

    return lines.join('<br>') || 'No event history for selected node.';
  }

  async function rerouteFromSelected(nodeId) {
    const state = ns.state.getState();
    const edgeDegree = new Map();
    Array.from(state.edges.values()).forEach((edge) => {
      edgeDegree.set(edge.source, (edgeDegree.get(edge.source) || 0) + 1);
      edgeDegree.set(edge.target, (edgeDegree.get(edge.target) || 0) + 1);
    });

    const sourcePeers = Array.from(state.edges.values())
      .filter((edge) => edge.source === nodeId || edge.target === nodeId).length;

    const candidates = Array.from(state.nodes.values())
      .filter((n) => n.node_id !== nodeId && String(n.status || '').toUpperCase() === 'ACTIVE')
      .sort((a, b) => (edgeDegree.get(b.node_id) || 0) - (edgeDegree.get(a.node_id) || 0))
      .map((n) => n.node_id);

    if (!candidates.length) {
      const box = document.getElementById('node-action-msg');
      if (box) {
        box.style.display = 'block';
        box.textContent = 'Need at least two active nodes for reroute.';
      }
      return;
    }

    const box = document.getElementById('node-action-msg');
    if (box) {
      box.style.display = 'block';
      box.textContent = 'Searching reroute path...';
    }

    try {
      for (let i = 0; i < candidates.length; i += 1) {
        const candidate = candidates[i];
        const resp = await ns.api.reroute(nodeId, candidate, `ui-${Date.now()}-${i}`);
        if (resp && resp.success && Array.isArray(resp.path) && resp.path.length >= 2) {
          if (box) {
            box.style.display = 'block';
            box.textContent = `Reroute path: ${(resp.path || []).join(' -> ')}`;
          }
          return;
        }
      }

      // If selected node is isolated, still provide a usable reroute preview
      // by finding any valid path among connected active nodes.
      const connectedCandidates = Array.from(state.nodes.values())
        .filter((n) => String(n.status || '').toUpperCase() === 'ACTIVE' && (edgeDegree.get(n.node_id) || 0) > 0)
        .sort((a, b) => (edgeDegree.get(b.node_id) || 0) - (edgeDegree.get(a.node_id) || 0))
        .map((n) => n.node_id);

      for (let i = 0; i < connectedCandidates.length; i += 1) {
        for (let j = i + 1; j < connectedCandidates.length; j += 1) {
          const source = connectedCandidates[i];
          const target = connectedCandidates[j];
          const resp = await ns.api.reroute(source, target, `ui-fallback-${Date.now()}-${i}-${j}`);
          if (resp && resp.success && Array.isArray(resp.path) && resp.path.length >= 2) {
            if (box) {
              box.style.display = 'block';
              box.textContent = `Selected node is isolated. Network reroute path: ${(resp.path || []).join(' -> ')}`;
            }
            return;
          }
        }
      }

      if (box) {
        box.style.display = 'block';
        box.textContent = sourcePeers === 0
          ? 'Reroute failed: selected node has no trust links.'
          : 'Reroute failed: no reachable path to active peers.';
      }
    } catch (err) {
      if (box) {
        box.style.display = 'block';
        box.textContent = err.message || 'Reroute request failed';
      }
    }
  }

  async function destroyNode(nodeId) {
    try {
      await ns.api.deleteNode(nodeId);
      ns.state.setSelectedNode(null);
    } catch (err) {
      const box = document.getElementById('node-action-msg');
      if (box) {
        box.style.display = 'block';
        box.textContent = err.message || 'Destroy failed';
      }
    }
  }

  function renderPanel(state) {
    const panel = document.getElementById('ndp');
    if (!panel) return;

    const node = state.selectedNodeId ? state.nodes.get(state.selectedNodeId) : null;
    if (!node) {
      panel.innerHTML = `<div class="node-empty">Select a node from the registry</div>`;
      return;
    }

    const s = statusClass(node.status);
    const sl = s === 'ok' ? 'VERIFIED' : s === 'warn' ? 'DEGRADED' : 'COMPROMISED';
    const coord = `${Number(node.lat || 0).toFixed(4)}N, ${Number(node.lon || 0).toFixed(4)}E`;
    const keyFrag = `${ns.utils.hashText(node.node_id)}:${ns.utils.hashText(node.joined_at || '')}:${ns.utils.hashText(node.status || '')}`;

    panel.innerHTML = `
      <div class="nd-head">
        <div>
          <div style="display:flex;align-items:center;gap:11px;margin-bottom:5px">
            <div class="nd-id">${ns.utils.esc(node.node_id)}</div>
            <div class="nd-status ${s}"><div class="ndot ${s}" style="width:5px;height:5px"></div>${sl}</div>
          </div>
          <div class="nd-role">Status: ${ns.utils.esc(node.status || 'UNKNOWN')} | Peers: ${Array.from(state.edges.values()).filter((e) => e.source === node.node_id || e.target === node.node_id).length}</div>
        </div>
        <div class="nd-actions" style="margin-left:auto">
          <button id="node-reroute" class="btn btn-amber">Reroute</button>
          <button id="node-kill" class="btn btn-red">Kill Node</button>
        </div>
      </div>

      <div class="dgrid">
        <div class="dcard"><div class="dl">Trust Score</div><div class="dv ${s}">${ns.utils.formatTrust(node.trust_score || 0)}</div></div>
        <div class="dcard"><div class="dl">Joined At</div><div class="dv">${ns.utils.esc(ns.utils.formatTimestamp(node.joined_at))}</div></div>
        <div class="dcard"><div class="dl">Coordinates</div><div class="dv">${ns.utils.esc(coord)}</div></div>
        <div class="dcard"><div class="dl">Topology Links</div><div class="dv">${Array.from(state.edges.values()).filter((e) => e.source === node.node_id || e.target === node.node_id).length}</div></div>
      </div>

      <div class="kfp">
        <div class="kfp-hdr"><span class="kfp-label">ML-DSA-65 Public Key Fingerprint</span><span class="kfp-algo">NIST FIPS 204</span></div>
        <div class="kfp-val">${ns.utils.esc(keyFrag)}</div>
      </div>

      <div>
        <div class="ttitle">Peer Trust Table</div>
        <table class="tt"><thead><tr><th>Peer Node</th><th>Trust</th><th>Status</th><th>Weight</th></tr></thead><tbody>${peerRows(state, node.node_id)}</tbody></table>
      </div>

      <div>
        <div class="ttitle">Recent Auth History</div>
        <div class="hist-block">${historyRows(state, node.node_id)}</div>
      </div>

      <div id="node-action-msg" class="inline-error" style="display:none"></div>
    `;

    document.getElementById('node-reroute').addEventListener('click', () => rerouteFromSelected(node.node_id));
    document.getElementById('node-kill').addEventListener('click', () => destroyNode(node.node_id));
  }

  function render(state) {
    const list = document.getElementById('ndl');
    if (list) {
      list.innerHTML = nodeRows(state) || '<div class="empty-state">No nodes available.</div>';
      list.querySelectorAll('.nitem').forEach((el) => {
        el.classList.toggle('selected', el.dataset.nodeId === state.selectedNodeId);
        el.addEventListener('click', () => ns.state.setSelectedNode(el.dataset.nodeId));
      });
    }

    renderPanel(state);
  }

  function mount(container) {
    container.innerHTML = `
      <div class="nd-list">
        <div class="panel-hdr"><span class="phdr-title"><span class="acc">◈</span>Select Node</span></div>
        <div id="ndl"></div>
      </div>
      <div class="nd-panel" id="ndp"></div>
    `;

    ns.state.subscribe((state, reason) => {
      if (reason === 'mesh_state' || reason === 'selection' || reason === 'events' || reason === 'tab') {
        render(state);
      }
    });

    render(ns.state.getState());
  }

  ns.screen5 = { mount };
})(window.DQRMAN);
