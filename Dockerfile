# ==============================================================================
# AIO — All-In-One Local AI Runtime  Docker Image
# ==============================================================================
# CPU build (default):
#   docker build -t aio .
#   docker run -it -p 9480:9480 -p 8501:8501 aio
#
# NVIDIA CUDA build:
#   docker build --build-arg GPU=cuda -t aio-cuda .
#   docker run -it --gpus all -p 9480:9480 -p 8501:8501 aio-cuda
#
# AMD ROCm build:
#   docker build --build-arg GPU=rocm -t aio-rocm .
#   docker run -it --device /dev/kfd --device /dev/dri -p 9480:9480 -p 8501:8501 aio-rocm
#
# AMD Vulkan build:
#   docker build --build-arg GPU=vulkan -t aio-vulkan .
# ==============================================================================

ARG GPU=cpu
ARG PYTHON_VERSION=3.12

# ── Base image selection ───────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS base-cpu
FROM nvidia/cuda:12.3.2-cudnn9-devel-ubuntu22.04 AS base-cuda
FROM rocm/dev-ubuntu-22.04:6.0-complete AS base-rocm
FROM ubuntu:22.04 AS base-vulkan

# Select the right base
FROM base-${GPU} AS base

# ── System packages ────────────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    cmake build-essential git curl wget \
    libopenblas-dev libssl-dev pkg-config \
    libgomp1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Vulkan runtime (for Vulkan GPU build)
RUN if [ "${GPU}" = "vulkan" ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
    vulkan-tools libvulkan-dev \
    && rm -rf /var/lib/apt/lists/*; \
    fi

# ── App directory ─────────────────────────────────────────────────────────────
WORKDIR /aio
COPY aio.py requirements.txt ./

# ── Python deps ───────────────────────────────────────────────────────────────
ENV AIO_DATA_DIR=/aio/data
ENV CMAKE_ARGS_CPU=""
ENV CMAKE_ARGS_CUDA="-DGGML_CUDA=on"
ENV CMAKE_ARGS_ROCM="-DGGML_HIPBLAS=on"
ENV CMAKE_ARGS_VULKAN="-DGGML_VULKAN=on"

RUN python3.12 -m pip install --upgrade pip wheel setuptools -q && \
    python3.12 -m pip install -r requirements.txt -q

# Compile llama-cpp-python with the right backend
RUN if [ "${GPU}" = "cuda" ]; then \
        CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --no-cache-dir -q; \
    elif [ "${GPU}" = "rocm" ]; then \
        CMAKE_ARGS="-DGGML_HIPBLAS=on" AMDGPU_TARGETS="gfx1100,gfx1030,gfx906" \
        pip install llama-cpp-python --no-cache-dir -q; \
    elif [ "${GPU}" = "vulkan" ]; then \
        CMAKE_ARGS="-DGGML_VULKAN=on" pip install llama-cpp-python --no-cache-dir -q; \
    else \
        pip install llama-cpp-python --no-cache-dir -q; \
    fi

# ── Volume for persistent data (models, RAG, adapters) ───────────────────────
VOLUME ["/aio/data"]

# ── Expose ports ──────────────────────────────────────────────────────────────
EXPOSE 9480 8501

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -sf http://localhost:9480/status || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
ENTRYPOINT ["python3.12", "aio.py"]
CMD []
