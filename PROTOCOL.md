# DQRMAN Protocol Specification (v1.0-Academic)

This document provides a technical overview of the Decentralized Quality-of-service Routing for Mesh Ad-hoc Networks (DQRMAN) orchestration protocol for security auditors and academic reviewers.

## 1. Overview
### 1.1 Security Model
DQRMAN operates on a **Zero-Trust, Decentralized Orchestration** model. There is no central authority for mesh logic, cryptographic root-of-trust, or routing decisions. Trust is established peer-to-peer via formal cryptographic proof and maintained through continuous behavioral monitoring.

### 1.2 Threat Assumptions
- **Adversary Capability**: We assume an active Dolev-Yao adversary capable of intercepting, replaying, and fabricating messages.
- **Trusted Computing Base (TCB)**: The TCB is limited to the local node's hardware/OS and the `liboqs` cryptographic library.
- **Identity**: Identity is strictly bound to a 1952-byte ML-DSA-65 (Dilithium) public key.

### 1.3 Out of Scope
- Physical-layer side-channel attacks (e.g., differential power analysis).
- Compromise of the underlying Linux kernel or hardware root-of-trust.
- Advanced persistent threats (APTs) involving total memory corruption of the node process.

## 2. Mutual Authentication Handshake
The following sequence describes the mutual authentication flow derived from SRS Section 8.2 and implemented in `backend/node.py:AuthProtocol`.

| Step | Actor | Action | Data Exchanged |
| :--- | :--- | :--- | :--- |
| 1 | Node A | Generate Challenge Nonce ($N_A$) | Internal |
| 2 | Node A | Capture Timestamp ($T_A$) | Internal |
| 3 | Node A | Sign $(N_A \|\| T_A)$ and send to Node B | $\{ID_A, PK_A, N_A, T_A, Sig_A\}$ |
| 4 | Node B | Validate $|T_{now} - T_A| \leq \Delta t$ (TTL Window) | Internal |
| 5 | Node B | Check $N_A$ against `NonceCache.hex()` | Internal |
| 6 | Node B | Verify $Sig_A$ using $PK_A$ | Internal |
| 7 | Node B | Generate Response Nonce ($N_B$) and $T_B$ | Internal |
| 8 | Node B | Sign $(N_B \|\| T_B \|\| N_A)$ (Challenge Binding) | Internal |
| 9 | Node B | Send response to Node A | $\{ID_B, PK_B, N_B, T_B, Sig_B\}$ |
| 10 | Node A | Validate $|T_{now} - T_B| \leq \Delta t$ | Internal |
| 11 | Node A | Verify $Sig_B$ using $PK_B$ on $(N_B \|\| T_B \|\| N_A)$ | Internal |
| 12 | System | Confirm Mutual Trust; Update `TrustTable` | Internal |

## 3. Node State Machine
Nodes transition through seven lifecycle states to ensure mesh stability and Byzantine fault tolerance.

| State | Entry Condition | Exit Condition |
| :--- | :--- | :--- |
| **UNVERIFIED** | Process start; keypair generation. | Successful socket bind / init completion. |
| **JOINING** | Broadcast of signed Join packet. | Receipt of valid Join response. |
| **ACTIVE** | Trust verified; heartbeat exchange active. | Heartbeat timeout or detected anomaly. |
| **HEALING** | Detected transient packet loss (1-2 misses). | Re-synchronization or transition to Isolated. |
| **ISOLATED** | Total loss of heartbeats from all peers. | Successful reconnection or manual destruction. |
| **QUARANTINED** | Detected Replay/Spoofing/Anomaly above threshold. | **None** (Requires administrative reset). |
| **DESTROYED** | Manual kill signal or unrecoverable error. | **None** (Terminal State). |

## 4. Threat Model & Detection
### 4.1 Replay Detection (FR-18, NFR-09)
Replay attacks are detected via a dual-layer check:
1. **Temporal Filtering**: Challenges older than 5 seconds are rejected (`TIMESTAMP_EXPIRED`).
2. **Nonce Blacklisting**: Every nonce is cached as its `.hex()` string for $5.0$ seconds. Attempting to reuse a nonce within the window triggers `DUPLICATE_NONCE`.

### 4.2 Spoofing Detection (FR-19)
Adversaries attempting to impersonate Node X by providing $ID_X$ and $PK_X$ will fail verification because they cannot produce a valid ML-DSA-65 signature for the current challenge without $PrK_X$. The system returns `INVALID_SIGNATURE`.

### 4.3 Jamming Detection (FR-13)
Physical layer jamming is modeled as total packet loss. The `HeartbeatReceiver` tracks liveness. If no valid heartbeats arrive within $(Interval \times TimeoutMultiplier)$, the node is marked as **HEALING** then **ISOLATED**, triggering an automatic reroute of the mesh topology.

## 5. Configuration Tradeoffs
The `time_sync_window` ($\Delta t$) determines the strictness of the freshness check:
- **1s**: Highly secure; requires ultra-stable PTP clock synchronization.
- **5s (Default)**: Balanced for tactical MANET deployments with moderate clock drift.
- **30s**: High tolerance for jitter; susceptible to larger-window replay window.
- **60s**: Dangerous; only for extremely legacy or high-latency satellite links.

## 6. Mathematical Formulas
### 6.1 Trust Edge Weight
$Weight = 0.5 \times AuthRate + 0.3 \times ProximityScore + 0.2 \times Recency$

### 6.2 Geographic Proximity Model
Proximity is computed using the **Haversine formula** to determine the great-circle distance ($d$) in meters between two sets of GPS coordinates. This serves as a software simulation of what will ultimately be physical Ultra-Wideband (UWB) radio hardware.

The **Proximity Score** is derived as follows:
$$Score = \frac{1.0}{1.0 + \frac{d}{500}}$$

*   **Co-located nodes** ($d = 0$): $1.0 / (1.0 + 0) = \mathbf{1.0}$
*   **Nodes 500m apart**: $1.0 / (1.0 + 1) = \mathbf{0.5}$
*   **Nodes 2km apart**: $1.0 / (1.0 + 4) = \mathbf{0.2}$

This model allows for a continuous trust gradient based on locality, providing a "geographic anchor" to the cryptographic identity.

## 7. Security Properties
- **Post-Quantum Resilience**: Uses ML-DSA-65 (Dilithium3) for all signatures.
- **Forward Secrecy**: Handshake binds responses to challenges to prevent pre-computation.
- **Byzantine Resilience**: `AnomalyLogger` automatically quarantines nodes exhibiting malicious behavior.

## 8. Known Limitations
The following limitations apply to the **Phase 1 Orchestrator** and are scheduled for resolution in Phase 2 hardware integration:

-   **Shared System Clock**: Current simulation assumes a perfectly shared system clock across all virtual nodes ($T_{now}$ is global). Physical deployment will require a high-precision PTP/NTP synchronization layer.
-   **Software-Only Simulation**: No physical device isolation or Trusted Execution Environment (TEE) is enforced. Any vulnerability in the host OS could compromise all "isolated" nodes.
-   **Simulated Proximity**: Haversine distance is a mathematical approximation and does not account for signal-to-noise ratios (SNR), multi-path interference, or real-world UWB flight-time characteristics.
-   **Unprotected Communication**: TCP on loopback (127.0.0.1) is used for simulation connectivity and is not battle-hardened for noisy or adversarial radio environments.
-   **Side-Channel Vulnerability**: The current `liboqs` integration is not hardened against power-analysis, electromagnetic, or timing-based side-channel attacks on the secret Dilithium signing keys.
