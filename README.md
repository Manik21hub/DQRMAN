# DQRMAN — Distributed Quantum-Resistant Mesh Authentication Network

![Version](https://img.shields.io/badge/version-1.0.0--phase1-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen.svg)
![CI Status](https://github.com/Manik21hub/DQRMAN/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

DQRMAN is a highly resilient, decentralized military mesh orchestrator featuring zero-trust mutual authentication powered by post-quantum cryptography (ML-DSA-65 / Dilithium), dynamic self-healing routing, and a live OpenStreetMap geographic command dashboard.

---

## Quick Start

### Prerequisites
* **Python**: 3.10 or 3.11
* **CMake & Build Tools**
* **liboqs**: The Open Quantum Safe C library (latest HEAD)
* **Docker / Docker Compose** (Optional, for containerized environments)

### 1. Install `liboqs` (Platform Specific)

**Ubuntu/Debian:**
```bash
sudo apt-get update && sudo apt-get install -y cmake build-essential libssl-dev pkg-config
git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs
cmake -S /tmp/liboqs -B /tmp/liboqs/build -DBUILD_SHARED_LIBS=ON -DOPENSSL_ROOT_DIR=/usr
cmake --build /tmp/liboqs/build --parallel 4
sudo cmake --install /tmp/liboqs/build
sudo ldconfig
rm -rf /tmp/liboqs
```

**macOS (Homebrew):**
```bash
brew install cmake openssl pkg-config
git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs
cmake -S /tmp/liboqs -B /tmp/liboqs/build -DBUILD_SHARED_LIBS=ON -DOPENSSL_ROOT_DIR=$(brew --prefix openssl)
cmake --build /tmp/liboqs/build --parallel 4
sudo cmake --install /tmp/liboqs/build
rm -rf /tmp/liboqs
```

### 2. Install Python Dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install git+https://github.com/open-quantum-safe/liboqs-python.git
make install
```

### 3. Run the complete stack
```bash
make run
```
Navigate to `http://localhost:8080/` to view the Kinetic Fortress dashboard.

---

## Architecture Overview

The system is strictly divided into five distinct operational layers:
1. **Frontend (Browser)**: Real-time UI displaying dynamic force topologies, geographic maps, and a scrolling threat event feed connected via WebSockets.
2. **Orchestrator (Server)**: The command authority maintaining a unified `TrustGraph` purely for visualization, and a REST/WS gateway.
3. **Simulation Layer**: Handles scenario generation, network latency, geospatial scattering, and adversarial injection (`AttackSimulator`).
4. **Decentralized Node Fabric**: The true mesh. Isolated `Node` instances that operate with zero-trust—maintaining private trust/routing tables and authenticating entirely peer-to-peer.
5. **Post-Quantum Cryptography Backing**: `liboqs` bound via `oqs-python` for generating ML-DSA-65 keys, challenges, nonces, and verifying mutual authentication packets.

---

## Demo Walkthrough

Try the following 5 actions immediately after booting:
1. **Observe Initial Authentication Flurry**: Watch the Event Feed immediately fill with green `MUTUAL_AUTH` logs as all nodes establish zero-trust validation using ML-DSA-65 signatures in under 200ms.
2. **Execute a Spoofing Attack**: From the Dashboard side-panel, inject a spoofing attack. Observe a red `SPOOF_FAILED` event in the feed and watch the targeted node isolate itself.
3. **Trigger Quarantine Threshold**: Spam attack simulations against a single node. The mesh `AnomalyLogger` will trigger, transitioning the node into a strict `QUARANTINED` state (yellow UI ring).
4. **Symmetric Node Destruction**: Use the terminal to call the `/api/v1/nodes/<id>` DELETE endpoint. The node state vanishes from the mesh, broadcasting a `NODE_DESTROYED` (grey) alert to the command center.
5. **Self-Healing Routing Check**: After destroying nodes within a network choke-point, observe subsequent mutual authentication successes gracefully re-route around the missing links automatically.

---

## OSM Map Integration

DQRMAN visualizes the military mesh over real-world geography using OpenStreetMap (OSM) and Leaflet.
* **Toggling the View**: Use the top-bar tabs (`DASHBOARD` / `MAP`) to instantly switch between the logical force topology and the geographic layout.
* **Changing the Demo City**: You can shift the mesh footprint by modifying the `osm.fallback_lat` and `osm.fallback_lon` parameters inside `config.yaml`.
* **Offline Deployment (Air-Gapped)**: Run `make cache-tiles` to pre-download localized map tiles directly into `frontend/vendor/tiles/`. The orchestrator server will act as an offline tile proxy.

---

## Running Tests

The repository maintains an automated suite covering encryption, simulation logic, mesh healing, and scaling capacity.
```bash
make test
```
The pipeline automatically asserts a minimum of 80% coverage and executes the `test_scale.py` NFR simulations.

---

## Configuration Reference (`config.yaml`)

Based strictly on SRS Appendix C specifications:

| Section | Parameter | Type | Default | Purpose |
|---------|-----------|------|---------|---------|
| `simulation` | `node_count` | int | 10 | The number of isolated instances to spawn at boot. |
| `simulation` | `auto_auth` | bool | true | Automatically perform peer validations on startup. |
| `crypto` | `dilithium_variant` | enum | ML-DSA-65 | Post-quantum algorithm strictness. |
| `network` | `survivability_threshold` | float | 0.2 | Min remaining node threshold before cluster-fail. |
| `network` | `time_sync_window` | float | 5.0 | Seconds permitted before detecting timestamp drift. |
| `security` | `anomaly_threshold` | int | 5 | Threshold count triggering node quarantine. |
| `osm` | `fallback_lat`/`lon` | float | 28.6139 | Center of operations for node scattering geometry. |

---

## API Reference

### Endpoints
| HTTP Method | Endpoint | Description | Return Signature |
|-------------|----------|-------------|------------------|
| `GET`       | `/health` | Core system health check | `200 {status, timestamp, version}` |
| `GET`       | `/api/v1/nodes` | List active mesh graph | `200 [{node_id, status, trust_score, lat, lon}]` |
| `POST`      | `/api/v1/attack` | Trigger scenario injection | `202 {status: 'accepted', attack_type}` |
| `DELETE`    | `/api/v1/nodes/<id>` | Destroy a peer | `200 {node_id, surviving_count, is_operational}` |

### WebSocket Events (`/events`)
* `MESH_STATE_UPDATE`: Total topology broadcast for rendering.
* `attack_detected`: Real-time streaming anomaly alert.
* `NODE_DESTROYED`: Status modification intercept.

---

## Phase 2 Roadmap
* Complete the independent background `Node` process logic explicitly detaching it from orchestrator instantiation.
* Integrate Stitch GUI mockups for `NODE_DETAIL_MODAL` and the `SURVIVABILITY_REPORT` panel.
* Expand the `AttackSimulator` to cover Eclipse attacks, Sybil swarms, and geographic jamming zones.
* Resolve eventlet maintenance deprecation by migrating the orchestration web-server to pure Python `asyncio`.

---

## Acknowledgements

* **[liboqs](https://github.com/open-quantum-safe/liboqs)** (Apache 2.0) — The core Open Quantum Safe C implementation.
* **[NetworkX](https://networkx.org/)** (BSD) — Graph processing for geographic spatial computations and trust-path verification.
* **[D3.js](https://d3js.org/)** (ISC) — Force-directed physics visualization for logical mesh plotting.
* **[Leaflet](https://leafletjs.com/)** (BSD-2-Clause) — High-performance mapping UI. 
