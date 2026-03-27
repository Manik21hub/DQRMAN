# ──────────────────────────────────────────────────────────────────────────────
# DQRMAN — Phase 1 Container Image
# Base: python:3.10-slim
# liboqs: 0.10.0 (built from source with shared library support)
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.10-slim

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake \
        build-essential \
        libssl-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Build and install liboqs 0.10.0 as a shared library
# ---------------------------------------------------------------------------
RUN git clone --depth 1 --branch 0.10.0 \
        https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs \
    && cmake -S /tmp/liboqs -B /tmp/liboqs/build \
        -DBUILD_SHARED_LIBS=ON \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/liboqs/build --parallel 4 \
    && cmake --install /tmp/liboqs/build \
    && ldconfig \
    && rm -rf /tmp/liboqs

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/     backend/
COPY simulation/  simulation/
COPY frontend/    frontend/
COPY config.yaml  config.yaml
COPY scripts/     scripts/

# ---------------------------------------------------------------------------
# Default entrypoint — run the simulation orchestrator
# ---------------------------------------------------------------------------
CMD ["python", "run_simulation.py"]
