# Contributing to DQRMAN

Welcome! DQRMAN is a mission-critical decentralized mesh orchestrator. To maintain high assurance and predictability, we follow strict development standards.

---

## 🛠️ Development Setup

### 1. Requirements (Local)
* **Python 3.10** (Primary target) or 3.11.
* **OpenSSL 1.1.1+** development headers.
* **CMake 3.18+**.

### 2. Platform-Specific `liboqs` Build

#### **Ubuntu / WSL2 (Recommended)**
```bash
sudo apt-get update && sudo apt-get install -y cmake build-essential libssl-dev pkg-config git
git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs
cmake -S /tmp/liboqs -B /tmp/liboqs/build -DBUILD_SHARED_LIBS=ON -DOPENSSL_ROOT_DIR=/usr
cmake --build /tmp/liboqs/build --parallel 4
sudo cmake --install /tmp/liboqs/build
sudo ldconfig
```

#### **macOS (Homebrew)**
```bash
brew install cmake openssl pkg-config
git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs
cmake -S /tmp/liboqs -B /tmp/liboqs/build -DBUILD_SHARED_LIBS=ON -DOPENSSL_ROOT_DIR=$(brew --prefix openssl)
cmake --build /tmp/liboqs/build --parallel 4
sudo cmake --install /tmp/liboqs/build
```

#### **Windows**
Native Windows builds are **not supported**. Please use **WSL2** with the Ubuntu instructions above.

---

## 📜 Coding Standards

### **Backend (Python 3.10)**
* **No Python 3.12+ features**: We maintain compatibility with standard Debian/Ubuntu LTS Python versions.
* **Docstrings**: All public classes and methods **must** use Google-style docstrings.
    ```python
    def verify_identity(self, public_key: bytes) -> bool:
        """Verifies the node identity against the provided key.
        
        Args:
            public_key: The ML-DSA-65 public key to check.
            
        Returns:
            True if valid, False otherwise.
        """
    ```

### **Frontend (JavaScript)**
* ⚠️ **TypeScript is EXPLICITLY PROHIBITED**: Per SRS Section 7.3, the frontend must remain in **Vanilla JavaScript** to minimize build-chain complexity and ensure long-term auditability without transpilation layers.
* **No Frameworks**: Use standard DOM APIs and D3/Leaflet directly.

---

## ⚔️ Adding a New Attack Type (Checklist)

When implementing a new adversarial vector in `simulation/attacks.py`, follow this 5-step checklist:

1. [ ] **Write the logic**: Implement the attack method in the `AttackSimulator` class.
2. [ ] **Verification**: Add a test in `tests/test_attacks.py` that asserts 100% detection/mitigation success.
3. [ ] **API Wiring**: Add a case to the `POST /api/v1/attack` endpoint in `backend/server.py`.
4. [ ] **Dashboard Integration**: Add the corresponding event handler in `frontend/socket.js` to trigger a specific UI animation or badge.
5. [ ] **Documentation**: Update the "Threat Model" section in `PROTOCOL.md` with the new vector's logic and mitigation details.

---

## 🚀 Git Conventions

Use the following prefixes for all commit messages:
* `feat`: A new feature (e.g., `feat: add sybil detection logic`)
* `fix`: A bug fix (e.g., `fix: resolve nonce collision in high-scaling runs`)
* `test`: Adding or updating tests.
* `docs`: Documentation changes.
* `refactor`: Code changes that neither fix a bug nor add a feature.
* `chore`: Maintenance tasks (CI updates, Makefile changes).
