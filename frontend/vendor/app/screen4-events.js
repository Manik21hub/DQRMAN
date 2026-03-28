window.DQRMAN = window.DQRMAN || {};

(function initScreen4(ns) {
  let filter = 'all';
  let search = '';

  function severityClass(level) {
    if (level === 'Alert') return 'err';
    if (level === 'Warning') return 'warn';
    return 'info';
  }

  function pickNode(event) {
    return event.node_id || event.target_node_id || (event.payload && (event.payload.node_id || event.payload.target_node_id)) || '--';
  }

  function rowLatency(event) {
    const d = Number(event.duration_ms || (event.payload && event.payload.duration_ms));
    return Number.isFinite(d) ? `${Math.round(d)}ms` : '--';
  }

  function match(event) {
    const type = String(event.event_type || '').toLowerCase();
    if (filter !== 'all' && !type.includes(filter)) return false;
    if (!search) return true;
    return JSON.stringify(event).toLowerCase().includes(search);
  }

  function render(state) {
    const items = state.events.filter(match).slice(0, 500);
    const tbody = document.getElementById('ltbody');
    if (!tbody) return;

    tbody.innerHTML = items.map((event) => {
      const sev = ns.utils.severityFromEvent(event.event_type);
      const sevCls = severityClass(sev);
      const node = pickNode(event);
      const peer = event.peer_node_id || (event.payload && event.payload.peer_node_id) || '--';
      const hash = ns.utils.hashText(`${event.event_type || ''}${node}${event.timestamp || event.time || ''}`);
      const msg = event.event_type || 'EVENT';
      return `<tr>
        <td style="color:var(--pearl-faint)">${ns.utils.esc(ns.utils.formatTimestamp(event.timestamp || event.time))}</td>
        <td><span class="lsev ${sevCls}">${sev === 'Alert' ? 'ATTACK' : sev.toUpperCase()}</span></td>
        <td style="color:var(--pearl-dim)">${ns.utils.esc(node)}</td>
        <td style="color:var(--pearl-dim)">${ns.utils.esc(peer)}</td>
        <td style="color:var(--pearl-dim)">${ns.utils.esc(msg)}</td>
        <td style="color:var(--pearl-faint)">${ns.utils.esc(hash)}</td>
        <td style="color:var(--pearl-dim)">${ns.utils.esc(rowLatency(event))}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="7">No matching events.</td></tr>';

    const count = document.getElementById('lcnt');
    if (count) count.textContent = String(items.length);
  }

  function setFilter(value) {
    filter = value;
    document.querySelectorAll('.lfbtn[data-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.filter === value);
    });
    render(ns.state.getState());
  }

  function mount(container) {
    container.innerHTML = `
      <div class="log-toolbar">
        <input id="lsrch" class="log-search" placeholder="Search node ID, event type..." />
        <button class="lfbtn active" data-filter="all">ALL</button>
        <button class="lfbtn" data-filter="auth">AUTH</button>
        <button class="lfbtn" data-filter="warn">WARN</button>
        <button class="lfbtn" data-filter="replay">ATTACK</button>
        <button class="lfbtn" data-filter="info">INFO</button>
        <div class="log-cnt"><span id="lcnt">0</span> events</div>
      </div>
      <div class="log-wrap">
        <table class="ltable">
          <thead>
            <tr>
              <th style="width:165px">Timestamp</th>
              <th style="width:72px">Sev</th>
              <th style="width:115px">Node</th>
              <th style="width:110px">Peer</th>
              <th>Event</th>
              <th style="width:100px">Sig Hash</th>
              <th style="width:64px">Latency</th>
            </tr>
          </thead>
          <tbody id="ltbody"></tbody>
        </table>
      </div>
    `;

    document.getElementById('lsrch').addEventListener('input', (e) => {
      search = String(e.target.value || '').trim().toLowerCase();
      render(ns.state.getState());
    });

    document.querySelectorAll('.lfbtn[data-filter]').forEach((btn) => {
      btn.addEventListener('click', () => setFilter(btn.dataset.filter));
    });

    ns.state.subscribe((state, reason) => {
      if (reason === 'events' || reason === 'mesh_state' || reason === 'ws') render(state);
    });

    render(ns.state.getState());
  }

  ns.screen4 = { mount };
})(window.DQRMAN);
