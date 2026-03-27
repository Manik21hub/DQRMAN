# ──────────────────────────────────────────────────────────────────────────────
# DQRMAN — Phase 1 Container Image
# Base: python:3.10-slim
# liboqs: latest HEAD (shared library, ML-DSA-65)
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.10-slim

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake \
        build-essential \
        libssl-dev \
        pkg-config \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Build and install liboqs (latest HEAD) as a shared library
# ---------------------------------------------------------------------------
RUN git clone --depth 1 \
        https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs \
    && cmake -S /tmp/liboqs -B /tmp/liboqs/build \
        -DBUILD_SHARED_LIBS=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DOPENSSL_ROOT_DIR=/usr \
    && cmake --build /tmp/liboqs/build --parallel 4 \
    && cmake --install /tmp/liboqs/build \
    && ldconfig \
    && rm -rf /tmp/liboqs

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------
WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install oqs-python from GitHub source (must run after liboqs .so is present)
RUN pip install --no-cache-dir \
    git+https://github.com/open-quantum-safe/liboqs-python.git

COPY backend/     backend/
COPY simulation/  simulation/
COPY frontend/    frontend/
COPY config.yaml  config.yaml
COPY scripts/     scripts/

# ---------------------------------------------------------------------------
# Default entrypoint — run the simulation orchestrator
# ---------------------------------------------------------------------------
CMD ["python", "run_simulation.py"]
