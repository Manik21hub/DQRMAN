# DQRMAN Presenter Script: 5-Minute Phase 1 Demo

This script is designed for a 5-minute live demonstration of the DQRMAN Orchestrator and Simulation environment.

---

### Step 1: Mesh Formation & Zero-Trust Core
*   **What to say**: "Welcome to DQRMAN. What you see here is a 10-node localized drone mesh initializing. Unlike traditional networks, there is no central server managing these identities. Every drone you see is independently authenticating its neighbors using post-quantum ML-DSA-65 (Dilithium) signatures. This is 'Zero-Trust at the Edge'—even if I'm the orchestrator, I don't hold their private keys."
*   **What to click**: Refresh the Dashboard (`http://localhost:8080`).
*   **Audience sees**: 10 green drone icons appearing on the Force Topology graph, instantly connecting with solid lines. The Event Feed scrolls with green `MUTUAL_AUTH SUCCESS` messages.

---

### Step 2: Geographic Self-Healing
*   **What to say**: "We aren't just looking at dots on a screen; we're looking at a real-world tactical deployment. By switching to the map, we see these drones scattered around our operational zone. If we lose nodes due to combat or failure—say, these three right here—the mesh detects the heartbeat loss and re-routes the trust paths in under 2 seconds."
*   **What to click**: 
    1. Click the **MAP** tab in the top navigation.
    2. Use the "Kill" button on 3 random nodes (or use the terminal: `make run kill=<id>`).
*   **Audience sees**: Drones positioned on a live OpenStreetMap. When nodes are destroyed, they turn grey/red. Existing routes briefly turn into **orange dashed lines** (HEALING) and then snap back to **green solid lines** (ACTIVE) as the mesh heals.

---

### Step 3: Resisting the Quantum Replay
*   **What to say**: "In a classic attack, an adversary captures a valid authentication packet and plays it back later to gain entry. Watch what happens when I inject a Replay Attack. Even with a quantum computer, the attacker fails because our protocol binds every signature to a unique, time-stamped challenge that expires in seconds."
*   **What to click**: Click the **Inject Replay** button in the Attack Simulation panel.
*   **Audience sees**: A red pulse/ripple animation on the targeted node's icon. A red `REPLAY_DETECTED — BLOCKING IDENTITY` alert appears at the top of the Event Feed.

---

### Step 4: Spoofing & Mathematical Impossibility
*   **What to say**: "Now, let's try a Spoofing attack. The adversary attempts to impersonate a trusted friendly drone by mimicking its ID. Because they lack the underlying Dilithium private key, it is mathematically impossible for them to sign the challenge correctly. The mesh identifies the fraud immediately."
*   **What to click**: Click the **Inject Spoof** button in the Attack Simulation panel.
*   **Audience sees**: Another red pulse animation. The Event Feed displays a high-priority `SPOOFING_ATTEMPT — IDENTITY MISMATCH` warning. The targeted node remains secure.

---

### Step 5: Absolute Survivability
*   **What to say**: "The ultimate test of a decentralized system is its breaking point. I'm going to destroy 7 more nodes, leaving only 20% of the original force. In a centralized system, the network would be dead. Here, the survivors still authenticate, still maintain trust, and the dashboard confirms: Operational: YES. There is no single kill-switch."
*   **What to click**: Destroy 7 more nodes until only 2 are left active.
*   **Audience sees**: 8 greyed-out icons and only 2 green active drones. The "Mesh Health" indicator shows a low percentage but the **Operational Status** remains a bold green **YES**.
