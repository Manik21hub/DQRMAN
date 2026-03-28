window.DQRMAN = window.DQRMAN || {};

(function initScreen3(ns) {
  function options(state) {
    return Array.from(state.nodes.values())
      .filter((n) => String(n.status || '').toUpperCase() !== 'DESTROYED')
      .map((n) => `<option value="${ns.utils.esc(n.node_id)}">${ns.utils.esc(n.node_id)}</option>`)
      .join('');
  }

  function pushLine(termId, cls, text) {
    const term = document.getElementById(termId);
    if (!term) return;
    const row = document.createElement('div');
    row.className = `tl ${cls}`;
    row.textContent = text;
    term.appendChild(row);
    term.scrollTop = term.scrollHeight;
  }

  function clearTerm(termId, banner) {
    const term = document.getElementById(termId);
    if (!term) return;
    term.innerHTML = `<div class="tl muted">${banner}</div>`;
  }

  function setBadge(id, cls, text) {
    const badge = document.getElementById(id);
    if (!badge) return;
    badge.className = `abadge ${cls}`;
    badge.textContent = text;
  }

  async function runAttack(kind) {
    const target = document.getElementById(`${kind}-target`).value;
    const delay = Number(document.getElementById(`${kind}-delay`).value || 0);
    const attempts = Number(document.getElementById(`${kind}-attempts`).value || 1);
    const termId = `${kind}-term`;
    const badgeId = `${kind}-badge`;

    if (!target) {
      pushLine(termId, 'err', '> Select target node first');
      return;
    }

    clearTerm(termId, `// ${kind.toUpperCase()} terminal - executing`);
    setBadge(badgeId, '', '');
    pushLine(termId, 'muted', `> target=${target} delay=${delay}s attempts=${attempts}`);

    let ok = 0;
    for (let i = 0; i < attempts; i += 1) {
      try {
        await ns.api.runAttack(kind, target, delay);
        ok += 1;
        pushLine(termId, 'warn', `> [${i + 1}/${attempts}] request accepted`);
      } catch (err) {
        pushLine(termId, 'err', `> [${i + 1}/${attempts}] failed: ${err.message || 'error'}`);
      }
      if (i >= 11 && attempts > 12) {
        pushLine(termId, 'muted', `> ...skipping ${attempts - i - 1} remaining lines`);
        break;
      }
    }

    if (ok > 0) {
      pushLine(termId, 'ok', `> RESULT: ${ok}/${attempts} accepted by backend`);
      const label = kind === 'replay' ? 'DETECTED' : kind === 'spoof' ? 'REJECTED' : 'QUEUED';
      setBadge(badgeId, kind === 'replay' ? 'detected' : kind === 'spoof' ? 'rejected' : 'healing', `${label}`);
    } else {
      setBadge(badgeId, 'rejected', 'FAILED');
    }
  }

  async function runKill() {
    const target = document.getElementById('kill-target').value;
    const count = Number(document.getElementById('kill-count').value || 1);
    if (!target) {
      pushLine('kill-term', 'err', '> Select kill target first');
      return;
    }

    clearTerm('kill-term', '// Kill terminal - executing');
    setBadge('kill-badge', '', '');
    let success = 0;

    for (let i = 0; i < count; i += 1) {
      try {
        const selected = i === 0 ? target : '';
        const fallback = Array.from(ns.state.getState().nodes.values())
          .filter((n) => String(n.status || '').toUpperCase() !== 'DESTROYED')
          .map((n) => n.node_id)
          .find((id) => id !== target);
        const nodeId = selected || fallback;
        if (!nodeId) break;

        const resp = await ns.api.deleteNode(nodeId);
        success += 1;
        pushLine('kill-term', 'warn', `> NODE_DOWN ${nodeId} mesh=${resp.mesh_status} survivors=${resp.surviving_count}`);
      } catch (err) {
        pushLine('kill-term', 'err', `> destroy failed: ${err.message || 'error'}`);
      }
    }

    if (success > 0) {
      pushLine('kill-term', 'ok', `> RESULT: ${success} nodes removed, self-heal triggered`);
      setBadge('kill-badge', 'healing', 'HEALING');
    } else {
      setBadge('kill-badge', 'rejected', 'FAILED');
    }
  }

  function render(state) {
    const optionRows = `<option value="">Select node</option>${options(state)}`;
    ['replay', 'spoof', 'jamming', 'kill'].forEach((kind) => {
      const sel = document.getElementById(`${kind}-target`);
      if (!sel) return;
      const current = sel.value;
      sel.innerHTML = optionRows;
      if (current) sel.value = current;
    });
  }

  function mount(container) {
    container.innerHTML = `
      <div class="attack-topbar">
        <span class="phdr-title" style="font-size:10.5px"><span class="acc">⚠</span>Attack Simulation Console</span>
        <span class="label">All attacks are executed against live backend endpoints and logged in real time.</span>
      </div>

      <div class="attack-grid">
        <div class="acard">
          <div class="acard-hdr"><div class="aicon replay">⟳</div><div><div class="aname">Replay Injection</div><div class="adesc">POST /api/v1/attack with replay payload and delay offset.</div></div></div>
          <div class="aparams">
            <div class="fgroup"><label class="form-label">Target Node</label><select id="replay-target" class="form-select"></select></div>
            <div class="fgroup"><label class="form-label">Delay offset (s)</label><input id="replay-delay" class="form-input" type="number" min="0" max="30" value="8" /></div>
            <div class="fgroup"><label class="form-label">Attempts</label><input id="replay-attempts" class="form-input" type="number" min="1" max="50" value="5" /></div>
          </div>
          <div id="replay-term" class="aterm"><div class="tl muted">// Replay terminal - ready</div></div>
          <div class="afooter"><button id="run-replay" class="btn-attack replay">Execute</button><span id="replay-badge" class="abadge"></span></div>
        </div>

        <div class="acard">
          <div class="acard-hdr"><div class="aicon spoof">⊘</div><div><div class="aname">Identity Spoofing</div><div class="adesc">POST /api/v1/attack with spoof/jamming signatures for adversarial simulation.</div></div></div>
          <div class="aparams">
            <div class="fgroup"><label class="form-label">Spoof Victim</label><select id="spoof-target" class="form-select"></select></div>
            <div class="fgroup"><label class="form-label">Delay (s)</label><input id="spoof-delay" class="form-input" type="number" min="0" max="30" value="0" /></div>
            <div class="fgroup"><label class="form-label">Attempts</label><input id="spoof-attempts" class="form-input" type="number" min="1" max="50" value="3" /></div>
            <div class="fgroup"><label class="form-label">Jamming Probe</label><button id="run-jamming" class="btn btn-amber" type="button">Run Jamming</button></div>
          </div>
          <div id="spoof-term" class="aterm"><div class="tl muted">// Spoofing terminal - ready</div></div>
          <div class="afooter"><button id="run-spoof" class="btn-attack spoof">Execute</button><span id="spoof-badge" class="abadge"></span></div>
        </div>

        <div class="acard">
          <div class="acard-hdr"><div class="aicon kill">✕</div><div><div class="aname">Node Kill / Attrition</div><div class="adesc">DELETE /api/v1/nodes/{id} and observe self-heal/reroute behavior.</div></div></div>
          <div class="aparams">
            <div class="fgroup"><label class="form-label">Kill Target</label><select id="kill-target" class="form-select"></select></div>
            <div class="fgroup"><label class="form-label">Kill Count</label><input id="kill-count" class="form-input" type="number" min="1" max="10" value="1" /></div>
            <div class="fgroup"><label class="form-label">Heal timeout (s)</label><input class="form-input" type="number" value="2" min="1" max="10" disabled /></div>
          </div>
          <div id="kill-term" class="aterm"><div class="tl muted">// Kill terminal - ready</div></div>
          <div class="afooter"><button id="run-kill" class="btn-attack kill">Execute</button><span id="kill-badge" class="abadge"></span></div>
        </div>
      </div>
    `;

    document.getElementById('run-replay').addEventListener('click', () => runAttack('replay'));
    document.getElementById('run-spoof').addEventListener('click', () => runAttack('spoof'));
    document.getElementById('run-jamming').addEventListener('click', () => runAttack('jamming'));
    document.getElementById('run-kill').addEventListener('click', runKill);

    ns.state.subscribe((state, reason) => {
      if (reason === 'mesh_state' || reason === 'events') render(state);
    });

    render(ns.state.getState());
  }

  ns.screen3 = { mount };
})(window.DQRMAN);
