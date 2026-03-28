window.DQRMAN = window.DQRMAN || {};

(function initScreen2(ns) {
  const cfg = {
    node_count: 12,
    center_lat: 28.65,
    center_lon: 77.30,
    spread_m: 500,
    distribution: 'random',
    sync_window: 5,
    trust_decay: 30,
  };

  let setupMap;
  let overlay;

  function isMissingSimulationControl(err) {
    const msg = String((err && err.message) || '');
    return msg.includes('HTTP 404') || msg.includes('HTTP 405');
  }

  function setMsg(text, kind) {
    const box = document.getElementById('setup-msg');
    if (!box) return;
    if (!text) {
      box.style.display = 'none';
      box.textContent = '';
      box.className = 'inline-error';
      return;
    }
    box.style.display = 'block';
    box.textContent = text;
    box.className = `inline-error ${kind === 'ok' ? 'setup-ok' : kind === 'info' ? 'setup-info' : ''}`;
  }

  function drawPreview() {
    if (!setupMap || !overlay) return;
    overlay.clearLayers();

    const center = window.L.latLng(cfg.center_lat, cfg.center_lon);
    const radius = Math.max(100, Number(cfg.spread_m));
    const bounds = center.toBounds(radius * 4);

    window.L.rectangle(bounds, {
      color: '#D4A847',
      weight: 1.5,
      fillColor: '#D4A847',
      fillOpacity: 0.07,
      dashArray: '6 4',
    }).addTo(overlay);

    const nodes = Math.min(64, cfg.node_count);
    const side = Math.max(2, Math.ceil(Math.sqrt(nodes)));
    for (let i = 0; i < nodes; i += 1) {
      const row = Math.floor(i / side);
      const col = i % side;
      let lat;
      let lon;
      if (cfg.distribution === 'grid') {
        lat = bounds.getSouth() + (bounds.getNorth() - bounds.getSouth()) * ((row + 0.5) / side);
        lon = bounds.getWest() + (bounds.getEast() - bounds.getWest()) * ((col + 0.5) / side);
      } else {
        const seed = i * 97 + 13;
        const rx = ((seed % 89) + 0.5) / 89;
        const ry = (((seed * 7) % 83) + 0.5) / 83;
        lat = bounds.getSouth() + (bounds.getNorth() - bounds.getSouth()) * ry;
        lon = bounds.getWest() + (bounds.getEast() - bounds.getWest()) * rx;
      }
      window.L.circleMarker([lat, lon], {
        radius: 3,
        color: '#52B788',
        fillColor: '#52B788',
        fillOpacity: 0.95,
        weight: 1,
      }).addTo(overlay);
    }

    setupMap.fitBounds(bounds.pad(0.2));
  }

  function syncUiValues() {
    const setText = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(value);
    };
    setText('nc-v', cfg.node_count);
    setText('td-v', `${cfg.trust_decay}%`);

    const setValue = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = String(value);
    };
    setValue('setup-node-count', cfg.node_count);
    setValue('setup-center-lat', cfg.center_lat);
    setValue('setup-center-lon', cfg.center_lon);
    setValue('setup-spread', cfg.spread_m);
    setValue('setup-sync-window', cfg.sync_window);
    setValue('setup-trust-decay', cfg.trust_decay);
  }

  async function refreshFromBackend() {
    try {
      const payload = await ns.api.getSimulationConfig();
      if (payload && payload.config) {
        cfg.node_count = Number(payload.config.node_count || cfg.node_count);
        cfg.center_lat = Number(payload.config.center_lat || cfg.center_lat);
        cfg.center_lon = Number(payload.config.center_lon || cfg.center_lon);
        cfg.spread_m = Number(payload.config.spread_m || cfg.spread_m);
      }
      syncUiValues();
      drawPreview();
    } catch (_err) {
      // Optional bootstrap; keep defaults.
    }
  }

  async function saveConfig() {
    setMsg('', '');
    try {
      await ns.api.setSimulationConfig({
        node_count: cfg.node_count,
        center_lat: cfg.center_lat,
        center_lon: cfg.center_lon,
        spread_m: cfg.spread_m,
      });
      setMsg('Configuration synced to backend simulation runtime.', 'ok');
    } catch (err) {
      if (isMissingSimulationControl(err)) {
        setMsg('Legacy backend detected: simulation config endpoint unavailable, using local setup values.', 'info');
        return;
      }
      setMsg(err.message || 'Failed to save backend simulation config.', 'err');
    }
  }

  async function deploy() {
    setMsg('', '');
    try {
      let launchedViaControlEndpoint = false;
      try {
        await ns.api.startSimulation({
          node_count: cfg.node_count,
          center_lat: cfg.center_lat,
          center_lon: cfg.center_lon,
          spread_m: cfg.spread_m,
        });
        launchedViaControlEndpoint = true;
      } catch (startErr) {
        if (!isMissingSimulationControl(startErr)) {
          throw startErr;
        }
      }

      await ns.api.setLocation('mesh-center', cfg.center_lat, cfg.center_lon, cfg.spread_m);

      if (launchedViaControlEndpoint) {
        setMsg(`Simulation deployed - ${cfg.node_count} nodes launched in AO`, 'ok');
      } else {
        setMsg(`Legacy backend mode: AO location pushed, simulation controls unavailable (node count target ${cfg.node_count}).`, 'info');
      }
    } catch (err) {
      setMsg(err.message || 'Simulation launch failed.', 'err');
    }
  }

  async function stopSimulation() {
    setMsg('', '');
    try {
      await ns.api.stopSimulation();
      setMsg('Simulation stopped and mesh cleared.', 'info');
    } catch (err) {
      if (isMissingSimulationControl(err)) {
        setMsg('Legacy backend mode: stop endpoint unavailable. Restart backend process to reset topology.', 'info');
        return;
      }
      setMsg(err.message || 'Failed to stop simulation.', 'err');
    }
  }

  function bind() {
    const bindNum = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', () => {
        cfg[key] = Number(el.value);
        syncUiValues();
        drawPreview();
      });
    };

    bindNum('setup-node-count', 'node_count');
    bindNum('setup-center-lat', 'center_lat');
    bindNum('setup-center-lon', 'center_lon');
    bindNum('setup-spread', 'spread_m');
    bindNum('setup-sync-window', 'sync_window');
    bindNum('setup-trust-decay', 'trust_decay');

    document.getElementById('setup-distribution').addEventListener('change', (e) => {
      cfg.distribution = e.target.value;
      drawPreview();
    });

    document.getElementById('setup-reset').addEventListener('click', () => {
      cfg.node_count = 12;
      cfg.center_lat = 28.65;
      cfg.center_lon = 77.30;
      cfg.spread_m = 500;
      cfg.distribution = 'random';
      cfg.sync_window = 5;
      cfg.trust_decay = 30;
      syncUiValues();
      drawPreview();
      setMsg('Controls reset to tactical defaults.', 'info');
    });

    document.getElementById('setup-save').addEventListener('click', saveConfig);
    document.getElementById('setup-deploy').addEventListener('click', deploy);
    document.getElementById('setup-stop').addEventListener('click', stopSimulation);
  }

  function mount(container) {
    container.innerHTML = `
      <div class="setup-form">
        <div>
          <div class="setup-h">Simulation Setup</div>
          <div class="setup-sub">Configure mesh parameters before deploying virtual nodes to the tactical area of operations.</div>
        </div>

        <div class="fgroup">
          <label class="form-label">Node Count</label>
          <div class="range-row">
            <input id="setup-node-count" type="range" class="form-range" min="5" max="64" value="12" />
            <span class="frval" id="nc-v">12</span>
          </div>
        </div>

        <div class="fgroup">
          <label class="form-label">Crypto Algorithm</label>
          <select class="form-select" disabled>
            <option selected>ML-DSA-65 - NIST FIPS 204 (Level 3)</option>
          </select>
        </div>

        <div class="frow">
          <div class="fgroup">
            <label class="form-label">Distribution</label>
            <select id="setup-distribution" class="form-select">
              <option value="random" selected>Random scatter</option>
              <option value="grid">Grid pattern</option>
            </select>
          </div>
          <div class="fgroup">
            <label class="form-label">Spread Radius (m)</label>
            <input id="setup-spread" class="form-input" type="number" value="500" min="100" max="5000" step="50" />
          </div>
        </div>

        <div class="frow">
          <div class="fgroup">
            <label class="form-label">Time-sync Window (s)</label>
            <input id="setup-sync-window" class="form-input" type="number" value="5" min="1" max="30" />
          </div>
          <div class="fgroup">
            <label class="form-label">Trust Decay</label>
            <div class="range-row">
              <input id="setup-trust-decay" type="range" class="form-range" min="1" max="100" value="30" />
              <span class="frval" id="td-v">30%</span>
            </div>
          </div>
        </div>

        <div class="ao-box">
          <div class="ao-title">AO Center Coordinates</div>
          <div class="frow">
            <div class="fgroup"><label class="form-label mini">Latitude</label><input id="setup-center-lat" class="form-input mini" type="number" step="0.0001" value="28.650" /></div>
            <div class="fgroup"><label class="form-label mini">Longitude</label><input id="setup-center-lon" class="form-input mini" type="number" step="0.0001" value="77.300" /></div>
          </div>
        </div>

        <div class="setup-actions">
          <button id="setup-reset" class="btn">Reset</button>
          <button id="setup-save" class="btn">Save Config</button>
          <button id="setup-stop" class="btn btn-amber">Stop</button>
          <button id="setup-deploy" class="btn btn-primary">Deploy Simulation</button>
        </div>
        <div id="setup-msg" class="inline-error" style="display:none"></div>
      </div>

      <div class="setup-map">
        <div class="setup-map-tag">AO bounding box - backend linked</div>
        <div id="setupmap"></div>
      </div>
    `;

    setupMap = window.L.map('setupmap', { center: [cfg.center_lat, cfg.center_lon], zoom: 12, attributionControl: false });
    window.L.tileLayer('/osm-tiles/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(setupMap);
    overlay = window.L.layerGroup().addTo(setupMap);

    bind();
    syncUiValues();
    drawPreview();
    refreshFromBackend();

    ns.state.subscribe((state, reason) => {
      if (reason === 'tab' && state.ui && state.ui.activeTab === 'setup' && setupMap) {
        window.setTimeout(() => {
          setupMap.invalidateSize();
          drawPreview();
        }, 30);
      }
    });
  }

  ns.screen2 = { mount };
})(window.DQRMAN);
