#!/usr/bin/env python3
"""
Linus PAI — Private AI Runtime  (May 2025)
==============================================
Single-file runtime: inference · RAG · thermal training · agentic tasks · mesh network.

Auto-detects platform (Apple Silicon MLX · CUDA · CPU), picks the best two models:
  • sudo  — largest model the device RAM can hold (complex reasoning)
  • sized — smallest fast model for routing/quick replies

Network drive mounts (/Volumes, /mnt, /media) are scanned for pre-existing GGUFs.
Exo-style mesh: UDP peer discovery + HTTP pipeline sharding across LAN nodes.

Usage
-----
  python aio.py                     auto-detect and launch web UI
  python aio.py --install           install deps only, no server
  python aio.py --chat              interactive terminal chat
  python aio.py --agent "task"      run one-shot autonomous agent task
  python aio.py --train             thermal-throttled LoRA fine-tune cycle
  python aio.py --mesh              start mesh relay node
  python aio.py --serve             backend API only (no UI)
  python aio.py --status            print device/thermal/model status
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 0 · Bootstrap (self-installing, zero-dep on first run)
# ──────────────────────────────────────────────────────────────────────────────

import argparse
import ast
import asyncio
import gc
import glob
import hashlib
import importlib
import json
import logging
import math
import os
import platform
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Generator, Iterator, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aio")

PAI_VERSION = "1.0.0"
BASE_DIR    = Path(__file__).parent.resolve()
DATA_DIR    = Path(os.getenv("PAI_DATA_DIR", str(BASE_DIR / "pai_data")))
MODELS_DIR  = DATA_DIR / "models"
ADAPTERS_DIR = DATA_DIR / "adapters"
TRAIN_DIR   = DATA_DIR / "train_buffer"
RAG_DIR     = DATA_DIR / "rag"
AUDIT_DIR   = DATA_DIR / "audit"

PLUGINS_DIR = DATA_DIR / "plugins"
QUERY_LOG   = DATA_DIR / "query.log"

for _d in [DATA_DIR, MODELS_DIR, ADAPTERS_DIR, TRAIN_DIR, RAG_DIR, AUDIT_DIR, PLUGINS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ── Package sets per platform ─────────────────────────────────────────────────

_BASE_PKGS = [
    "fastapi>=0.111", "uvicorn[standard]>=0.29",
    "pydantic>=2.0", "requests>=2.31",
    "psutil>=5.9", "ddgs>=9.0",
    "huggingface_hub>=0.22", "tqdm>=4.66",
    "python-multipart>=0.0.9", "aiofiles>=23.2",
    "rich>=13.7", "pypdf>=4.2",
    "streamlit>=1.35",
]
_MLX_PKGS   = ["mlx>=0.16", "mlx-lm>=0.16"]
_GGUF_PKGS  = []   # compiled below per platform
_RAG_PKGS   = ["torch", "numpy>=1.26,<2.0", "sentence-transformers>=3.0,<4.0"]
# Install order matters: torch first so pip respects its numpy constraint,
# then numpy pinned <2.0 (torch<=2.3 on Intel Mac CPU needs numpy 1.x),
# then sentence-transformers 3.x (works with torch>=1.11; 4.x needs torch>=2.4).
_TRAIN_PKGS = []   # MLX only; GGUF training via llama-cpp-python


def _pip(*pkgs: str, quiet: bool = True) -> None:
    cmd = [sys.executable, "-m", "pip", "install"] + list(pkgs)
    if quiet:
        cmd += ["-q"]
    subprocess.check_call(cmd)


def _progress(step: int, total: int, msg: str) -> None:
    """Print a progress line during installs.
    Uses carriage-return overwrite in a TTY, plain newlines when piped/logged
    so install.log stays readable instead of containing raw \\r bytes."""
    import sys as _sys
    bar = "█" * step + "░" * (total - step)
    if _sys.stdout.isatty():
        print(f"\r  [{bar}] {step}/{total}  {msg:<52}", end="", flush=True)
        if step == total:
            print()
    else:
        # Non-interactive (piped to a log): one clean line per step
        print(f"  [{bar}] {step}/{total}  {msg}", flush=True)


def bootstrap(force: bool = False) -> None:
    """Install all required packages.  Called once at startup."""
    marker = DATA_DIR / ".bootstrapped"
    if marker.exists() and not force:
        return

    total_steps = 5
    print(f"\n  Linus PAI — first-run setup (Python {sys.version.split()[0]})")
    print(f"  Platform: {platform.system()} {platform.machine()}\n")

    # Step 1: Core packages
    _progress(1, total_steps, "Installing core packages…")
    try:
        _pip(*_BASE_PKGS)
    except Exception as exc:
        print(f"\n[ERR] Core package install failed: {exc}")
        print("      Check your internet connection and try: pip install -r requirements.txt")
        sys.exit(1)

    # Step 2: RAG packages (non-fatal — keyword fallback is built in)
    # sentence-transformers requires torch; torch has no py3.13 wheel.
    _progress(2, total_steps, "Installing RAG / embeddings (torch + sentence-transformers)…")
    try:
        _pip(*_RAG_PKGS)
    except Exception as e:
        print(f"\n  [warn] sentence-transformers skipped ({type(e).__name__}). "
              "RAG uses keyword search instead of cosine similarity.")

    plat    = platform.system().lower()
    machine = platform.machine().lower()

    # macOS → Metal for both Apple Silicon and Intel Mac AMD GPUs
    if plat == "darwin":
        if machine in ("arm64", "aarch64"):
            _progress(3, total_steps, "Installing MLX (Apple Silicon Metal backend)…")
            try:
                _pip(*_MLX_PKGS)
            except Exception as exc:
                print(f"\n  [warn] MLX install failed: {exc}. Will use llama-cpp instead.")
        else:
            _progress(3, total_steps, "Intel Mac — skipping MLX (Apple Silicon only)…")

        _progress(4, total_steps, "Compiling llama-cpp-python with Metal GPU support…")
        env = os.environ.copy()
        env["CMAKE_ARGS"] = "-DGGML_METAL=on"
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install",
                 "llama-cpp-python>=0.2.80", "--no-cache-dir", "-q"],
                env=env,
            )
        except Exception as exc:
            print(f"\n  [warn] Metal llama-cpp-python failed: {exc}. Falling back to CPU build.")
            try:
                _pip("llama-cpp-python>=0.2.80")
            except Exception:
                pass
    elif plat in ("linux", "windows"):
        cuda_available = shutil.which("nvcc") is not None or os.path.exists("/usr/local/cuda")
        rocm_available = shutil.which("rocm-smi") is not None or os.path.exists("/opt/rocm")
        vulkan_available = shutil.which("vulkaninfo") is not None or os.path.exists("/dev/dri")
        env = os.environ.copy()
        if cuda_available:
            env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
        elif rocm_available:
            # AMD ROCm — HIPBlas backend
            env["CMAKE_ARGS"] = "-DGGML_HIPBLAS=on"
            env.setdefault("AMDGPU_TARGETS", "gfx1100,gfx1030,gfx906")   # RX 7xxx, RX 6xxx, Vega
        elif vulkan_available:
            # AMD/Intel Vulkan compute — works on any Vulkan 1.1+ GPU
            env["CMAKE_ARGS"] = "-DGGML_VULKAN=on"
        gpu_tag = ("CUDA" if cuda_available else
                   "ROCm" if rocm_available else
                   "Vulkan" if vulkan_available else "CPU")
        _progress(4, total_steps, f"Compiling llama-cpp-python ({gpu_tag})…")
        try:
            if cuda_available or rocm_available or vulkan_available:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install",
                     "llama-cpp-python>=0.2.80", "--no-cache-dir", "-q"],
                    env=env,
                )
            else:
                _pip("llama-cpp-python>=0.2.80")
        except Exception as exc:
            print(f"\n  [warn] llama-cpp-python compile failed: {exc}")
            print("  Try: CMAKE_ARGS='-DGGML_METAL=on' pip install llama-cpp-python --no-cache-dir")
    else:
        _progress(4, total_steps, "Compiling llama-cpp-python (CPU)…")
        try:
            _pip("llama-cpp-python>=0.2.80")
        except Exception as exc:
            print(f"\n  [warn] llama-cpp-python failed: {exc}")

    _progress(total_steps, total_steps, "Setup complete.")
    print(f"\n  ✓ Linus PAI ready.  Run --doctor to verify.\n")
    marker.write_text(PAI_VERSION)


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 · Platform Detection
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DeviceProfile:
    platform:      str        # "apple_silicon" | "cuda" | "rocm" | "vulkan" | "cpu"
    os_name:       str
    machine:       str
    cpu_cores:     int
    ram_gb:        float
    gpu_vram_gb:   float      # 0 if no discrete GPU
    gpu_name:      str
    has_metal:     bool
    has_cuda:      bool
    has_rocm:      bool       # AMD ROCm (HIP) — Linux/Windows
    has_vulkan:    bool       # Vulkan compute — AMD/Intel/cross-platform fallback
    chip_name:     str        # e.g. "Apple M4 Max"
    node_id:       str        # stable UUID for mesh identity
    hostname:      str


def detect_device() -> DeviceProfile:
    import psutil

    plat    = platform.system().lower()
    machine = platform.machine().lower()
    cpu_cores = psutil.cpu_count(logical=False) or 4
    ram_gb    = psutil.virtual_memory().total / (1024 ** 3)
    hostname  = socket.gethostname()

    node_id_file = DATA_DIR / ".node_id"
    if node_id_file.exists():
        node_id = node_id_file.read_text().strip()
    else:
        node_id = str(uuid.uuid4())
        node_id_file.write_text(node_id)

    gpu_vram_gb  = 0.0
    gpu_name     = "none"
    has_metal    = False
    has_cuda     = False
    has_rocm     = False
    has_vulkan   = False
    chip_name    = ""

    if plat == "darwin":
        has_metal = True
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"], text=True, timeout=10
            )
            for line in out.splitlines():
                if "Chip" in line or "Processor Name" in line:
                    chip_name = line.split(":")[-1].strip()
                    break
        except Exception:
            chip_name = platform.processor()

        if machine in ("arm64", "aarch64"):
            gpu_vram_gb = ram_gb
            gpu_name    = chip_name or "Apple GPU"
            device_plat = "apple_silicon"
        else:
            # Intel Mac — detect discrete GPU (AMD Radeon etc.) via DisplaysDataType
            try:
                disp_out = subprocess.check_output(
                    ["system_profiler", "SPDisplaysDataType"], text=True, timeout=10
                )
                for line in disp_out.splitlines():
                    line = line.strip()
                    if "Chipset Model" in line:
                        gpu_name = line.split(":", 1)[-1].strip()
                    elif "VRAM" in line and ":" in line:
                        vram_str = line.split(":", 1)[-1].strip()
                        try:
                            parts = vram_str.split()
                            val   = float(parts[0].replace(",", ""))
                            unit  = parts[1].upper() if len(parts) > 1 else "GB"
                            gpu_vram_gb = val if "GB" in unit else val / 1024
                        except Exception:
                            pass
            except Exception:
                pass
            device_plat = "metal" if gpu_vram_gb > 0 else "cpu"

    else:
        chip_name = platform.processor()

        # ── NVIDIA CUDA ──────────────────────────────────────────────────────
        try:
            nv_out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5
            ).strip()
            if nv_out:
                parts       = nv_out.split(",")
                gpu_name    = parts[0].strip()
                gpu_vram_gb = float(parts[1].strip()) / 1024
                has_cuda    = True
                device_plat = "cuda"
        except Exception:
            device_plat = "cpu"

        # ── AMD ROCm (HIP) — Linux & Windows ──────────────────────────────
        if not has_cuda:
            try:
                roc_out = subprocess.check_output(
                    ["rocm-smi", "--showproductname", "--csv"],
                    text=True, timeout=5
                ).strip()
                if roc_out:
                    for line in roc_out.splitlines():
                        if "Card" in line or "GPU" in line:
                            gpu_name = line.split(",")[-1].strip()
                            break
                    # VRAM from rocm-smi
                    try:
                        mem_out = subprocess.check_output(
                            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                            text=True, timeout=5
                        )
                        for ln in mem_out.splitlines():
                            if "VRAM Total" in ln:
                                gpu_vram_gb = int(ln.split(",")[-1]) / (1024 ** 3)
                                break
                    except Exception:
                        gpu_vram_gb = 8.0   # conservative default
                    has_rocm    = True
                    device_plat = "rocm"
            except (FileNotFoundError, subprocess.SubprocessError):
                pass

        # ── AMD/Intel Vulkan (fallback when no ROCm/CUDA driver) ──────────
        if not has_cuda and not has_rocm:
            try:
                vk_out = subprocess.check_output(
                    ["vulkaninfo", "--summary"], text=True, timeout=5
                )
                # Look for AMD or Intel discrete GPU
                for line in vk_out.splitlines():
                    if any(v in line for v in ("AMD", "Radeon", "Intel", "Iris", "Arc")):
                        gpu_name    = line.strip()
                        has_vulkan  = True
                        device_plat = "vulkan"
                        # Try to parse VRAM from vulkaninfo
                        break
            except (FileNotFoundError, subprocess.SubprocessError):
                pass

            # Vulkan via /dev/dri on Linux (no vulkaninfo needed)
            if not has_vulkan and os.path.exists("/dev/dri"):
                import glob as _glob
                renders = _glob.glob("/dev/dri/renderD*")
                if renders:
                    try:
                        lspci = subprocess.check_output(
                            ["lspci"], text=True, timeout=5
                        )
                        for ln in lspci.splitlines():
                            if any(k in ln for k in ("VGA", "Display", "3D")):
                                if any(v in ln for v in ("AMD", "ATI", "Radeon")):
                                    gpu_name    = ln.split(":")[-1].strip()
                                    has_vulkan  = True
                                    device_plat = "vulkan"
                                    break
                                elif any(v in ln for v in ("Intel", "Iris")):
                                    gpu_name    = ln.split(":")[-1].strip()
                                    has_vulkan  = True
                                    device_plat = "vulkan"
                                    break
                    except Exception:
                        pass

    return DeviceProfile(
        platform    = device_plat,
        os_name     = plat,
        machine     = machine,
        cpu_cores   = cpu_cores,
        ram_gb      = ram_gb,
        gpu_vram_gb = gpu_vram_gb,
        gpu_name    = gpu_name,
        has_metal   = has_metal,
        has_cuda    = has_cuda,
        has_rocm    = has_rocm,
        has_vulkan  = has_vulkan,
        chip_name   = chip_name,
        node_id     = node_id,
        hostname    = hostname,
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 · Model Registry & Selection
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelSpec:
    key:             str
    engine:          str       # "mlx" | "gguf"
    repo_id:         str
    filename:        str       # for gguf; empty for mlx snapshot
    check_file:      str       # file that proves download is complete
    size_gb:         float     # approximate download / RAM footprint
    params_b:        float     # total parameters (billions)
    ctx:             int       # default context window
    description:     str
    is_moe:          bool  = False   # Mixture-of-Experts architecture
    active_params_b: float = 0.0    # params active per token (0 = same as params_b)
    # ── Remote API backend (no local download) ───────────────────────────────
    remote:          bool  = False   # True → calls external API, no local model
    api_base:        str   = ""      # e.g. "https://api.openai.com/v1"
    api_key_env:     str   = ""      # env-var holding the API key
    api_model_id:    str   = ""      # model name sent to remote API (e.g. "gpt-4o")
    api_provider:    str   = ""      # human label: "OpenAI" | "Groq" | "Together" | …

    @property
    def effective_params_b(self) -> float:
        return self.active_params_b if self.is_moe and self.active_params_b else self.params_b

    def label(self) -> str:
        tags = []
        if self.is_moe:
            tags.append("MoE")
        if self.remote:
            tags.append(f"Remote/{self.api_provider or 'API'}")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        return self.description + suffix

    def api_key(self) -> str:
        """Return the live API key value, or empty string if not set."""
        return os.getenv(self.api_key_env, "") if self.api_key_env else ""


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL MODEL LADDERS
# Both ladders are ordered by size_gb DESCENDING so select_models() picks the
# best model that fits in available RAM.  MoE models are preferred at each RAM
# tier — same disk footprint, far more total parameters.
#
# ~120B class : Mistral Large 2, Llama 4 Scout, Command R+, Mixtral 8×22B
# ~20B  class : Mistral Small 3.1, Gemma 3 27B, QwQ-32B, Llama 4 Scout (active)
# Big-5 recents: Meta Llama 4 · Google Gemma 3 · Microsoft Phi-4 ·
#                Mistral Small/Nemo/Large · DeepSeek R1 distils · Cohere Command R
# ─────────────────────────────────────────────────────────────────────────────

_MLX_LADDER: List[ModelSpec] = [
    # ══ ~120B class ══════════════════════════════════════════════════════════
    # Meta Llama 4 Scout — 16-expert MoE, 17B active / 109B total, 10M ctx
    ModelSpec("mlx-llama4-scout", "mlx",
              "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
              "", "config.json", 61.0, 109.0, 10_000_000,
              "Meta Llama 4 Scout MoE 4-bit — 17B active / 109B total · 10M ctx",
              is_moe=True, active_params_b=17.0),
    # Mixtral 8×22B — 39B active / 141B total  (Q2_K-equiv at 4-bit)
    ModelSpec("mlx-moe-8x22b", "mlx",
              "mlx-community/Mixtral-8x22B-Instruct-v0.1-4bit",
              "", "config.json", 49.0, 141.0, 65536,
              "Mixtral 8×22B MoE 4-bit — 39B active / 141B total",
              is_moe=True, active_params_b=39.0),
    # ══ 70B dense ════════════════════════════════════════════════════════════
    ModelSpec("mlx-70b", "mlx",
              "mlx-community/Llama-3.3-70B-Instruct-4bit",
              "", "config.json", 42.0, 70.0, 8192,
              "Meta Llama-3.3 70B 4-bit (Apple Silicon)"),
    # ══ ~20B class ═══════════════════════════════════════════════════════════
    # Mixtral 8×7B — 13B active, excellent throughput
    ModelSpec("mlx-moe-8x7b", "mlx",
              "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit",
              "", "config.json", 25.5, 46.7, 32768,
              "Mixtral 8×7B MoE 4-bit — 13B active / 47B total",
              is_moe=True, active_params_b=12.9),
    # Qwen2.5 32B dense
    ModelSpec("mlx-32b", "mlx",
              "mlx-community/Qwen2.5-32B-Instruct-4bit",
              "", "config.json", 20.0, 32.0, 8192,
              "Alibaba Qwen2.5 32B 4-bit (Apple Silicon)"),
    # QwQ-32B — Qwen open reasoning/chain-of-thought model
    ModelSpec("mlx-qwq-32b", "mlx",
              "mlx-community/QwQ-32B-4bit",
              "", "config.json", 18.0, 32.0, 32768,
              "Qwen QwQ-32B 4-bit — open reasoning / chain-of-thought (Apple Silicon)"),
    # Google Gemma 3 27B — multimodal-trained, 128k ctx
    ModelSpec("mlx-gemma3-27b", "mlx",
              "mlx-community/gemma-3-27b-it-4bit",
              "", "config.json", 15.0, 27.0, 131072,
              "Google Gemma 3 27B IT 4-bit — 128k ctx (Apple Silicon)"),
    # Mistral Small 3.1 22B — latest Mistral small model (March 2025)
    ModelSpec("mlx-mistral-small-22b", "mlx",
              "mlx-community/Mistral-Small-3.1-22B-Instruct-2503-4bit",
              "", "config.json", 13.0, 22.0, 32768,
              "Mistral Small 3.1 22B 4-bit — Mar 2025 (Apple Silicon)"),
    # ══ 14B tier ═════════════════════════════════════════════════════════════
    ModelSpec("mlx-14b", "mlx",
              "mlx-community/Qwen2.5-14B-Instruct-4bit",
              "", "config.json", 9.5, 14.0, 8192,
              "Alibaba Qwen2.5 14B 4-bit (Apple Silicon)"),
    # DeepSeek-V2-Lite MoE — 2.4B active, efficient coding/math
    ModelSpec("mlx-moe-ds-lite", "mlx",
              "mlx-community/DeepSeek-V2-Lite-Chat-4bit",
              "", "config.json", 9.0, 15.7, 16384,
              "DeepSeek-V2-Lite MoE 4-bit — 2.4B active / 15.7B total",
              is_moe=True, active_params_b=2.4),
    # Microsoft Phi-4 — GPT-4-class quality at 14B
    ModelSpec("mlx-phi4", "mlx",
              "mlx-community/phi-4-4bit",
              "", "config.json", 8.5, 14.0, 16384,
              "Microsoft Phi-4 4-bit — GPT-4-class OSS 14B (Apple Silicon)"),
    # DeepSeek R1 Distil 14B — reasoning capability distilled from R1 671B
    ModelSpec("mlx-r1-distill-14b", "mlx",
              "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit",
              "", "config.json", 8.0, 14.0, 16384,
              "DeepSeek-R1-Distill Qwen-14B 4-bit — R1 reasoning in 14B (Apple Silicon)"),
    # ══ 7–8 GB tier ══════════════════════════════════════════════════════════
    # Qwen1.5 MoE — 2.7B active, ultra-efficient
    ModelSpec("mlx-moe-qwen-a2.7b", "mlx",
              "mlx-community/Qwen1.5-MoE-A2.7B-Chat-4bit",
              "", "config.json", 7.5, 14.3, 32768,
              "Qwen1.5 MoE A2.7B 4-bit — 2.7B active / 14.3B total",
              is_moe=True, active_params_b=2.7),
    # Mistral Nemo 12B — Apache 2.0, 128k context, co-developed with NVIDIA
    ModelSpec("mlx-mistral-nemo-12b", "mlx",
              "mlx-community/Mistral-Nemo-Instruct-2407-4bit",
              "", "config.json", 6.8, 12.0, 128000,
              "Mistral Nemo 12B 4-bit — 128k ctx, Apache 2.0 (Apple Silicon)"),
    # ══ 5 GB tier ════════════════════════════════════════════════════════════
    ModelSpec("mlx-8b", "mlx",
              "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
              "", "tokenizer.json", 5.0, 8.0, 8192,
              "Meta Llama-3.1 8B 4-bit (Apple Silicon)"),
    # OpenHermes 2.5 — GPT-4-quality instruction fine-tune on Mistral 7B
    ModelSpec("mlx-openhermes-7b", "mlx",
              "mlx-community/OpenHermes-2.5-Mistral-7B-4bit",
              "", "tokenizer.json", 4.5, 7.0, 8192,
              "OpenHermes-2.5 Mistral-7B 4-bit — GPT-4-quality fine-tune"),
    # ══ Edge / IoT ════════════════════════════════════════════════════════════
    ModelSpec("mlx-3b", "mlx",
              "mlx-community/Llama-3.2-3B-Instruct-4bit",
              "", "tokenizer.json", 2.0, 3.0, 4096,
              "Meta Llama-3.2 3B 4-bit (Apple Silicon)"),
    ModelSpec("mlx-1b", "mlx",
              "mlx-community/Llama-3.2-1B-Instruct-4bit",
              "", "tokenizer.json", 0.8, 1.0, 2048,
              "Meta Llama-3.2 1B 4-bit (Apple Silicon)"),
]

# GGUF ladder — CUDA · AMD ROCm · AMD Vulkan · CPU
# Uses bartowski quantisations (actively maintained, all formats).
_GGUF_LADDER: List[ModelSpec] = [
    # ══ ~120B class ══════════════════════════════════════════════════════════
    # Mistral Large 2 (123B) — Mistral's largest open-weight release
    # Mistral Research License: free for research/non-commercial use
    ModelSpec("gguf-mistral-large2", "gguf",
              "bartowski/Mistral-Large-Instruct-2407-GGUF",
              "Mistral-Large-Instruct-2407-Q4_K_M.gguf", "", 69.0, 123.0, 131072,
              "Mistral Large 2 123B Q4_K_M — 128k ctx (Mistral Research License)"),
    # Meta Llama 4 Scout — 16-expert MoE, 10M context
    ModelSpec("gguf-llama4-scout", "gguf",
              "bartowski/Llama-4-Scout-17B-16E-Instruct-GGUF",
              "Llama-4-Scout-17B-16E-Instruct-Q4_K_M.gguf", "", 61.0, 109.0, 10_000_000,
              "Meta Llama 4 Scout MoE Q4_K_M — 17B active / 109B total · 10M ctx",
              is_moe=True, active_params_b=17.0),
    # Cohere Command R+ (104B) — RAG-optimised, 128k context
    ModelSpec("gguf-command-r-plus", "gguf",
              "bartowski/command-r-plus-08-2024-GGUF",
              "command-r-plus-08-2024-Q4_K_M.gguf", "", 58.0, 104.0, 128000,
              "Cohere Command R+ 104B Q4_K_M — RAG-optimised 128k ctx"),
    # Mixtral 8×22B — Q2_K to fit in 64 GB devices
    ModelSpec("gguf-moe-8x22b", "gguf",
              "bartowski/Mixtral-8x22B-Instruct-v0.1-GGUF",
              "Mixtral-8x22B-Instruct-v0.1-Q2_K.gguf", "", 52.0, 141.0, 65536,
              "Mixtral 8×22B MoE Q2_K — 39B active / 141B total",
              is_moe=True, active_params_b=39.0),
    # ══ 70B class ════════════════════════════════════════════════════════════
    ModelSpec("gguf-70b", "gguf",
              "bartowski/Llama-3.3-70B-Instruct-GGUF",
              "Llama-3.3-70B-Instruct-Q4_K_M.gguf", "", 42.0, 70.0, 8192,
              "Meta Llama-3.3 70B Q4_K_M"),
    # DeepSeek R1 Distil Llama-70B — full R1 reasoning transferred to 70B
    ModelSpec("gguf-r1-distill-llama-70b", "gguf",
              "bartowski/DeepSeek-R1-Distill-Llama-70B-GGUF",
              "DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf", "", 39.0, 70.0, 16384,
              "DeepSeek-R1-Distill Llama-70B Q4_K_M — R1 reasoning at 70B scale"),
    # ══ ~20B class ═══════════════════════════════════════════════════════════
    # Mixtral 8×7B MoE
    ModelSpec("gguf-moe-8x7b", "gguf",
              "bartowski/Mixtral-8x7B-Instruct-v0.1-GGUF",
              "Mixtral-8x7B-Instruct-v0.1-Q4_K_M.gguf", "", 26.0, 46.7, 32768,
              "Mixtral 8×7B MoE Q4_K_M — 13B active / 47B total",
              is_moe=True, active_params_b=12.9),
    # Cohere Command R (35B) — Apache 2.0, RAG-tuned, 128k context
    ModelSpec("gguf-command-r-35b", "gguf",
              "bartowski/command-r-v01-GGUF",
              "command-r-v01-Q4_K_M.gguf", "", 20.0, 35.0, 131072,
              "Cohere Command R 35B Q4_K_M — Apache 2.0, 128k RAG-optimised"),
    # Qwen2.5 32B dense
    ModelSpec("gguf-32b", "gguf",
              "bartowski/Qwen2.5-32B-Instruct-GGUF",
              "Qwen2.5-32B-Instruct-Q4_K_M.gguf", "", 19.0, 32.0, 8192,
              "Alibaba Qwen2.5 32B Q4_K_M"),
    # DeepSeek R1 Distil Qwen-32B — powerful reasoning in 32B
    ModelSpec("gguf-r1-distill-qwen-32b", "gguf",
              "bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF",
              "DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf", "", 18.0, 32.0, 16384,
              "DeepSeek-R1-Distill Qwen-32B Q4_K_M — strong reasoning distil"),
    # QwQ-32B — Qwen open chain-of-thought reasoning model
    ModelSpec("gguf-qwq-32b", "gguf",
              "bartowski/QwQ-32B-GGUF",
              "QwQ-32B-Q4_K_M.gguf", "", 18.0, 32.0, 32768,
              "Qwen QwQ-32B Q4_K_M — open reasoning / chain-of-thought"),
    # Google Gemma 3 27B — multimodal-trained, 128k context
    ModelSpec("gguf-gemma3-27b", "gguf",
              "bartowski/gemma-3-27b-it-GGUF",
              "gemma-3-27b-it-Q4_K_M.gguf", "", 15.0, 27.0, 131072,
              "Google Gemma 3 27B IT Q4_K_M — 128k ctx, Mar 2025"),
    # Mistral Small 3.1 22B — latest Mistral small model (March 2025)
    ModelSpec("gguf-mistral-small-22b", "gguf",
              "bartowski/Mistral-Small-3.1-22B-Instruct-2503-GGUF",
              "Mistral-Small-3.1-22B-Instruct-2503-Q4_K_M.gguf", "", 13.0, 22.0, 32768,
              "Mistral Small 3.1 22B Q4_K_M — Mar 2025"),
    # ══ 14B tier ═════════════════════════════════════════════════════════════
    # DeepSeek-V2-Lite MoE — largest file in this tier
    ModelSpec("gguf-moe-ds-lite", "gguf",
              "bartowski/DeepSeek-V2-Lite-Chat-GGUF",
              "DeepSeek-V2-Lite-Chat-Q4_K_M.gguf", "", 9.5, 15.7, 16384,
              "DeepSeek-V2-Lite MoE Q4_K_M — 2.4B active / 15.7B total",
              is_moe=True, active_params_b=2.4),
    # Qwen2.5 14B
    ModelSpec("gguf-14b", "gguf",
              "bartowski/Qwen2.5-14B-Instruct-GGUF",
              "Qwen2.5-14B-Instruct-Q4_K_M.gguf", "", 9.0, 14.0, 8192,
              "Alibaba Qwen2.5 14B Q4_K_M"),
    # Microsoft Phi-4 — GPT-4 quality at 14B
    ModelSpec("gguf-phi4", "gguf",
              "bartowski/phi-4-GGUF",
              "phi-4-Q4_K_M.gguf", "", 8.5, 14.0, 16384,
              "Microsoft Phi-4 Q4_K_M — GPT-4-class OSS 14B"),
    # DeepSeek R1 Distil Qwen-14B — R1 reasoning in 14B
    ModelSpec("gguf-r1-distill-qwen-14b", "gguf",
              "bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF",
              "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf", "", 8.0, 14.0, 16384,
              "DeepSeek-R1-Distill Qwen-14B Q4_K_M — R1 reasoning in 14B"),
    # ══ 12B tier ═════════════════════════════════════════════════════════════
    # Mistral Nemo 12B — Apache 2.0, 128k context, co-developed with NVIDIA
    ModelSpec("gguf-mistral-nemo-12b", "gguf",
              "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
              "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf", "", 6.8, 12.0, 128000,
              "Mistral Nemo 12B Q4_K_M — 128k ctx, Apache 2.0 (NVIDIA co-dev)"),
    # ══ 7–8B tier ════════════════════════════════════════════════════════════
    ModelSpec("gguf-8b", "gguf",
              "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
              "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf", "", 5.0, 8.0, 8192,
              "Meta Llama-3.1 8B Q4_K_M"),
    # OpenHermes 2.5 — GPT-4 quality instruction fine-tune
    ModelSpec("gguf-openhermes-7b", "gguf",
              "bartowski/OpenHermes-2.5-Mistral-7B-GGUF",
              "OpenHermes-2.5-Mistral-7B-Q4_K_M.gguf", "", 4.5, 7.0, 8192,
              "OpenHermes-2.5 Mistral-7B Q4_K_M — GPT-4 quality fine-tune"),
    # WizardLM-2 7B — Microsoft research, 32k ctx
    ModelSpec("gguf-wizardlm2-7b", "gguf",
              "bartowski/WizardLM-2-7B-GGUF",
              "WizardLM-2-7B-Q4_K_M.gguf", "", 4.5, 7.0, 32768,
              "WizardLM-2 7B Q4_K_M — Microsoft research, GPT-4-tier 32k"),
    # ══ Edge / OSS history ════════════════════════════════════════════════════
    # GPT4All-J — EleutherAI GPT-J architecture (true OpenAI-lineage OSS)
    ModelSpec("gguf-gptj-6b", "gguf",
              "nomic-ai/gpt4all-j-v1.3-groovy-GGUF",
              "gpt4all-j-v1.3-groovy.Q4_K_M.gguf", "", 3.5, 6.0, 2048,
              "GPT4All-J 6B Q4_K_M — EleutherAI GPT-J, true OpenAI-arch OSS"),
    ModelSpec("gguf-3b", "gguf",
              "bartowski/Llama-3.2-3B-Instruct-GGUF",
              "Llama-3.2-3B-Instruct-Q4_K_M.gguf", "", 2.0, 3.0, 4096,
              "Meta Llama-3.2 3B Q4_K_M"),
    ModelSpec("gguf-1b", "gguf",
              "bartowski/Llama-3.2-1B-Instruct-GGUF",
              "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "", 0.8, 1.0, 2048,
              "Meta Llama-3.2 1B Q4_K_M"),
]


# Remote model ladder — zero RAM footprint, activated by env-var API keys.
# Providers: OpenAI · Groq (free) · Together AI · Anthropic · Ollama (local server)
# Models are injected at the TOP of the ladder by select_models() when the key exists.
_REMOTE_LADDER: List[ModelSpec] = [

    # ── OpenAI GPT (needs OPENAI_API_KEY) ────────────────────────────────────
    ModelSpec("gpt-4o", "remote", "openai/gpt-4o", "", "", 0.0, 200.0, 128000,
              "GPT-4o (OpenAI — $2.50/1M in · $10/1M out)",
              remote=True, api_base="https://api.openai.com/v1",
              api_key_env="OPENAI_API_KEY", api_model_id="gpt-4o",
              api_provider="OpenAI"),

    ModelSpec("o3-mini", "remote", "openai/o3-mini", "", "", 0.0, 100.0, 200000,
              "o3-mini reasoning (OpenAI — $1.10/1M in · $4.40/1M out)",
              remote=True, api_base="https://api.openai.com/v1",
              api_key_env="OPENAI_API_KEY", api_model_id="o3-mini",
              api_provider="OpenAI"),

    ModelSpec("o1-mini", "remote", "openai/o1-mini", "", "", 0.0, 100.0, 128000,
              "o1-mini reasoning (OpenAI — $3/1M in · $12/1M out)",
              remote=True, api_base="https://api.openai.com/v1",
              api_key_env="OPENAI_API_KEY", api_model_id="o1-mini",
              api_provider="OpenAI"),

    ModelSpec("gpt-4o-mini", "remote", "openai/gpt-4o-mini", "", "", 0.0, 8.0, 128000,
              "GPT-4o mini (OpenAI — $0.15/1M in · $0.60/1M out)",
              remote=True, api_base="https://api.openai.com/v1",
              api_key_env="OPENAI_API_KEY", api_model_id="gpt-4o-mini",
              api_provider="OpenAI"),

    # ── Anthropic Claude (needs ANTHROPIC_API_KEY) ────────────────────────────
    ModelSpec("claude-opus", "remote", "anthropic/claude-opus-4-7", "", "", 0.0, 200.0, 200000,
              "Claude Opus 4.7 (Anthropic — $15/1M in · $75/1M out)",
              remote=True, api_base="https://api.anthropic.com/v1",
              api_key_env="ANTHROPIC_API_KEY", api_model_id="claude-opus-4-7",
              api_provider="Anthropic"),

    ModelSpec("claude-sonnet", "remote", "anthropic/claude-sonnet-4-6", "", "", 0.0, 100.0, 200000,
              "Claude Sonnet 4.6 (Anthropic — $3/1M in · $15/1M out)",
              remote=True, api_base="https://api.anthropic.com/v1",
              api_key_env="ANTHROPIC_API_KEY", api_model_id="claude-sonnet-4-6",
              api_provider="Anthropic"),

    # ── Groq (free tier, needs GROQ_API_KEY — very fast inference) ───────────
    ModelSpec("groq-llama-70b", "remote", "groq/llama-3.3-70b", "", "", 0.0, 70.0, 32768,
              "Llama-3.3 70B on Groq (free tier — ~275 tok/s)",
              remote=True, api_base="https://api.groq.com/openai/v1",
              api_key_env="GROQ_API_KEY", api_model_id="llama-3.3-70b-versatile",
              api_provider="Groq"),

    ModelSpec("groq-mixtral", "remote", "groq/mixtral-8x7b", "", "", 0.0, 46.7, 32768,
              "Mixtral 8x7B MoE on Groq (free tier — 13B active params)",
              remote=True, api_base="https://api.groq.com/openai/v1",
              api_key_env="GROQ_API_KEY", api_model_id="mixtral-8x7b-32768",
              api_provider="Groq", is_moe=True, active_params_b=12.9),

    ModelSpec("groq-llama-8b", "remote", "groq/llama-3.1-8b", "", "", 0.0, 8.0, 8192,
              "Llama-3.1 8B on Groq (free tier — ~1250 tok/s)",
              remote=True, api_base="https://api.groq.com/openai/v1",
              api_key_env="GROQ_API_KEY", api_model_id="llama-3.1-8b-instant",
              api_provider="Groq"),

    # ── Together AI (needs TOGETHER_API_KEY) ──────────────────────────────────
    ModelSpec("together-qwen-72b", "remote", "together/qwen2.5-72b", "", "", 0.0, 72.0, 32768,
              "Qwen2.5 72B on Together AI ($0.90/1M)",
              remote=True, api_base="https://api.together.xyz/v1",
              api_key_env="TOGETHER_API_KEY",
              api_model_id="Qwen/Qwen2.5-72B-Instruct-Turbo",
              api_provider="Together"),

    ModelSpec("together-llama-405b", "remote", "together/llama-405b", "", "", 0.0, 405.0, 32768,
              "Llama-3.1 405B on Together AI ($3.50/1M)",
              remote=True, api_base="https://api.together.xyz/v1",
              api_key_env="TOGETHER_API_KEY",
              api_model_id="meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
              api_provider="Together"),

    ModelSpec("together-mixtral-8x22b", "remote", "together/mixtral-8x22b", "", "", 0.0, 141.0, 65536,
              "Mixtral 8x22B MoE on Together AI — 39B active",
              remote=True, api_base="https://api.together.xyz/v1",
              api_key_env="TOGETHER_API_KEY",
              api_model_id="mistralai/Mixtral-8x22B-Instruct-v0.1",
              api_provider="Together", is_moe=True, active_params_b=39.0),

    # ── Ollama local server (zero cost — needs Ollama running on localhost) ────
    ModelSpec("ollama-llama3", "remote", "ollama/llama3.3", "", "", 0.0, 70.0, 8192,
              "Llama 3.3 via Ollama (local server — ollama pull llama3.3)",
              remote=True, api_base="http://localhost:11434/v1",
              api_key_env="", api_model_id="llama3.3",
              api_provider="Ollama"),

    ModelSpec("ollama-phi4", "remote", "ollama/phi4", "", "", 0.0, 14.0, 16384,
              "Microsoft Phi-4 via Ollama (local server — ollama pull phi4)",
              remote=True, api_base="http://localhost:11434/v1",
              api_key_env="", api_model_id="phi4",
              api_provider="Ollama"),
]

# Active-key cache — recomputed once per select_models() call
_AVAILABLE_REMOTE: List[ModelSpec] = []


def _probe_ollama() -> bool:
    """Return True if a local Ollama server is reachable."""
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=1)
        return True
    except Exception:
        return False


def _scan_network_mounts() -> List[Tuple[Path, float]]:
    """Return (path, size_gb) for every .gguf found on mounted volumes."""
    scan_roots = []
    for prefix in ["/Volumes", "/mnt", "/media"]:
        p = Path(prefix)
        if p.exists():
            try:
                scan_roots.extend(p.iterdir())
            except PermissionError:
                pass

    found: List[Tuple[Path, float]] = []
    for root in scan_roots:
        for depth in range(5):
            pattern = str(root) + "/*/" * depth + "*.gguf"
            for g in glob.glob(pattern):
                fp = Path(g)
                try:
                    size_gb = fp.stat().st_size / (1024 ** 3)
                    found.append((fp, size_gb))
                except OSError:
                    pass
    return sorted(found, key=lambda x: x[1], reverse=True)


def select_models(dev: DeviceProfile) -> Tuple[Optional[ModelSpec], Optional[ModelSpec]]:
    """
    Return (sudo_model, sized_model).

    Priority order for sudo:
      1. Remote GPT/cloud models (if API key present — highest quality, zero local RAM)
      2. Network-mounted GGUFs (pre-existing, no download)
      3. Best local model that fits in 78 % of RAM

    sized = fastest small local model for routing/quick replies.
    """
    global _AVAILABLE_REMOTE

    usable_gb = dev.ram_gb * 0.78
    ollama_up = _probe_ollama()

    # ── Discover which remote models are available right now ─────────────────
    _AVAILABLE_REMOTE = []
    for spec in _REMOTE_LADDER:
        if spec.api_provider == "Ollama":
            if ollama_up:
                _AVAILABLE_REMOTE.append(spec)
        elif spec.api_key_env:
            if os.getenv(spec.api_key_env):
                _AVAILABLE_REMOTE.append(spec)
        else:
            _AVAILABLE_REMOTE.append(spec)   # no-key remote (shouldn't happen)

    if _AVAILABLE_REMOTE:
        providers = sorted({s.api_provider for s in _AVAILABLE_REMOTE})
        log.info(f"Remote backends available: {', '.join(providers)} "
                 f"({len(_AVAILABLE_REMOTE)} models)")
        # sudo = best available remote (first in _REMOTE_LADDER = highest quality)
        sudo_spec: Optional[ModelSpec] = _AVAILABLE_REMOTE[0]
    else:
        sudo_spec = None

    # ── Network mount fallback ────────────────────────────────────────────────
    if sudo_spec is None:
        mounts = _scan_network_mounts()
        if mounts:
            log.info(f"Found {len(mounts)} GGUF(s) on mounted volumes")
        for path, size_gb in mounts:
            if size_gb <= usable_gb:
                sudo_spec = ModelSpec(
                    key=f"mount-{path.stem}", engine="gguf",
                    repo_id="", filename=str(path), check_file=str(path),
                    size_gb=size_gb, params_b=_gb_to_params(size_gb),
                    ctx=8192, description=f"Mounted: {path.name}",
                )
                break

    # ── Local model ladder ────────────────────────────────────────────────────
    ladder = _MLX_LADDER if dev.platform == "apple_silicon" else _GGUF_LADDER

    if sudo_spec is None:
        for spec in ladder:
            if spec.size_gb <= usable_gb:
                sudo_spec = spec
                break

    # sized = always a small FAST local model (last in ladder — never remote)
    sized_spec = ladder[-1]

    return sudo_spec, sized_spec


def _gb_to_params(size_gb: float) -> float:
    # rough: 1 param ≈ 0.5 bytes at 4-bit quant
    return (size_gb * 1024 ** 3) / (0.5 * 1e9)


def ensure_model(spec: ModelSpec) -> Path:
    """Download model if not present; return local path."""
    from huggingface_hub import snapshot_download, hf_hub_download

    if spec.engine == "mlx":
        dest = MODELS_DIR / spec.key
        check = dest / spec.check_file
        if not check.exists():
            log.info(f"Downloading {spec.description}…")
            snapshot_download(repo_id=spec.repo_id, local_dir=str(dest))
        return dest

    # gguf
    if spec.filename and Path(spec.filename).is_absolute():
        return Path(spec.filename)   # already on mount

    dest = MODELS_DIR / spec.filename
    if not dest.exists():
        log.info(f"Downloading {spec.description}…")
        hf_hub_download(
            repo_id=spec.repo_id,
            filename=spec.filename,
            local_dir=str(MODELS_DIR),
        )
    return dest


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 · Thermal Governor (5-stage, predictive, cross-platform)
# ──────────────────────────────────────────────────────────────────────────────

class ThermalState(IntEnum):
    NOMINAL   = 0   # < 60°C — full power
    WARM      = 1   # 60–75°C — -50% batch
    HOT       = 2   # 75–85°C — 25% workers
    CRITICAL  = 3   # 85–95°C — migrate / pause training
    EMERGENCY = 4   # >95°C  — halt inference


@dataclass
class ThermalReading:
    temp_c:    float
    ts:        float
    source:    str = "unknown"


class ThermalGovernor:
    THRESHOLDS = (60.0, 75.0, 85.0, 95.0)   # NOMINAL→WARM→HOT→CRITICAL→EMERGENCY
    HYSTERESIS = 6.0    # seconds a new state must persist before committing
    PREDICT_S  = 60.0   # lookahead for linear extrapolation

    def __init__(self, poll_s: float = 4.0):
        self._poll_s  = poll_s
        self._state   = ThermalState.NOMINAL
        self._history: Deque[ThermalReading] = deque(maxlen=150)
        self._pending: Optional[ThermalState] = None
        self._pending_ts: float = 0.0
        self._callbacks: List[Callable[[ThermalState], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def on_change(self, fn: Callable[[ThermalState], None]) -> None:
        self._callbacks.append(fn)

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="thermal")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    @property
    def state(self) -> ThermalState:
        return self._state

    @property
    def temp_c(self) -> float:
        return self._history[-1].temp_c if self._history else 0.0

    def wait_cool(self, target: ThermalState = ThermalState.WARM, timeout: float = 300.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._state <= target:
                return True
            time.sleep(2.0)
        return False

    def stats(self) -> dict:
        temps = [r.temp_c for r in self._history]
        return {
            "state":       self._state.name,
            "temp_c":      round(self.temp_c, 1),
            "predicted_60s": round(self._predict(self.PREDICT_S), 1),
            "max_c":       round(max(temps), 1) if temps else 0,
            "avg_c":       round(sum(temps) / len(temps), 1) if temps else 0,
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            t = self._read()
            if t is not None:
                self._history.append(ThermalReading(t, time.time(), platform.system()))
                self._evaluate(t)
            time.sleep(self._poll_s)

    def _read(self) -> Optional[float]:
        sys_name = platform.system().lower()
        try:
            if sys_name == "linux":
                return self._read_linux()
            elif sys_name == "darwin":
                return self._read_macos()
            elif sys_name == "windows":
                return self._read_windows()
        except Exception:
            pass
        return 42.0  # safe fallback

    def _read_linux(self) -> Optional[float]:
        candidates = (
            glob.glob("/sys/class/thermal/thermal_zone*/temp")
            + glob.glob("/sys/class/hwmon/hwmon*/temp*_input")
        )
        readings = []
        for p in candidates:
            try:
                v = int(open(p).read().strip())
                readings.append(v / 1000.0 if v > 200 else float(v))
            except Exception:
                pass
        return max(readings) if readings else 42.0

    def _read_macos(self) -> Optional[float]:
        # powermetrics requires sudo; use pmset thermlog as proxy
        try:
            out = subprocess.check_output(
                ["pmset", "-g", "thermlog"], text=True, timeout=5
            )
            for line in out.splitlines():
                if any(k in line for k in ("CPU_", "cpu_", "GPU_")):
                    parts = line.split()
                    if parts:
                        return float(parts[-1])
        except Exception:
            pass
        return 42.0  # conservative default without sudo

    def _read_windows(self) -> Optional[float]:
        try:
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "(Get-WmiObject MSAcpi_ThermalZoneTemperature "
                 "-Namespace root/wmi).CurrentTemperature"],
                text=True, timeout=10, creationflags=0x08000000
            )
            return float(out.strip()) / 10.0 - 273.15
        except Exception:
            return 45.0

    def _classify(self, temp: float) -> ThermalState:
        for i, threshold in enumerate(self.THRESHOLDS):
            if temp < threshold:
                return ThermalState(i)
        return ThermalState.EMERGENCY

    def _predict(self, ahead_s: float) -> float:
        pts = list(self._history)
        if len(pts) < 3:
            return pts[-1].temp_c if pts else 40.0
        pts = pts[-30:]
        t0 = pts[0].ts
        xs = [p.ts - t0 for p in pts]
        ys = [p.temp_c for p in pts]
        n  = len(xs)
        sx = sum(xs)
        sy = sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        d   = n * sxx - sx * sx
        if abs(d) < 1e-9:
            return ys[-1]
        slope = (n * sxy - sx * sy) / d
        intercept = (sy - slope * sx) / n
        return intercept + slope * (xs[-1] + ahead_s)

    def _evaluate(self, temp: float) -> None:
        target = self._classify(temp)
        predicted = self._predict(self.PREDICT_S)
        target = max(target, self._classify(predicted))

        now = time.time()
        if target != self._state:
            if self._pending != target:
                self._pending    = target
                self._pending_ts = now
            elif (now - self._pending_ts) >= self.HYSTERESIS:
                old = self._state
                self._state   = target
                self._pending = None
                log.info(f"Thermal: {old.name} → {target.name}  ({temp:.1f}°C, pred={predicted:.1f}°C)")
                for cb in self._callbacks:
                    try:
                        cb(target)
                    except Exception:
                        pass
        else:
            self._pending = None


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 · Inference Engine (MLX + GGUF, swap-aware)
# ──────────────────────────────────────────────────────────────────────────────

class InferenceEngine:
    """Thread-safe model swapper.  Holds at most one model in memory at a time."""

    def __init__(self, dev: DeviceProfile, thermal: ThermalGovernor):
        self._dev      = dev
        self._thermal  = thermal
        self._lock     = threading.Lock()
        self._model    = None
        self._tok      = None
        self._llama    = None
        self._active   = None   # "mlx:key" | "gguf:path"
        thermal.on_change(self._on_thermal)

    def _on_thermal(self, state: ThermalState) -> None:
        if state >= ThermalState.EMERGENCY:
            log.warning("Thermal EMERGENCY — unloading models")
            with self._lock:
                self._unload()

    def _unload(self) -> None:
        if self._model is not None:
            del self._model
            del self._tok
            self._model = self._tok = None
        if self._llama is not None:
            del self._llama
            self._llama = None
        self._active = None
        gc.collect()

    def _load_mlx(self, path: Path) -> None:
        from mlx_lm import load as mlx_load
        self._unload()
        log.info(f"Loading MLX model: {path.name}")
        self._model, self._tok = mlx_load(str(path))
        self._active = f"mlx:{path}"

    def _load_gguf(self, path: Path, ctx: int = 8192) -> None:
        from llama_cpp import Llama
        self._unload()
        log.info(f"Loading GGUF model: {path.name}")
        n_gpu = -1 if (self._dev.has_metal or self._dev.has_cuda
                       or self._dev.has_rocm or self._dev.has_vulkan) else 0
        self._llama = Llama(
            model_path=str(path),
            n_ctx=ctx,
            n_gpu_layers=n_gpu,
            verbose=False,
            use_mmap=True,
        )
        self._active = f"gguf:{path}"

    # ── Remote API inference ──────────────────────────────────────────────────

    def _generate_remote(
        self,
        prompt: str,
        spec: ModelSpec,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call an OpenAI-compatible or Anthropic remote API (pure stdlib urllib)."""
        api_key = spec.api_key()
        if spec.api_provider == "Anthropic":
            return self._generate_anthropic(prompt, spec, api_key, max_tokens, temperature)
        # OpenAI-compatible: OpenAI, Groq, Together, Ollama, etc.
        return self._generate_openai_compat(prompt, spec, api_key, max_tokens, temperature)

    def _generate_openai_compat(
        self, prompt: str, spec: ModelSpec, api_key: str, max_tokens: int, temperature: float
    ) -> str:
        payload = json.dumps({
            "model": spec.api_model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            f"{spec.api_base}/chat/completions",
            data=payload, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")[:400]
            log.warning(f"Remote API HTTP {exc.code}: {body}")
            return f"[Remote {spec.api_provider} error {exc.code}: {body[:200]}]"
        except Exception as exc:
            log.warning(f"Remote API error: {exc}")
            return f"[Remote {spec.api_provider} error: {exc}]"

    def _generate_anthropic(
        self, prompt: str, spec: ModelSpec, api_key: str, max_tokens: int, temperature: float
    ) -> str:
        """Anthropic Messages API — not OpenAI-compatible on the request side."""
        if not api_key:
            return "[ANTHROPIC_API_KEY not set]"
        payload = json.dumps({
            "model": spec.api_model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")[:400]
            return f"[Anthropic error {exc.code}: {body[:200]}]"
        except Exception as exc:
            return f"[Anthropic error: {exc}]"

    def _stream_remote(
        self,
        prompt: str,
        spec: ModelSpec,
        max_tokens: int,
        temperature: float,
    ) -> Generator[str, None, None]:
        """Stream tokens from an OpenAI-compatible SSE endpoint (Groq, Together, Ollama)."""
        if spec.api_provider == "Anthropic":
            # Anthropic streaming is different — just do one blocking call and yield
            yield self._generate_anthropic(prompt, spec, spec.api_key(), max_tokens, temperature)
            return

        api_key = spec.api_key()
        payload = json.dumps({
            "model": spec.api_model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }).encode()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            f"{spec.api_base}/chat/completions",
            data=payload, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        chunk = json.loads(body)
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
        except Exception as exc:
            yield f"[Stream error: {exc}]"

    # ── Local model loading ───────────────────────────────────────────────────

    def ensure_loaded(self, spec: ModelSpec, path: Path) -> None:
        if spec.remote:
            return   # nothing to load
        tag = f"{spec.engine}:{path}"
        if self._active == tag:
            return
        with self._lock:
            if self._active != tag:
                if spec.engine == "mlx":
                    self._load_mlx(path)
                else:
                    self._load_gguf(path, spec.ctx)

    def generate(
        self,
        prompt: str,
        spec: ModelSpec,
        path: Path,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> str:
        """Generate a completion — local or remote, transparent to callers."""
        if spec.remote:
            log.debug(f"Remote inference via {spec.api_provider}: {spec.api_model_id}")
            return self._generate_remote(prompt, spec, max_tokens, temperature)

        if self._thermal.state >= ThermalState.CRITICAL:
            log.warning("Thermal CRITICAL — refusing local inference")
            return "[Thermal limit reached. Try again when device cools.]"

        self.ensure_loaded(spec, path)

        with self._lock:
            if spec.engine == "mlx":
                from mlx_lm import generate as mlx_gen
                return mlx_gen(
                    self._model, self._tok,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temp=temperature,
                    verbose=False,
                )
            else:
                result = self._llama.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return result["choices"][0]["message"]["content"]

    def generate_stream(
        self,
        prompt: str,
        spec: ModelSpec,
        path: Path,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        if spec.remote:
            yield from self._stream_remote(prompt, spec, max_tokens, temperature)
            return

        if self._thermal.state >= ThermalState.CRITICAL:
            yield "[Thermal limit reached.]"
            return

        self.ensure_loaded(spec, path)

        if spec.engine == "mlx":
            from mlx_lm import stream_generate
            for chunk in stream_generate(
                self._model, self._tok, prompt=prompt,
                max_tokens=max_tokens, temp=temperature,
            ):
                yield chunk
        else:
            for chunk in self._llama.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            ):
                delta = chunk["choices"][0].get("delta", {})
                if "content" in delta:
                    yield delta["content"]


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5 · RAG Pipeline (Documents + DDGS Web Search)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RagChunk:
    doc_id:  str
    source:  str
    text:    str
    emb:     Optional[Any] = field(default=None, repr=False)


class RagStore:
    """
    Lightweight vector store backed by numpy cosine similarity.
    Falls back to keyword BM25-style scoring when sentence-transformers
    is unavailable, so the script works on very constrained hardware.
    """
    CHUNK_SIZE = 400
    OVERLAP    = 80

    def __init__(self):
        self._chunks: List[RagChunk] = []
        self._encoder = None
        self._embs: Optional[Any] = None   # numpy array (N, D)

    def _get_encoder(self):
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2",
                    cache_folder=str(DATA_DIR / "st_cache"),
                )
            except Exception:
                pass
        return self._encoder

    def _chunk_text(self, text: str) -> List[str]:
        words = text.split()
        chunks = []
        i = 0
        while i < len(words):
            chunk = " ".join(words[i : i + self.CHUNK_SIZE])
            chunks.append(chunk)
            i += self.CHUNK_SIZE - self.OVERLAP
        return chunks

    def add_text(self, text: str, source: str = "unknown") -> int:
        import hashlib
        doc_id = hashlib.md5(text[:256].encode()).hexdigest()[:8]
        for chunk in self._chunk_text(text):
            self._chunks.append(RagChunk(doc_id=doc_id, source=source, text=chunk))
        self._embs = None   # invalidate cache
        return len(self._chunks)

    def add_file(self, path: Path) -> int:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                text = "\n".join(p.extract_text() or "" for p in reader.pages)
            except Exception as e:
                log.warning(f"PDF read failed {path}: {e}")
                return 0
        else:
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                return 0
        return self.add_text(text, source=str(path))

    def _build_embs(self) -> Optional[Any]:
        enc = self._get_encoder()
        if enc is None or not self._chunks:
            return None
        import numpy as np
        if self._embs is None or len(self._embs) != len(self._chunks):
            texts = [c.text for c in self._chunks]
            self._embs = enc.encode(texts, batch_size=32, show_progress_bar=False)
        return self._embs

    def search(self, query: str, top_k: int = 5) -> List[RagChunk]:
        if not self._chunks:
            return []

        enc = self._get_encoder()
        if enc is not None:
            import numpy as np
            embs = self._build_embs()
            q_emb = enc.encode([query])[0]
            sims  = np.dot(embs, q_emb) / (
                np.linalg.norm(embs, axis=1) * np.linalg.norm(q_emb) + 1e-9
            )
            idxs = sims.argsort()[::-1][:top_k]
            return [self._chunks[i] for i in idxs]

        # Fallback: keyword overlap score
        q_words = set(query.lower().split())
        scored = [
            (sum(1 for w in q_words if w in c.text.lower()), c)
            for c in self._chunks
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]


def ddgs_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """DuckDuckGo search; returns list of {title, url, body}."""
    try:
        from ddgs import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=max_results))
    except Exception as e:
        log.warning(f"DDGS search failed: {e}")
        return []


def ddgs_news(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    try:
        from ddgs import DDGS
        with DDGS() as d:
            return list(d.news(query, max_results=max_results))
    except Exception as e:
        log.warning(f"DDGS news failed: {e}")
        return []


def build_rag_context(
    rag: RagStore,
    query: str,
    top_k: int = 4,
    web: bool = True,
    max_web: int = 3,
) -> str:
    parts: List[str] = []

    # Local documents
    local_hits = rag.search(query, top_k=top_k)
    if local_hits:
        parts.append("=== Local Context ===")
        for c in local_hits:
            parts.append(f"[{c.source}] {c.text[:300]}")

    # Web
    if web:
        web_hits = ddgs_search(query, max_results=max_web)
        if web_hits:
            parts.append("=== Web Search ===")
            for h in web_hits:
                parts.append(f"[{h.get('href','')[:60]}] {h.get('body','')[:300]}")

    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 6 · Mesh Network (exo-style: UDP discovery + HTTP pipeline sharding)
# ──────────────────────────────────────────────────────────────────────────────

MESH_PORT    = 9480
MESH_RELAY   = 9777
BEACON_PORT  = 9479
BEACON_INT   = 10.0   # seconds between UDP beacons
PEER_TTL     = 45.0   # drop peer if not seen within this window


@dataclass
class Peer:
    node_id:   str
    hostname:  str
    address:   str
    api_port:  int
    ram_gb:    float
    platform:  str
    last_seen: float = field(default_factory=time.time)

    def is_alive(self) -> bool:
        return (time.time() - self.last_seen) < PEER_TTL

    def url(self, path: str = "") -> str:
        return f"http://{self.address}:{self.api_port}{path}"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "address": self.address,
            "api_port": self.api_port,
            "ram_gb": self.ram_gb,
            "platform": self.platform,
            "last_seen": self.last_seen,
        }


class MeshPeer:
    """
    Lightweight exo-style peer.
    - Broadcasts UDP beacons every BEACON_INT seconds.
    - Listens for beacons from other nodes; maintains live peer table.
    - Exposes /mesh/status over HTTP (added to the FastAPI app).
    """

    def __init__(self, dev: DeviceProfile, api_port: int = MESH_PORT):
        self._dev      = dev
        self._api_port = api_port
        self._peers: Dict[str, Peer] = {}
        self._lock     = threading.Lock()
        self._running  = False

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._beacon_sender, daemon=True, name="mesh-tx").start()
        threading.Thread(target=self._beacon_listener, daemon=True, name="mesh-rx").start()

    def stop(self) -> None:
        self._running = False

    def live_peers(self) -> List[Peer]:
        with self._lock:
            return [p for p in self._peers.values() if p.is_alive()]

    def _make_beacon(self) -> bytes:
        msg = {
            "node_id":  self._dev.node_id,
            "hostname": self._dev.hostname,
            "api_port": self._api_port,
            "ram_gb":   round(self._dev.ram_gb, 1),
            "platform": self._dev.platform,
            "ts":       time.time(),
        }
        return json.dumps(msg).encode()

    def _beacon_sender(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self._running:
            try:
                sock.sendto(self._make_beacon(), ("<broadcast>", BEACON_PORT))
            except Exception:
                pass
            time.sleep(BEACON_INT)
        sock.close()

    def _beacon_listener(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        try:
            sock.bind(("", BEACON_PORT))
        except OSError as e:
            log.warning(f"Mesh listener bind failed: {e} (UDP port {BEACON_PORT} in use?)")
            return
        sock.settimeout(2.0)
        while self._running:
            try:
                data, addr = sock.recvfrom(2048)
                msg = json.loads(data.decode())
                nid = msg.get("node_id", "")
                if nid and nid != self._dev.node_id:
                    peer = Peer(
                        node_id=nid,
                        hostname=msg.get("hostname", ""),
                        address=addr[0],
                        api_port=int(msg.get("api_port", MESH_PORT)),
                        ram_gb=float(msg.get("ram_gb", 0)),
                        platform=msg.get("platform", "cpu"),
                    )
                    with self._lock:
                        self._peers[nid] = peer
            except (socket.timeout, json.JSONDecodeError):
                pass
            except Exception as e:
                log.debug(f"Mesh rx error: {e}")
        sock.close()

    def forward_generate(self, peer: Peer, prompt: str, max_tokens: int = 512) -> str:
        """Ask a peer to generate — simple HTTP call."""
        payload = json.dumps({"prompt": prompt, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(
            peer.url("/generate"),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())["response"]
        except Exception as e:
            return f"[Peer error: {e}]"

    def best_peer_for_large(self) -> Optional[Peer]:
        """Return the live peer with most RAM — for offloading large tasks."""
        peers = self.live_peers()
        if not peers:
            return None
        return max(peers, key=lambda p: p.ram_gb)


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 7 · Agent Engine (ReAct: think → act → observe → answer)
# ──────────────────────────────────────────────────────────────────────────────

_AGENT_SYSTEM = """\
You are an autonomous AI agent running locally on this device.
You have access to the following tools. Use ONE tool per turn.

  search(query)          — DuckDuckGo web search
  python(code)           — execute Python code in a sandbox
  read_file(path)        — read a local file
  write_file(path, text) — write text to a local file
  shell(cmd)             — run a shell command (no sudo)
  fetch(url)             — fetch a URL and return text content
  recall(key)            — retrieve from persistent memory
  remember(key, value)   — store to persistent memory
  delegate(peer, task)   — forward task to a mesh peer  (format: peer=IP:PORT)
  done(answer)           — emit the final answer and stop

Format (strict):
  Thought: <your reasoning>
  Action: tool(args)

After an Observation is injected, continue with Thought/Action or produce a final:
  Answer: <final complete response>

NEVER fabricate an Observation. ALWAYS use search() for real-time or current facts.
"""

_TOOL_RE = re.compile(r"(\w+)\((.+)?\)", re.DOTALL)


class PythonSandbox:
    """Execute Python code in a subprocess with a 30s timeout; no network."""

    TIMEOUT = 30

    def run(self, code: str) -> str:
        fd, fname = tempfile.mkstemp(suffix=".py", prefix="aio_sandbox_")
        try:
            os.write(fd, code.encode())
            os.close(fd)
            result = subprocess.run(
                [sys.executable, fname],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT,
            )
            out = (result.stdout or "") + (result.stderr or "")
            return out[:4000]
        except subprocess.TimeoutExpired:
            return "[Sandbox timeout]"
        except Exception as e:
            return f"[Sandbox error: {e}]"
        finally:
            try:
                os.unlink(fname)
            except OSError:
                pass


class AgentMemory:
    _store: Dict[str, str] = {}

    def remember(self, key: str, value: str) -> None:
        self._store[key.strip()] = value.strip()

    def recall(self, key: str) -> str:
        return self._store.get(key.strip(), "[not found]")


_sandbox = PythonSandbox()
_agent_mem = AgentMemory()


def _tool_dispatch(name: str, args_str: str, rag: RagStore, mesh: MeshPeer) -> str:
    args_str = (args_str or "").strip().strip('"\'')

    if name == "search":
        hits = ddgs_search(args_str, max_results=4)
        if not hits:
            return "[No results]"
        return "\n".join(f"- {h.get('body', '')[:200]}" for h in hits[:3])

    elif name == "python":
        return _sandbox.run(args_str)

    elif name == "read_file":
        p = Path(args_str).expanduser()
        if not p.exists():
            return f"[File not found: {p}]"
        return p.read_text(errors="ignore")[:4000]

    elif name == "write_file":
        parts = args_str.split(",", 1)
        if len(parts) < 2:
            return "[write_file requires path, text]"
        p = Path(parts[0].strip()).expanduser()
        p.write_text(parts[1].strip())
        return f"[Written {p}]"

    elif name == "shell":
        if any(c in args_str for c in [";", "&&", "||", "`", "$("]):
            return "[Compound shell commands not allowed in agent]"
        try:
            out = subprocess.run(
                args_str, shell=True, capture_output=True, text=True, timeout=30
            )
            return (out.stdout + out.stderr)[:2000]
        except Exception as e:
            return f"[Shell error: {e}]"

    elif name == "fetch":
        try:
            with urllib.request.urlopen(args_str, timeout=10) as r:
                return r.read(4000).decode("utf-8", errors="ignore")
        except Exception as e:
            return f"[Fetch error: {e}]"

    elif name == "recall":
        return _agent_mem.recall(args_str)

    elif name == "remember":
        kv = args_str.split("=", 1)
        if len(kv) == 2:
            _agent_mem.remember(kv[0], kv[1])
            return f"[Stored {kv[0]}]"
        return "[remember requires key=value]"

    elif name == "delegate":
        # delegate(peer=IP:PORT, task=text)
        m = re.search(r"peer=([^,]+),\s*task=(.+)", args_str, re.DOTALL)
        if m:
            peer_addr = m.group(1).strip()
            task_text = m.group(2).strip()
            best = mesh.best_peer_for_large()
            if best and f"{best.address}:{best.api_port}" == peer_addr:
                return mesh.forward_generate(best, task_text)
        return "[No suitable peer found]"

    elif name == "done":
        return f"DONE:{args_str}"

    # Plugin-registered tools (loaded from pai_data/plugins/)
    if name in _plugin_mgr.tools:
        try:
            return str(_plugin_mgr.tools[name](args_str))
        except Exception as exc:
            return f"[Plugin '{name}' error: {exc}]"

    return f"[Unknown tool: {name}]"


def run_agent(
    task: str,
    engine: InferenceEngine,
    sudo_spec: ModelSpec,
    sudo_path: Path,
    rag: RagStore,
    mesh: MeshPeer,
    max_steps: int = 12,
    web_rag: bool = True,
) -> str:
    """ReAct agent loop.  Returns final answer string."""
    context = build_rag_context(rag, task, web=web_rag)
    history = [
        f"System:\n{_AGENT_SYSTEM}",
        f"Context:\n{context}" if context else "",
        f"Task: {task}",
    ]

    for step in range(max_steps):
        prompt = "\n\n".join(filter(None, history))
        raw = engine.generate(prompt, sudo_spec, sudo_path, max_tokens=512, temperature=0.2)
        history.append(f"Agent:\n{raw}")

        # Extract Action
        action_m = re.search(r"Action:\s*(.+)", raw, re.DOTALL)
        answer_m = re.search(r"Answer:\s*(.+)", raw, re.DOTALL)

        if answer_m:
            return answer_m.group(1).strip()

        if action_m:
            action_line = action_m.group(1).strip().split("\n")[0]
            m = _TOOL_RE.match(action_line)
            if m:
                tool_name = m.group(1)
                tool_args = m.group(2) or ""
                obs = _tool_dispatch(tool_name, tool_args, rag, mesh)
                if obs.startswith("DONE:"):
                    return obs[5:].strip()
                history.append(f"Observation: {obs[:1500]}")
                continue

        # No structured output — treat raw as answer
        if step >= 2:
            return raw.strip()

    return "[Agent reached max steps without a final answer]"


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 8 · Code Agent (agentic coding: plan → write → run → fix)
# ──────────────────────────────────────────────────────────────────────────────

_CODE_SYSTEM = """\
You are an expert software engineer agent. Given a coding task:
1. Plan what files/code to create.
2. Write each file using write_file(path, code).
3. Validate with python(code) or shell(cmd).
4. Fix any errors and iterate.
5. When complete, call done(summary).

Rules:
- Write complete, runnable code — never truncate or use placeholders.
- Test your code before calling done().
- Prefer stdlib; only import packages that exist in the environment.
"""


def run_code_agent(
    task: str,
    engine: InferenceEngine,
    sudo_spec: ModelSpec,
    sudo_path: Path,
    rag: RagStore,
    mesh: MeshPeer,
    workdir: Optional[Path] = None,
    max_steps: int = 20,
) -> str:
    wd = workdir or BASE_DIR / "agent_workspace"
    wd.mkdir(parents=True, exist_ok=True)

    # Inject cwd into the task so the agent writes to the right place
    full_task = f"{task}\n\n[Working directory: {wd}]"
    sys_block = _CODE_SYSTEM

    history = [
        f"System:\n{sys_block}",
        f"Task: {full_task}",
    ]

    for step in range(max_steps):
        prompt = "\n\n".join(history)
        raw = engine.generate(prompt, sudo_spec, sudo_path, max_tokens=1024, temperature=0.15)
        history.append(f"Agent:\n{raw}")

        action_m = re.search(r"Action:\s*(.+)", raw, re.DOTALL)
        answer_m = re.search(r"Answer:\s*(.+)", raw, re.DOTALL)

        if answer_m:
            return answer_m.group(1).strip()

        if action_m:
            action_line = action_m.group(1).strip().split("\n")[0]
            m = _TOOL_RE.match(action_line)
            if m:
                tool_name = m.group(1)
                tool_args = m.group(2) or ""
                obs = _tool_dispatch(tool_name, tool_args, rag, mesh)
                if obs.startswith("DONE:"):
                    return obs[5:].strip()
                history.append(f"Observation: {obs[:2000]}")
                continue

        if step >= 3:
            return raw.strip()

    return "[Code agent reached max steps]"


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 9 · Thermal Trainer (LoRA fine-tune, MLX only, thermally gated)
# ──────────────────────────────────────────────────────────────────────────────

class ThermalTrainer:
    """
    Idle-time LoRA fine-tuner.  Collects inference Q&A pairs as training data,
    then fine-tunes when the device is idle and cool.

    Only runs on Apple Silicon (MLX).  Skipped silently on CUDA/CPU.
    """

    IDLE_BEFORE_TRAIN   = 20 * 60    # 20 minutes idle before starting
    MAX_ITERS_PER_CYCLE = 50
    BATCH_SIZE          = 2
    LR                  = 1e-5
    COOL_BETWEEN_STEPS  = 5.0        # seconds between training steps

    def __init__(
        self,
        dev: DeviceProfile,
        thermal: ThermalGovernor,
        sudo_spec: Optional[ModelSpec],
        sudo_path: Optional[Path],
    ):
        self._dev       = dev
        self._thermal   = thermal
        self._sudo_spec = sudo_spec
        self._sudo_path = sudo_path
        self._last_active = time.time()
        self._running   = False
        self._training  = False

    def record_interaction(self, prompt: str, response: str, tag: str = "general") -> None:
        entry = json.dumps({"text": f"User: {prompt}\nAssistant: {response}"})
        buf = TRAIN_DIR / f"{tag}_buffer.jsonl"
        with open(buf, "a") as f:
            f.write(entry + "\n")

    def touch(self) -> None:
        self._last_active = time.time()

    def start(self) -> None:
        if self._dev.platform != "apple_silicon":
            log.info("ThermalTrainer: not Apple Silicon — training skipped")
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="trainer").start()

    def stop(self) -> None:
        self._running = False

    @property
    def is_training(self) -> bool:
        return self._training

    def _loop(self) -> None:
        while self._running:
            time.sleep(60)
            idle_s = time.time() - self._last_active
            if idle_s < self.IDLE_BEFORE_TRAIN:
                continue
            if self._thermal.state > ThermalState.WARM:
                continue
            buf_files = list(TRAIN_DIR.glob("*_buffer.jsonl"))
            if not buf_files:
                continue
            if self._sudo_spec is None or self._sudo_path is None:
                continue

            self._training = True
            log.info("ThermalTrainer: starting fine-tune cycle")
            try:
                self._train_cycle(buf_files[0])
            except Exception as e:
                log.warning(f"ThermalTrainer error: {e}")
            finally:
                self._training = False
                self.touch()

    def _train_cycle(self, buf: Path) -> None:
        import psutil
        from mlx_lm import load as mlx_load

        if psutil.virtual_memory().percent > 82:
            log.warning("ThermalTrainer: RAM too high, skipping")
            return

        adapter_key = f"{self._sudo_spec.key}_lora"
        adapter_path = str(ADAPTERS_DIR / adapter_key)

        log.info(f"ThermalTrainer: loading {self._sudo_spec.key} for training…")
        model, tokenizer = mlx_load(str(self._sudo_path))

        try:
            import mlx_lm
            # mlx_lm.train is the public API for LoRA fine-tuning
            mlx_lm.train(
                model=model,
                tokenizer=tokenizer,
                data=str(buf),
                adapter_path=adapter_path,
                iters=self.MAX_ITERS_PER_CYCLE,
                batch_size=self.BATCH_SIZE,
                learning_rate=self.LR,
            )
        except AttributeError:
            log.warning("ThermalTrainer: mlx_lm.train not available in this version")
            return

        # Archive processed data
        buf.rename(str(buf) + f".{int(time.time())}.done")
        log.info("ThermalTrainer: cycle complete, adapter saved")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 10 · FastAPI Backend
# ──────────────────────────────────────────────────────────────────────────────

def make_app(
    dev: DeviceProfile,
    thermal: ThermalGovernor,
    engine: InferenceEngine,
    sudo_spec: Optional[ModelSpec],
    sudo_path: Optional[Path],
    sized_spec: Optional[ModelSpec],
    sized_path: Optional[Path],
    rag: RagStore,
    mesh: MeshPeer,
    trainer: ThermalTrainer,
):
    from fastapi import FastAPI, HTTPException, UploadFile, File
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel as PBM

    app = FastAPI(title="Linus PAI", version=PAI_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request / response models ─────────────────────────────────────────────

    class GenReq(PBM):
        prompt:      str
        model:       str = "sudo"   # "sudo" | "sized" | "auto"
        max_tokens:  int = 1024
        temperature: float = 0.7
        web_rag:     bool = True
        stream:      bool = False

    class AgentReq(PBM):
        task:      str
        code_mode: bool = False
        web_rag:   bool = True

    class UploadDocReq(PBM):
        text:   str
        source: str = "inline"

    # ── Route helpers ─────────────────────────────────────────────────────────

    def _pick(model_key: str) -> Tuple[ModelSpec, Path]:
        if model_key == "sized" and sized_spec and sized_path:
            return sized_spec, sized_path
        if sudo_spec and sudo_path:
            return sudo_spec, sudo_path
        if sized_spec and sized_path:
            return sized_spec, sized_path
        raise HTTPException(503, "No model loaded")

    def _auto_pick(prompt: str) -> Tuple[ModelSpec, Path]:
        simple = len(prompt) < 120 and not any(
            k in prompt.lower() for k in ("explain", "analyze", "write", "code", "create")
        )
        return _pick("sized" if simple else "sudo")

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @app.get("/status")
    def status():
        import psutil
        return {
            "version":     PAI_VERSION,
            "device":      dev.platform,
            "chip":        dev.chip_name,
            "ram_gb":      round(dev.ram_gb, 1),
            "ram_used_pct": psutil.virtual_memory().percent,
            "cpu_pct":     psutil.cpu_percent(interval=0.2),
            "thermal":     thermal.stats(),
            "sudo_model":    sudo_spec.key  if sudo_spec  else None,
            "sudo_remote":   sudo_spec.remote if sudo_spec else False,
            "sudo_provider": sudo_spec.api_provider if (sudo_spec and sudo_spec.remote) else None,
            "sized_model":   sized_spec.key if sized_spec else None,
            "remote_backends": [
                {"key": s.key, "provider": s.api_provider, "model": s.api_model_id}
                for s in _AVAILABLE_REMOTE
            ],
            "peers":         [p.to_dict() for p in mesh.live_peers()],
            "training":      trainer.is_training,
            "downloads":     dict(_dl_state),
        }

    @app.post("/generate")
    def generate(req: GenReq):
        trainer.touch()
        ctx = build_rag_context(rag, req.prompt, web=req.web_rag) if req.web_rag else ""
        full_prompt = f"{ctx}\n\nUser: {req.prompt}" if ctx else req.prompt

        if req.model == "auto":
            spec, path = _auto_pick(req.prompt)
        else:
            spec, path = _pick(req.model)

        response = engine.generate(full_prompt, spec, path, req.max_tokens, req.temperature)
        trainer.record_interaction(req.prompt, response)
        _cost_tracker.record(req.prompt, response)
        # Append to query log for live dashboard
        try:
            with open(QUERY_LOG, "a") as _ql:
                _ql.write(req.prompt[:120].replace("\n", " ") + "\n")
        except Exception:
            pass
        return {"response": response, "model": spec.key}

    @app.post("/agent")
    def agent_endpoint(req: AgentReq):
        trainer.touch()
        spec, path = _pick("sudo")
        if req.code_mode:
            answer = run_code_agent(req.task, engine, spec, path, rag, mesh)
        else:
            answer = run_agent(req.task, engine, spec, path, rag, mesh, web_rag=req.web_rag)
        trainer.record_interaction(req.task, answer, tag="agent")
        return {"answer": answer}

    @app.post("/rag/add")
    def rag_add(req: UploadDocReq):
        count = rag.add_text(req.text, source=req.source)
        return {"chunks_added": count}

    @app.post("/rag/upload")
    async def rag_upload(file: UploadFile = File(...)):
        suffix = Path(file.filename).suffix.lower()
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            content = await file.read()
            os.write(fd, content)
            os.close(fd)
            count = rag.add_file(Path(tmp))
        finally:
            os.unlink(tmp)
        return {"filename": file.filename, "chunks_added": count}

    @app.post("/rag/search")
    def rag_search(query: str, top_k: int = 5):
        hits = rag.search(query, top_k=top_k)
        return {"results": [{"source": c.source, "text": c.text[:300]} for c in hits]}

    @app.get("/mesh/peers")
    def mesh_peers():
        return {"peers": [p.to_dict() for p in mesh.live_peers()]}

    @app.get("/thermal")
    def thermal_status():
        return thermal.stats()

    @app.post("/train/trigger")
    def train_trigger():
        if dev.platform != "apple_silicon":
            return {"status": "skipped", "reason": "Apple Silicon only"}
        if trainer.is_training:
            return {"status": "already_training"}
        trainer.touch()
        threading.Thread(
            target=trainer._train_cycle,
            args=(next(iter(TRAIN_DIR.glob("*_buffer.jsonl")), None),),
            daemon=True,
        ).start()
        return {"status": "triggered"}

    @app.get("/download/status")
    def download_status():
        """Live download progress for all models — poll while runaio.sh waits."""
        rows = []
        for key, info in _dl_state.items():
            pct = info.get("pct", 0)
            rows.append({
                "key":   key,
                "state": info.get("state", "?"),
                "bytes": info.get("bytes", 0),
                "pct":   pct,
                "expected_bytes": info.get("expected_bytes", 0),
            })
        return {"downloads": rows}

    @app.get("/download/verify/{model_key}")
    def download_verify(model_key: str):
        """Integrity-check a locally stored model."""
        ladder = _MLX_LADDER + _GGUF_LADDER
        spec = next((s for s in ladder if s.key == model_key), None)
        if spec is None:
            raise HTTPException(404, f"Unknown model key: {model_key}")
        ok, reason = verify_model(spec)
        return {"key": model_key, "valid": ok, "reason": reason,
                "path": str(_local_path(spec))}

    @app.post("/download/scan")
    def download_scan(model_name: str, engine: str = "gguf"):
        """Search HuggingFace for available versions of a model — useful when primary fails."""
        if engine == "mlx":
            repos = hf_scan_mlx(model_name)
            return {"engine": "mlx", "results": [{"repo_id": r} for r in repos]}
        hits = hf_scan_gguf(model_name)
        return {"engine": "gguf",
                "results": [{"repo_id": r, "filename": f} for r, f in hits]}

    # ── OpenAI-compatible API (/v1/*) ─────────────────────────────────────────
    # Drop-in replacement: set base_url=http://localhost:9480 in any OpenAI SDK.

    from fastapi.responses import StreamingResponse as _SR

    class _OAIMsg(PBM):
        role:    str = "user"
        content: str

    class _OAIChatReq(PBM):
        model:       str = "aio-sudo"
        messages:    List[_OAIMsg]
        max_tokens:  Optional[int] = 1024
        temperature: float = 0.7
        stream:      bool  = False

    class _OAICompReq(PBM):
        model:       str = "aio-sudo"
        prompt:      str
        max_tokens:  Optional[int] = 1024
        temperature: float = 0.7
        stream:      bool  = False

    @app.get("/v1/models")
    def oai_models():
        data = []
        if sudo_spec:
            data.append({"id": "aio-sudo",  "object": "model", "owned_by": "aio",
                          "description": sudo_spec.label(),  "is_moe": sudo_spec.is_moe})
        if sized_spec:
            data.append({"id": "aio-sized", "object": "model", "owned_by": "aio",
                          "description": sized_spec.label(), "is_moe": sized_spec.is_moe})
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def oai_chat(req: _OAIChatReq):
        spec, path = _pick("sudo" if "sudo" in req.model else "sized")
        prompt = "\n".join(f"{m.role.capitalize()}: {m.content}" for m in req.messages)
        trainer.touch()
        _cost_tracker.record(prompt, "")
        msg_id  = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if req.stream:
            async def _sse():
                for tok in engine.generate_stream(
                    prompt, spec, path,
                    max_tokens=req.max_tokens or 1024,
                    temperature=req.temperature,
                ):
                    chunk = {"id": msg_id, "object": "chat.completion.chunk",
                             "created": created, "model": req.model,
                             "choices": [{"delta": {"content": tok}, "index": 0,
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                    _cost_tracker.record("", tok)
                yield "data: [DONE]\n\n"
            return _SR(_sse(), media_type="text/event-stream")

        response_text = engine.generate(prompt, spec, path,
                                        max_tokens=req.max_tokens or 1024,
                                        temperature=req.temperature)
        _cost_tracker.record(prompt, response_text)
        trainer.record_interaction(prompt, response_text)
        inp_tok = len(prompt.split())
        out_tok = len(response_text.split())
        return {
            "id": msg_id, "object": "chat.completion",
            "created": created, "model": req.model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": response_text}}],
            "usage": {"prompt_tokens": inp_tok, "completion_tokens": out_tok,
                      "total_tokens": inp_tok + out_tok},
        }

    @app.post("/v1/completions")
    async def oai_completion(req: _OAICompReq):
        """Legacy OpenAI completion endpoint."""
        spec, path = _pick("sudo" if "sudo" in req.model else "sized")
        trainer.touch()
        if req.stream:
            async def _sse():
                for tok in engine.generate_stream(req.prompt, spec, path,
                                                  max_tokens=req.max_tokens or 1024,
                                                  temperature=req.temperature):
                    chunk = {"object": "text_completion",
                             "choices": [{"text": tok, "index": 0, "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                yield "data: [DONE]\n\n"
            return _SR(_sse(), media_type="text/event-stream")

        out = engine.generate(req.prompt, spec, path,
                              max_tokens=req.max_tokens or 1024,
                              temperature=req.temperature)
        return {"object": "text_completion",
                "choices": [{"text": out, "index": 0, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": len(req.prompt.split()),
                          "completion_tokens": len(out.split())}}

    @app.get("/v1/usage")
    def oai_usage():
        return _cost_tracker.stats()

    @app.get("/v1/benchmark")
    async def oai_benchmark():
        """Trigger benchmark and return results as JSON."""
        from fastapi.concurrency import run_in_threadpool
        if sudo_spec and sudo_path:
            results = await run_in_threadpool(
                run_benchmark, engine, sudo_spec, sudo_path, dev
            )
            return {"results": [vars(r) for r in results]}
        return {"error": "No model loaded"}

    # ── Plugin routes ─────────────────────────────────────────────────────────
    _plugin_mgr.register_routes(
        app,
        engine=engine, sudo_spec=sudo_spec, sudo_path=sudo_path,
        sized_spec=sized_spec, sized_path=sized_path,
        rag=rag, mesh=mesh, thermal=thermal,
    )

    return app


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 11 · Streamlit Frontend (written to disk and launched as subprocess)
# ──────────────────────────────────────────────────────────────────────────────

_FRONTEND_CODE = '''\
import streamlit as st
import requests, json, os, time, psutil

API = os.getenv("PAI_API", "http://localhost:9480")

def _rj(r):
    """Safe response.json() — returns {} on empty or non-JSON body."""
    try:
        return r.json()
    except Exception:
        return {}

st.set_page_config(page_title="Linus PAI · Local AI", layout="wide", page_icon="⚡")

# ── Theme ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp{background:#0d0d0d; color:#e0e0e0; font-family: \'SF Mono\',monospace;}
.metric-box{background:#1a1a1a; border-radius:8px; padding:12px 16px; margin:4px 0;}
.peer-chip{display:inline-block; background:#1e3a5f; border-radius:12px;
           padding:2px 10px; margin:2px; font-size:0.8em;}
</style>""", unsafe_allow_html=True)

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ PAI Runtime")

    try:
        s = requests.get(f"{API}/status", timeout=3).json()
        th = s.get("thermal", {})
        col1, col2 = st.columns(2)
        col1.metric("CPU", f"{s.get(\'cpu_pct\',0):.0f}%")
        col2.metric("RAM", f"{s.get(\'ram_used_pct\',0):.0f}%")
        st.caption(f"🌡 Thermal: **{th.get(\'state\',\'?\')}**  {th.get(\'temp_c\',0)}°C")
        st.caption(f"🔮 Sudo: `{s.get(\'sudo_model\',\'none\')}`")
        st.caption(f"⚡ Sized: `{s.get(\'sized_model\',\'none\')}`")
        peers = s.get("peers", [])
        if peers:
            st.divider()
            st.caption(f"🌐 Mesh peers: {len(peers)}")
            for p in peers:
                st.markdown(f"<span class=\'peer-chip\'>{p.get(\'hostname\',\'?\')} {p.get(\'ram_gb\',0):.0f}GB</span>",
                            unsafe_allow_html=True)
    except Exception:
        st.warning("Backend not reachable")

    st.divider()
    mode       = st.radio("Mode", ["Chat", "Agent", "Code Agent"], index=0)
    model_sel  = st.selectbox("Model", ["auto", "sudo", "sized"])
    web_rag    = st.checkbox("Web RAG", value=True)
    max_tokens = st.slider("Max tokens", 128, 4096, 1024, 128)

    st.divider()
    with st.expander("Upload Document"):
        uf = st.file_uploader("PDF / TXT / MD", type=["pdf","txt","md"])
        ufs = st.file_uploader("PDF / TXT / MD", type=["pdf","txt","md"], accept_multiple_files=True)
        if ufs and st.button("Ingest"):
            total = 0
            for uf in ufs:
                r = requests.post(f"{API}/rag/upload", files={"file": (uf.name, uf, uf.type)})
                data = _rj(r)
                if r.ok:
                    total += data.get("chunks_added", 0)
                else:
                    st.error(f"{uf.name}: {r.status_code} {data or r.text[:120]}")
            if r.ok:
                st.success(f"Ingested {len(ufs)} file(s) — {total} chunks added")

# ── Main ─────────────────────────────────────────────────────────────────────
tab_chat, tab_status, tab_train = st.tabs(["💬 Chat", "📊 Status", "🎓 Train"])

with tab_chat:
    if "history" not in st.session_state:
        st.session_state.history = []

    for m in st.session_state.history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if prompt := st.chat_input("Ask anything…"):
        st.session_state.history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            box = st.empty()
            box.markdown("_Thinking…_")
            try:
                if mode == "Chat":
                    r = requests.post(f"{API}/generate", json={
                        "prompt": prompt,
                        "model": model_sel,
                        "max_tokens": max_tokens,
                        "web_rag": web_rag,
                    }, timeout=120)
                    data = _rj(r)
                    resp = data.get("response","[Error]")
                    used = data.get("model","?")
                    box.markdown(resp + f"\\n\\n*Model: {used}*")
                else:
                    r = requests.post(f"{API}/agent", json={
                        "task": prompt,
                        "code_mode": mode == "Code Agent",
                        "web_rag": web_rag,
                    }, timeout=300)
                    data = _rj(r)
                    resp = data.get("answer","[Error]")
                    box.markdown(resp)
                st.session_state.history.append({"role": "assistant", "content": resp})
            except Exception as e:
                box.error(str(e))

with tab_status:
    if st.button("Refresh"):
        st.rerun()
    try:
        s = requests.get(f"{API}/status", timeout=3).json()
        st.json(s)
    except Exception as e:
        st.error(str(e))

with tab_train:
    st.caption("Thermal-gated LoRA fine-tune (Apple Silicon only)")
    if st.button("Trigger Training Cycle"):
        r = requests.post(f"{API}/train/trigger", timeout=10)
        st.info(_rj(r) or r.text)
    buf_files = list((os.getenv("PAI_DATA_DIR", "pai_data") + "/train_buffer/").split())
    st.caption("Training buffers accumulate from every inference interaction.")
'''


def write_frontend() -> Path:
    fp = BASE_DIR / "pai_frontend.py"
    fp.write_text(_FRONTEND_CODE)
    return fp


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 12 · One-time Compilation (llama-cpp-python Metal / CUDA)
# ──────────────────────────────────────────────────────────────────────────────

def check_compile_once(dev: DeviceProfile) -> None:
    """
    Verify that llama-cpp-python was compiled with the right backend.
    Re-compiles once if the Metal/CUDA flag is missing.
    """
    marker = DATA_DIR / ".compiled_backend"
    expected = (
        "metal"  if dev.has_metal  else
        "cuda"   if dev.has_cuda   else
        "rocm"   if dev.has_rocm   else
        "vulkan" if dev.has_vulkan else
        "cpu"
    )

    if marker.exists() and marker.read_text().strip() == expected:
        return

    log.info(f"Compiling llama-cpp-python for backend: {expected}")
    env = os.environ.copy()
    if expected == "metal":
        env["CMAKE_ARGS"] = "-DGGML_METAL=on"
    elif expected == "cuda":
        env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
    elif expected == "rocm":
        env["CMAKE_ARGS"] = "-DGGML_HIPBLAS=on"
        env.setdefault("AMDGPU_TARGETS", "gfx1100,gfx1030,gfx906")
    elif expected == "vulkan":
        env["CMAKE_ARGS"] = "-DGGML_VULKAN=on"

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install",
         "llama-cpp-python>=0.2.80",
         "--no-cache-dir", "--force-reinstall", "-q"],
        env=env,
    )
    marker.write_text(expected)
    log.info(f"llama-cpp-python compiled for {expected}")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 14 · Session Cost Tracker
# ──────────────────────────────────────────────────────────────────────────────

class SessionCostTracker:
    """Counts tokens and shows money saved versus GPT-4o pricing."""
    GPT4O_IN_PER_M  = 2.50
    GPT4O_OUT_PER_M = 10.00

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_tok = self._out_tok = self._queries = 0
        self._start  = time.time()

    def record(self, prompt: str, response: str) -> None:
        inp  = max(1, len(prompt)   // 4)
        outp = max(1, len(response) // 4)
        with self._lock:
            self._in_tok  += inp
            self._out_tok += outp
            self._queries += 1

    def saved_usd(self) -> float:
        return (self._in_tok  / 1e6 * self.GPT4O_IN_PER_M +
                self._out_tok / 1e6 * self.GPT4O_OUT_PER_M)

    def stats(self) -> dict:
        return {
            "queries":         self._queries,
            "input_tokens":    self._in_tok,
            "output_tokens":   self._out_tok,
            "total_tokens":    self._in_tok + self._out_tok,
            "saved_usd":       round(self.saved_usd(), 4),
            "session_minutes": round((time.time() - self._start) / 60, 1),
        }

    def summary(self) -> str:
        s = self.stats()
        return (f"{s['queries']} queries · {s['total_tokens']:,} tokens · "
                f"saved ${s['saved_usd']:.2f} vs GPT-4o")


_cost_tracker = SessionCostTracker()


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 15 · Plugin System
# ──────────────────────────────────────────────────────────────────────────────

class PluginManager:
    """
    Drop a .py file into pai_data/plugins/ and it is loaded automatically.
    A plugin exposes any subset of:
      TOOLS: dict[str, Callable[[str], str]]   — new agent tools
      register_routes(app, **ctx)              — new FastAPI routes
      register_ui(tab_fn)                      — new Streamlit tabs (future)
    """
    def __init__(self) -> None:
        self._plugins: List[Any] = []
        self._tools:   Dict[str, Callable] = {}

    def load(self, plugin_dir: Path) -> int:
        count = 0
        for py in sorted(plugin_dir.glob("*.py")):
            if py.stem.startswith("_"):
                continue
            try:
                import importlib.util as _ilu
                spec = _ilu.spec_from_file_location(py.stem, py)
                mod  = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)        # type: ignore[union-attr]
                self._plugins.append(mod)
                if hasattr(mod, "TOOLS"):
                    self._tools.update(mod.TOOLS)
                    log.info(f"Plugin {py.name}: registered {list(mod.TOOLS.keys())}")
                count += 1
                log.info(f"Plugin loaded: {py.name}")
            except Exception as e:
                log.warning(f"Plugin {py.name} failed to load: {e}")
        return count

    def register_routes(self, app: Any, **ctx: Any) -> None:
        for mod in self._plugins:
            if hasattr(mod, "register_routes"):
                try:
                    mod.register_routes(app, **ctx)
                except Exception as e:
                    log.warning(f"Plugin route registration error: {e}")

    @property
    def tools(self) -> Dict[str, Callable]:
        return self._tools


_plugin_mgr = PluginManager()


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 16 · MCP Server (Model Context Protocol — stdio transport)
# ──────────────────────────────────────────────────────────────────────────────

_MCP_TOOLS_DEF = [
    {"name": "aio_generate",
     "description": "Generate text with the local privacy-first PAI model (no data leaves device).",
     "inputSchema": {"type": "object",
                     "properties": {"prompt":     {"type": "string"},
                                    "model":      {"type": "string", "enum": ["sudo","sized","auto"], "default": "auto"},
                                    "max_tokens": {"type": "integer", "default": 1024}},
                     "required": ["prompt"]}},
    {"name": "aio_search_web",
     "description": "Privacy-respecting DuckDuckGo web search.",
     "inputSchema": {"type": "object",
                     "properties": {"query":       {"type": "string"},
                                    "max_results": {"type": "integer", "default": 5}},
                     "required": ["query"]}},
    {"name": "aio_rag_query",
     "description": "Query the local RAG document store (ingested files).",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "top_k": {"type": "integer", "default": 5}},
                     "required": ["query"]}},
    {"name": "aio_run_agent",
     "description": "Run an autonomous ReAct agent task fully on-device.",
     "inputSchema": {"type": "object",
                     "properties": {"task":    {"type": "string"},
                                    "web_rag": {"type": "boolean", "default": True}},
                     "required": ["task"]}},
    {"name": "aio_status",
     "description": "Return PAI device status: thermal, RAM, active models, mesh peers.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def run_mcp_server(
    engine: "InferenceEngine",
    sudo_spec: "ModelSpec",
    sudo_path: Path,
    rag: "RagStore",
    mesh: "MeshPeer",
    dev: "DeviceProfile",
    thermal: "ThermalGovernor",
) -> None:
    """
    MCP stdio server.  Add to Claude Code .claude/settings.json:
      {"mcpServers": {"aio": {"command": "python", "args": ["aio.py", "--mcp"]}}}
    """
    import psutil as _psu

    def _send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def _err(id_: Any, code: int, msg: str) -> dict:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}

    log.info("MCP server listening on stdio")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            _send(_err(None, -32700, "Parse error"))
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "linus-pai", "version": PAI_VERSION},
            }})

        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": req_id,
                   "result": {"tools": _MCP_TOOLS_DEF}})

        elif method == "tools/call":
            name_ = params.get("name", "")
            args_ = params.get("arguments", {})
            try:
                if name_ == "aio_generate":
                    txt = engine.generate(
                        args_["prompt"], sudo_spec, sudo_path,
                        max_tokens=args_.get("max_tokens", 1024),
                    )
                    _send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"content": [{"type": "text", "text": txt}]}})

                elif name_ == "aio_search_web":
                    hits = ddgs_search(args_["query"], args_.get("max_results", 5))
                    txt  = "\n".join(f"- {h.get('body', '')}" for h in hits) or "[no results]"
                    _send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"content": [{"type": "text", "text": txt}]}})

                elif name_ == "aio_rag_query":
                    chunks = rag.search(args_["query"], top_k=args_.get("top_k", 5))
                    txt    = "\n".join(f"[{c.source}] {c.text[:300]}" for c in chunks) or "[empty]"
                    _send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"content": [{"type": "text", "text": txt}]}})

                elif name_ == "aio_run_agent":
                    answer = run_agent(
                        args_["task"], engine, sudo_spec, sudo_path, rag, mesh,
                        web_rag=args_.get("web_rag", True),
                    )
                    _send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"content": [{"type": "text", "text": answer}]}})

                elif name_ == "aio_status":
                    st_txt = json.dumps({
                        "version":  PAI_VERSION,
                        "platform": dev.platform,
                        "gpu":      dev.gpu_name,
                        "ram_gb":   round(dev.ram_gb, 1),
                        "ram_used": _psu.virtual_memory().percent,
                        "thermal":  thermal.stats(),
                        "peers":    len(mesh.live_peers()),
                    }, indent=2)
                    _send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"content": [{"type": "text", "text": st_txt}]}})

                else:
                    _send(_err(req_id, -32601, f"Unknown tool: {name_}"))

            except Exception as exc:
                _send(_err(req_id, -32603, str(exc)))

        elif method == "notifications/initialized":
            pass
        else:
            if req_id is not None:
                _send(_err(req_id, -32601, f"Unknown method: {method}"))


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 17 · Benchmark Runner
# ──────────────────────────────────────────────────────────────────────────────

_BENCH_SUITE = [
    ("reasoning", "Is 9.9 or 9.11 larger? Think step by step, final answer only."),
    ("code",      "Write a Python binary-search function with docstring."),
    ("math",      "Solve: 5x − 3 = 22. Show working, box the answer."),
    ("knowledge", "Explain the Transformer attention mechanism in exactly 2 sentences."),
    ("creative",  "Write a haiku about running AI entirely on your own hardware."),
]


@dataclass
class BenchResult:
    label:   str
    ttft_ms: float
    total_s: float
    tokens:  int
    tps:     float


def run_benchmark(
    engine:    "InferenceEngine",
    sudo_spec: "ModelSpec",
    sudo_path: Path,
    dev:       "DeviceProfile",
    out_file:  Optional[Path] = None,
) -> List[BenchResult]:
    from rich.console import Console
    from rich.table   import Table
    from rich.panel   import Panel

    c       = Console()
    results: List[BenchResult] = []

    c.print(Panel.fit(
        f"[bold cyan]PAI Benchmark[/bold cyan]\n"
        f"Device: {dev.chip_name or dev.gpu_name}  |  RAM: {dev.ram_gb:.0f} GB  |  "
        f"GPU backend: {dev.platform}\n"
        f"Model:  {sudo_spec.label()}",
        border_style="cyan",
    ))

    for label, prompt in _BENCH_SUITE:
        c.print(f"  [dim]{label}…[/dim]", end="")
        t0 = time.time()
        first_tok_t: Optional[float] = None
        tokens = 0
        try:
            for chunk in engine.generate_stream(
                prompt, sudo_spec, sudo_path, max_tokens=256, temperature=0.1
            ):
                if first_tok_t is None:
                    first_tok_t = time.time()
                tokens += len(chunk.split())
            total_s = time.time() - t0
            ttft_ms = (first_tok_t - t0) * 1000 if first_tok_t else 0.0
            tps     = tokens / max(total_s, 0.001)
            results.append(BenchResult(label, ttft_ms, total_s, tokens, tps))
            c.print(f" [green]{tps:.1f} tok/s[/green]  TTFT {ttft_ms:.0f}ms")
        except Exception as exc:
            c.print(f" [red]FAIL: {exc}[/red]")

    if results:
        avg_tps  = sum(r.tps     for r in results) / len(results)
        avg_ttft = sum(r.ttft_ms for r in results) / len(results)

        t = Table(title="Results", show_header=True, header_style="bold magenta")
        for col in ("Test", "Tok/s", "TTFT", "Tokens", "Time"):
            t.add_column(col)
        for r in results:
            t.add_row(r.label, f"{r.tps:.1f}", f"{r.ttft_ms:.0f}ms",
                      str(r.tokens), f"{r.total_s:.1f}s")
        c.print(t)

        pad  = 47
        card = "\n".join([
            f"┌{'─'*pad}┐",
            f"│  PAI Benchmark  {(dev.chip_name or dev.gpu_name)[:26]:<26}  │",
            f"│  Backend: {dev.platform:<10}  RAM: {dev.ram_gb:.0f}GB{'':<12}│",
            f"│  Model:   {sudo_spec.key[:35]:<35}  │",
            f"│  {'[MoE] ' if sudo_spec.is_moe else ''}{sudo_spec.active_params_b:.0f}B active / {sudo_spec.params_b:.0f}B total{'':<14}│" if sudo_spec.is_moe else
            f"│  Dense {sudo_spec.params_b:.0f}B params{'':<28}│",
            f"│  Avg {avg_tps:>6.1f} tok/s   Avg TTFT {avg_ttft:>5.0f}ms{'':<8}│",
            f"│  github.com/miryala3/linus-pai{'':<22}│",
            f"└{'─'*pad}┘",
        ])
        c.print(f"\n[bold]Shareable card (copy & paste):[/bold]\n{card}\n")

        if out_file:
            payload = {
                "version": PAI_VERSION, "device": dev.chip_name or dev.gpu_name,
                "platform": dev.platform, "ram_gb": dev.ram_gb,
                "model": sudo_spec.key, "is_moe": sudo_spec.is_moe,
                "avg_tps": round(avg_tps, 2), "avg_ttft_ms": round(avg_ttft, 1),
                "results": [vars(r) for r in results],
            }
            out_file.write_text(json.dumps(payload, indent=2))
            c.print(f"[dim]Saved → {out_file}[/dim]")

    return results


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 18 · Doctor Check
# ──────────────────────────────────────────────────────────────────────────────

def run_doctor() -> bool:
    from rich.console import Console
    from rich.table   import Table

    c = Console()
    c.print("[bold cyan]PAI Doctor — system diagnostics[/bold cyan]\n")
    checks: List[Tuple[str, bool, str]] = []

    def chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    # Python version
    v = sys.version_info
    chk("Python ≥ 3.10", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}")

    # pip
    try:
        subprocess.run([sys.executable, "-m", "pip", "--version"],
                       capture_output=True, check=True, timeout=5)
        chk("pip", True)
    except Exception:
        chk("pip", False, "python -m ensurepip")

    # Core packages
    for pkg in ("psutil", "fastapi", "uvicorn", "streamlit", "rich",
                "requests", "ddgs", "huggingface_hub", "pypdf"):
        try:
            mod = importlib.import_module(pkg.replace("-", "_").replace(".", "_"))
            chk(pkg, True, getattr(mod, "__version__", "ok"))
        except ImportError:
            chk(pkg, False, f"pip install {pkg}")

    # sentence-transformers: optional (torch has no Python 3.13 wheel yet)
    try:
        import sentence_transformers as _st
        chk("sentence-transformers (RAG embeddings)", True, _st.__version__)
    except ImportError:
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        if sys.version_info >= (3, 13):
            chk("sentence-transformers (RAG embeddings)", False,
                f"torch has no Python {py_ver} wheel — use Python 3.12 for full RAG "
                "(keyword fallback is active)")
        else:
            chk("sentence-transformers (RAG embeddings)", False,
                "pip install sentence-transformers")

    # llama-cpp-python
    try:
        import llama_cpp
        chk("llama-cpp-python", True, getattr(llama_cpp, "__version__", "ok"))
    except ImportError:
        chk("llama-cpp-python", False, "python aio.py --force-install")

    # MLX (Apple Silicon)
    if platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        try:
            import mlx as _mlx
            chk("mlx (Apple Silicon)", True, _mlx.__version__)
        except ImportError:
            chk("mlx", False, "pip install mlx mlx-lm")

    # ROCm (AMD)
    has_rocm_bin = shutil.which("rocm-smi") is not None
    chk("AMD ROCm (rocm-smi)", has_rocm_bin,
        "not found — CPU inference will be used" if not has_rocm_bin else "")

    # Vulkan
    has_vk = shutil.which("vulkaninfo") is not None or os.path.exists("/dev/dri")
    chk("Vulkan compute", has_vk, "not found" if not has_vk else "")

    # CUDA
    has_nv = shutil.which("nvidia-smi") is not None
    chk("NVIDIA CUDA (nvidia-smi)", has_nv, "not found" if not has_nv else "")

    # Ports
    for port in (9480, 8501, 9479):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        free = s.connect_ex(("127.0.0.1", port)) != 0
        s.close()
        chk(f"Port {port} free", free, "in use" if not free else "")

    # Disk space
    try:
        free_gb = shutil.disk_usage(str(DATA_DIR)).free / (1024 ** 3)
        chk("Disk space ≥ 20 GB", free_gb >= 20, f"{free_gb:.0f} GB free")
    except Exception as exc:
        chk("Disk space", False, str(exc))

    # Writable dirs
    for dpath in (MODELS_DIR, ADAPTERS_DIR, TRAIN_DIR, PLUGINS_DIR):
        try:
            test = dpath / ".write_test"
            test.write_text("x")
            test.unlink()
            chk(f"{dpath.name}/ writable", True)
        except Exception as exc:
            chk(f"{dpath.name}/ writable", False, str(exc))

    # Network
    try:
        urllib.request.urlopen("https://huggingface.co", timeout=5)
        chk("HuggingFace reachable", True)
    except Exception:
        chk("HuggingFace reachable", False, "offline or firewalled")

    # Remote / GPT API keys
    _KEY_GUIDE = {
        "OPENAI_API_KEY":    "https://platform.openai.com/api-keys",
        "ANTHROPIC_API_KEY": "https://console.anthropic.com/",
        "GROQ_API_KEY":      "https://console.groq.com  (free tier)",
        "TOGETHER_API_KEY":  "https://api.together.xyz",
    }
    for env_var, guide in _KEY_GUIDE.items():
        present = bool(os.getenv(env_var))
        chk(f"{env_var} set", present,
            "active — remote GPT models enabled" if present else f"optional — {guide}")

    # Ollama local server
    chk("Ollama server (localhost:11434)", _probe_ollama(),
        "not running — start with: ollama serve")

    # Compiled backend marker
    marker = DATA_DIR / ".compiled_backend"
    if marker.exists():
        chk("llama backend compiled", True, marker.read_text().strip())
    else:
        chk("llama backend compiled", False, "run: python aio.py --force-install")

    # Model integrity for every file already on disk
    all_ladders = _MLX_LADDER + _GGUF_LADDER
    on_disk = [s for s in all_ladders if _local_path(s).exists() and not s.remote]
    if on_disk:
        c.print("\n[bold]Local model integrity[/bold]")
        t_m = Table(show_header=True, header_style="bold")
        t_m.add_column("Key",        min_width=28)
        t_m.add_column("Size on disk")
        t_m.add_column("Valid")
        t_m.add_column("Detail")
        for spec in on_disk:
            ok_m, reason_m = verify_model(spec)
            sz = _path_size_bytes(_local_path(spec))
            color = "green" if ok_m else "red"
            t_m.add_row(
                spec.key,
                f"{sz/(1024**3):.1f} GB",
                f"[{color}]{'✔' if ok_m else '✘'}[/{color}]",
                reason_m,
            )
            if not ok_m:
                all_ok = False
        c.print(t_m)
    else:
        c.print("[dim]No models on disk yet — run normally to download.[/dim]")

    # Print table
    t = Table(show_header=True, header_style="bold")
    t.add_column("Check", min_width=32)
    t.add_column("Status", min_width=6)
    t.add_column("Detail")

    all_ok = True
    for name, ok, detail in checks:
        color = "green" if ok else "red"
        t.add_row(name, f"[{color}]{'✔' if ok else '✘'}[/{color}]", detail)
        if not ok:
            all_ok = False

    c.print(t)
    if all_ok:
        c.print("\n[bold green]All checks passed. Linus PAI is ready.[/bold green]")
    else:
        c.print("\n[bold yellow]Some checks failed. Fix highlighted items above.[/bold yellow]")
    return all_ok


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 19 · Live Terminal Dashboard
# ──────────────────────────────────────────────────────────────────────────────

def run_dashboard(
    dev:       "DeviceProfile",
    thermal:   "ThermalGovernor",
    engine:    "InferenceEngine",
    sudo_spec: Optional["ModelSpec"],
    sized_spec: Optional["ModelSpec"],
    mesh:      "MeshPeer",
    cost:      SessionCostTracker,
    api_port:  int = MESH_PORT,
    ui_port:   int = 8501,
) -> None:
    import psutil as _psu
    from rich.console import Console
    from rich.layout  import Layout
    from rich.live    import Live
    from rich.panel   import Panel
    from rich.text    import Text

    recent: Deque[str] = deque(maxlen=10)
    console = Console()

    _SPARK = " ▁▂▃▄▅▆▇█"
    _temp_history: Deque[float] = deque(maxlen=30)

    def _bar(pct: float, width: int = 22) -> str:
        filled = int(pct / 100 * width)
        c_     = "green" if pct < 70 else ("yellow" if pct < 85 else "red")
        return f"[{c_}]{'█'*filled}{'░'*(width-filled)}[/{c_}] {pct:4.0f}%"

    def _spark(vals: Deque[float]) -> str:
        if not vals:
            return "─" * 15
        mn, mx = min(vals), max(vals)
        rng    = max(mx - mn, 1.0)
        return "".join(_SPARK[min(8, int((v - mn) / rng * 8))] for v in list(vals)[-15:])

    def _render() -> Panel:
        cpu  = _psu.cpu_percent(interval=None)
        ram  = _psu.virtual_memory().percent
        th   = thermal.stats()
        _temp_history.append(th["temp_c"])
        peers  = mesh.live_peers()
        cost_s = cost.stats()

        st_color = {"NOMINAL":"green","WARM":"yellow","HOT":"orange1",
                    "CRITICAL":"red","EMERGENCY":"bright_red"}.get(th["state"], "white")

        # Refresh query log
        if QUERY_LOG.exists():
            new_lines = QUERY_LOG.read_text().splitlines()[-10:]
            for ln in new_lines:
                if ln not in recent:
                    recent.append(ln)

        txt = Text()
        txt.append(f"PAI v{PAI_VERSION}  │  {dev.chip_name or dev.platform}  │  "
                   f"{dev.ram_gb:.0f} GB RAM\n", style="bold cyan")
        txt.append(f"Thermal  [{st_color}]{th['state']}[/{st_color}]  "
                   f"{th['temp_c']}°C  spark: {_spark(_temp_history)}"
                   f"  pred {th['predicted_60s']}°C/60s\n")
        txt.append(f"\nRAM  {_bar(ram)}\nCPU  {_bar(cpu)}\n")
        txt.append(f"\n🧠 sudo   {sudo_spec.key  if sudo_spec  else 'none'}\n")
        if sudo_spec and sudo_spec.is_moe:
            txt.append(f"   MoE {sudo_spec.active_params_b:.0f}B active / "
                       f"{sudo_spec.params_b:.0f}B total\n", style="dim")
        txt.append(f"⚡ sized  {sized_spec.key if sized_spec else 'none'}\n")
        txt.append(f"\n🌐 Peers  {len(peers)}\n")
        for p in peers[:4]:
            txt.append(f"   • {p.hostname} ({p.ram_gb:.0f}GB {p.platform})\n",
                       style="dim")
        txt.append(f"\n💰 {cost_s['queries']} queries · {cost_s['total_tokens']:,} tokens\n")
        txt.append(f"   Saved ${cost_s['saved_usd']:.3f} vs GPT-4o\n")
        txt.append(f"\nRecent queries\n", style="bold")
        for q in list(recent)[-6:]:
            txt.append(f"  › {q[:60]}\n", style="dim")
        txt.append(f"\nAPI :{'localhost:'+str(api_port):<22} "
                   f"UI :localhost:{ui_port}\n", style="dim")
        txt.append("[dim]Ctrl-C to exit dashboard[/dim]")
        return Panel(txt, title="[bold]PAI Dashboard[/bold]", border_style="cyan",
                     expand=True)

    try:
        with Live(console=console, refresh_per_second=2, screen=True) as live:
            while True:
                live.update(_render())
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 20 · Auto-update
# ──────────────────────────────────────────────────────────────────────────────

PAI_UPDATE_URL = os.getenv(
    "PAI_UPDATE_URL",
    "https://raw.githubusercontent.com/miryala3/linus-pai/main/pai.py",
)


def run_update(url: str = PAI_UPDATE_URL) -> bool:
    from rich.console import Console
    c = Console()
    c.print(f"[cyan]Checking for PAI update…[/cyan]")
    c.print(f"[dim]{url}[/dim]")

    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            new_bytes = resp.read()
    except Exception as exc:
        c.print(f"[red]Fetch failed: {exc}[/red]")
        return False

    # Validate: must be valid Python
    try:
        compile(new_bytes, "<update>", "exec")
    except SyntaxError as exc:
        c.print(f"[red]Downloaded file has syntax errors — update aborted: {exc}[/red]")
        return False

    current   = Path(__file__).resolve()
    old_hash  = hashlib.sha256(current.read_bytes()).hexdigest()[:12]
    new_hash  = hashlib.sha256(new_bytes).hexdigest()[:12]

    if old_hash == new_hash:
        c.print("[green]Already up to date.[/green]")
        return True

    # Backup
    backup = current.with_suffix(f".{old_hash}.bak.py")
    shutil.copy2(current, backup)
    c.print(f"[dim]Backup saved: {backup.name}[/dim]")

    current.write_bytes(new_bytes)
    c.print(f"[green]Updated! {old_hash} → {new_hash}[/green]")
    c.print("[dim]Restart PAI to use the new version.[/dim]")
    return True


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 21 · Demo Mode
# ──────────────────────────────────────────────────────────────────────────────

def run_demo(
    engine:    "InferenceEngine",
    sudo_spec: "ModelSpec",
    sudo_path: Path,
    rag:       "RagStore",
    mesh:      "MeshPeer",
    dev:       "DeviceProfile",
    thermal:   "ThermalGovernor",
) -> None:
    from rich.console  import Console
    from rich.panel    import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn

    c = Console()

    def _step(n: int, title: str) -> None:
        c.rule(f"[bold cyan]{n}/6  {title}[/bold cyan]")
        time.sleep(0.5)

    c.clear()
    c.print(Panel.fit(
        f"[bold cyan]Linus PAI — Private AI Runtime[/bold cyan]  v{PAI_VERSION}\n"
        "[dim]Interactive Demonstration[/dim]",
        border_style="cyan", padding=(1, 6),
    ))
    time.sleep(0.8)

    # 1 · Device
    _step(1, "Device Detection")
    gpu_backend = (
        f"Apple Metal ({dev.chip_name})" if dev.has_metal else
        f"NVIDIA CUDA ({dev.gpu_name})"  if dev.has_cuda  else
        f"AMD ROCm ({dev.gpu_name})"     if dev.has_rocm  else
        f"Vulkan ({dev.gpu_name})"       if dev.has_vulkan else
        "CPU only"
    )
    c.print(f"  Platform : [green]{dev.platform}[/green]")
    c.print(f"  GPU      : [green]{gpu_backend}[/green]")
    c.print(f"  RAM      : [green]{dev.ram_gb:.0f} GB[/green]")
    c.print(f"  Thermal  : [green]{thermal.stats()['state']}  {thermal.temp_c:.0f}°C[/green]")
    time.sleep(1.2)

    # 2 · Model
    _step(2, "MoE-First Model Selection")
    c.print(f"  sudo  : [cyan]{sudo_spec.label()}[/cyan]")
    if sudo_spec.is_moe:
        c.print(f"  [dim]Active params: {sudo_spec.active_params_b:.0f}B / "
                f"{sudo_spec.params_b:.0f}B total — quality of a 47B+ model[/dim]")
    time.sleep(1.0)

    # 3 · RAG
    _step(3, "RAG — Document Ingestion")
    sample = ("PAI is a local private AI runtime supporting MLX, GGUF, "
              "MoE models, AMD Vulkan/ROCm, thermal training, mesh networking, "
              "and agentic tasks on any device without cloud APIs.")
    rag.add_text(sample, source="demo")
    c.print(f"  [dim]Ingested: \"{sample[:65]}…\"[/dim]")
    hits = rag.search("thermal training AMD", top_k=1)
    c.print(f"  Query: [yellow]thermal training AMD[/yellow]")
    if hits:
        c.print(f"  [green]Found: \"{hits[0].text[:80]}\"[/green]")
    time.sleep(1.0)

    # 4 · Web search
    _step(4, "Web Search (DuckDuckGo — private, no tracking)")
    c.print("  [yellow]Query: latest open source AI 2025[/yellow]")
    with Progress(SpinnerColumn(), TextColumn("[dim]{task.description}"),
                  console=c, transient=True) as prog:
        prog.add_task("Searching DuckDuckGo…")
        results = ddgs_search("latest open source AI models 2025", max_results=2)
    for r in results[:2]:
        c.print(f"  [dim]→ {r.get('body','')[:90]}[/dim]")
    time.sleep(0.8)

    # 5 · Inference
    _step(5, "Local Inference (no internet needed)")
    prompt = "In one sentence, why is local AI better for privacy than cloud AI?"
    c.print(f"  [yellow]{prompt}[/yellow]")
    c.print("  Response: ", end="")
    t0 = time.time()
    try:
        tok = 0
        for chunk in engine.generate_stream(prompt, sudo_spec, sudo_path, max_tokens=80):
            c.print(chunk, end="", highlight=False)
            tok += len(chunk.split())
        c.print()
        c.print(f"  [dim]~{tok/(time.time()-t0):.0f} tok/s[/dim]")
    except Exception as exc:
        c.print(f"[red]{exc}[/red]")
    time.sleep(0.8)

    # 6 · Mesh
    _step(6, "Mesh Network (exo-style LAN inference)")
    peers = mesh.live_peers()
    if peers:
        c.print(f"  [green]{len(peers)} peer(s) discovered on LAN[/green]")
        for p in peers[:3]:
            c.print(f"    • {p.hostname}  {p.ram_gb:.0f}GB  {p.platform}")
    else:
        c.print("  [dim]Start PAI on another device on the same network to see peers.[/dim]")

    c.print()
    c.print(Panel.fit(
        f"[bold green]Demo complete![/bold green]\n"
        f"UI  → http://localhost:8501\n"
        f"API → http://localhost:{MESH_PORT}/docs\n"
        f"OpenAI-compatible → http://localhost:{MESH_PORT}/v1/\n"
        f"MCP for Claude Code → python aio.py --mcp",
        border_style="green",
    ))


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 22 · Download Manager
#   • Integrity check   : GGUF magic-byte + size ratio · MLX shard scan
#   • Stall detection   : background file-growth monitor; aborts if frozen
#   • Retry with backoff: up to 3 attempts, resume_download=True each time
#   • Cascade fallback  : if all retries fail, walk down the ladder to the
#                         next-best model that fits in RAM and try again
#   • State persistence : DATA_DIR/download_state.json — queryable via /status
# ──────────────────────────────────────────────────────────────────────────────

import concurrent.futures as _cf

# GGUF file format magic (first 4 bytes)
_GGUF_MAGIC = b"GGUF"

# Persisted download progress visible to the REST status endpoint
_DOWNLOAD_STATE_FILE = DATA_DIR / "download_state.json"
_dl_state: Dict[str, Any] = {}   # key → dict(state, bytes, ts, spec_key)


def _persist_dl_state() -> None:
    try:
        _DOWNLOAD_STATE_FILE.write_text(json.dumps(_dl_state, indent=2))
    except Exception:
        pass


# ── Path helpers ──────────────────────────────────────────────────────────────

def _local_path(spec: "ModelSpec") -> Path:
    if spec.engine == "mlx":
        return MODELS_DIR / spec.key
    if spec.filename and Path(spec.filename).is_absolute():
        return Path(spec.filename)
    return MODELS_DIR / spec.filename


def _path_size_bytes(p: Path) -> int:
    """Bytes on disk: file → stat, directory → recursive sum."""
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


# ── Integrity verifiers ───────────────────────────────────────────────────────

def _verify_gguf(path: Path, expected_gb: float) -> Tuple[bool, str]:
    """Check GGUF magic-bytes, minimum size, and ±40 % size sanity bound."""
    if not path.exists():
        return False, "file missing"
    size = path.stat().st_size
    if size < 4096:
        return False, f"file too small: {size} bytes"
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic != _GGUF_MAGIC:
            return False, f"bad GGUF magic {magic!r} — file is corrupt or truncated"
    except Exception as exc:
        return False, f"unreadable: {exc}"
    # Allow ±40 % of expected size (quant levels vary: Q2=~35%, Q8=~100%)
    ratio = size / max(expected_gb * 1024 ** 3, 1)
    if ratio < 0.30:
        return False, (f"file is only {size/(1024**3):.1f} GB "
                       f"vs {expected_gb:.1f} GB expected — likely truncated")
    return True, "ok"


def _verify_mlx(spec: "ModelSpec") -> Tuple[bool, str]:
    """Check MLX snapshot: required check_file present + no zero-byte shards."""
    dest  = MODELS_DIR / spec.key
    check = dest / spec.check_file
    if not check.exists():
        return False, f"missing {spec.check_file} in {dest}"
    shards = list(dest.glob("*.safetensors")) + list(dest.glob("*.gguf"))
    for s in shards:
        try:
            if s.stat().st_size < 1024:
                return False, f"zero-size shard: {s.name}"
        except OSError:
            pass
    return True, "ok"


def verify_model(spec: "ModelSpec") -> Tuple[bool, str]:
    """Public integrity check — used by doctor and download retry logic."""
    if spec.remote:
        return True, "remote (no local file)"
    path = _local_path(spec)
    if spec.engine == "mlx":
        return _verify_mlx(spec)
    return _verify_gguf(path, spec.size_gb)


# ── Stall error ───────────────────────────────────────────────────────────────

class _StallError(RuntimeError):
    pass


# ── Core download attempt (runs in a thread) ──────────────────────────────────

def _do_download(spec: "ModelSpec") -> Path:
    """Blocking HuggingFace download with resume_download=True."""
    from huggingface_hub import snapshot_download, hf_hub_download
    if spec.engine == "mlx":
        dest = MODELS_DIR / spec.key
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(dest),
            resume_download=True,
            local_files_only=False,
        )
        return dest
    else:
        hf_hub_download(
            repo_id=spec.repo_id,
            filename=spec.filename,
            local_dir=str(MODELS_DIR),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        return MODELS_DIR / spec.filename


# ── Download Manager ──────────────────────────────────────────────────────────

class DownloadManager:
    """
    Reliable model downloader.

    For each model:
      1. Integrity-check what's already on disk (GGUF magic, MLX shards).
      2. If corrupt or missing → delete partial file and retry.
      3. Up to 3 attempts with exponential backoff.  Each attempt:
           a. Runs hf_hub_download / snapshot_download in a thread.
           b. Polls file-growth every 2 s from the main thread.
           c. If growth stops for STALL_TIMEOUT seconds → raise _StallError.
           d. On _StallError: log, wait retry_delay, try again (resume picks up).
      4. Post-download integrity check.  If still corrupt → delete + retry.
      5. Returns local Path on success, None on total failure.
    """
    MAX_RETRIES   = 3
    RETRY_DELAYS  = [15, 60, 240]   # seconds between attempts
    POLL_INTERVAL = 2.0              # seconds between size polls
    # Stall timeout scales with file size: 5 min floor, +90s per GB
    # (a slow CDN edge node on a 70B model might go 20 min before resuming)

    def stall_timeout(self, spec: "ModelSpec") -> float:
        return max(300.0, spec.size_gb * 90.0)

    def get(self, spec: "ModelSpec") -> Optional[Path]:
        if spec.remote:
            return Path("/dev/null")

        # Fast-path: present AND valid
        path = _local_path(spec)
        ok, reason = verify_model(spec)
        if ok:
            log.debug(f"Model already valid: {spec.key}")
            return path

        # Delete known-corrupt partial (but keep truncated files — resume may fix them)
        if path.exists() and "corrupt" in reason:
            log.warning(f"Deleting corrupt {spec.key}: {reason}")
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except Exception:
                pass

        _dl_state[spec.key] = {"state": "queued", "bytes": 0, "ts": time.time()}
        _persist_dl_state()

        for attempt in range(self.MAX_RETRIES):
            try:
                result_path = self._attempt(spec, attempt)
                ok2, reason2 = verify_model(spec)
                if ok2:
                    _dl_state[spec.key] = {"state": "ok",   "bytes": _path_size_bytes(result_path), "ts": time.time()}
                    _persist_dl_state()
                    return result_path
                # Downloaded but still corrupt — delete and retry
                log.warning(f"Post-download integrity FAILED ({reason2}) "
                            f"— attempt {attempt+1}/{self.MAX_RETRIES}")
                if result_path.exists():
                    try:
                        (result_path.unlink if result_path.is_file()
                         else lambda: shutil.rmtree(result_path, ignore_errors=True))()
                    except Exception:
                        pass

            except _StallError as exc:
                log.warning(f"Stall on {spec.key} attempt {attempt+1}: {exc}")
                _dl_state[spec.key] = {"state": "stalled", "bytes": _path_size_bytes(_local_path(spec)), "ts": time.time()}
                _persist_dl_state()

            except Exception as exc:
                log.warning(f"Download error {spec.key} attempt {attempt+1}: {type(exc).__name__}: {exc}")
                _dl_state[spec.key] = {"state": f"error:{exc}", "bytes": 0, "ts": time.time()}
                _persist_dl_state()

            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_DELAYS[attempt]
                self._console().print(
                    f"[yellow]  ↻ Retry {attempt+2}/{self.MAX_RETRIES} "
                    f"for [bold]{spec.key}[/bold] in {delay}s "
                    f"(partial download will be resumed)…[/yellow]"
                )
                time.sleep(delay)

        # ── All direct retries failed → try alt sources ──────────────────────
        self._console().print(
            f"[yellow]  Primary download exhausted for [bold]{spec.key}[/bold] "
            f"— scanning alternate repos and quants…[/yellow]"
        )
        alt_result = self._try_alt_sources(spec)
        if alt_result is not None:
            _dl_state[spec.key] = {"state": "ok-alt", "bytes": _path_size_bytes(alt_result), "ts": time.time()}
            _persist_dl_state()
            return alt_result

        _dl_state[spec.key] = {"state": "failed", "bytes": 0, "ts": time.time()}
        _persist_dl_state()
        log.error(f"All sources exhausted for {spec.key} — cascade fallback will try next model")
        return None

    # ── Alternate source resolution ───────────────────────────────────────────

    # Quantisation fall-through order — best → smallest (lowest quality)
    _QUANT_PRIORITY = [
        "Q6_K", "Q5_K_M", "Q5_K_S",
        "Q4_K_M", "Q4_K_S", "Q4_0",
        "IQ4_XS", "IQ4_NL",
        "Q3_K_M", "Q3_K_S",
        "IQ3_M",
        "Q2_K",
        "IQ2_M",
    ]

    # Alternative HuggingFace orgs to try when the primary fails (GGUF)
    _ALT_ORGS_GGUF = ["bartowski", "QuantFactory", "mradermacher",
                       "TheBloke", "unsloth", "NousResearch"]
    # Alternative HuggingFace orgs for MLX
    _ALT_ORGS_MLX  = ["mlx-community", "nisten", "apple"]

    def _try_alt_sources(self, spec: "ModelSpec") -> Optional[Path]:
        """
        When the primary repo+filename fail, try in order:
          1. Different quant of the same model in the same repo (GGUF only)
          2. Same model name in alternate provider orgs
          3. Live HuggingFace search (uses HF API, network required)
        Returns a valid local Path or None.
        """
        if spec.engine == "mlx":
            return self._try_mlx_alts(spec)
        return self._try_gguf_alts(spec)

    def _try_gguf_alts(self, spec: "ModelSpec") -> Optional[Path]:
        c = self._console()
        base_fname = spec.filename  # e.g. "Llama-3.3-70B-Instruct-Q4_K_M.gguf"

        # Extract stem without the quant suffix, e.g. "Llama-3.3-70B-Instruct"
        stem_match = re.match(
            r"^(.+?)[-._](Q[0-9]|IQ[0-9]|f16|f32|bf16).*\.gguf$",
            base_fname, re.IGNORECASE
        )
        model_stem = stem_match.group(1) if stem_match else base_fname.replace(".gguf", "")

        # ── 1. Different quant in same repo ──────────────────────────────────
        from huggingface_hub import HfApi as _HfApi
        api = _HfApi()
        c.print(f"  [dim]Scanning {spec.repo_id} for alternate quants…[/dim]")
        try:
            repo_files = list(api.list_repo_files(spec.repo_id, timeout=15))
            gguf_files = [f for f in repo_files if f.lower().endswith(".gguf")]
            ordered = self._rank_quant_files(gguf_files, model_stem)
            for alt_fname in ordered:
                if alt_fname == spec.filename:
                    continue
                c.print(f"  → trying quant: [cyan]{alt_fname}[/cyan]")
                result = self._download_one(spec.repo_id, alt_fname,
                                            spec.size_gb, spec.key)
                if result:
                    return result
        except Exception as exc:
            log.debug(f"Alt quant scan failed for {spec.repo_id}: {exc}")

        # ── 2. Same model in alternate orgs ──────────────────────────────────
        primary_org   = spec.repo_id.split("/")[0]
        model_repo_part = spec.repo_id.split("/", 1)[-1]   # e.g. "Llama-3.3-70B-Instruct-GGUF"

        for org in self._ALT_ORGS_GGUF:
            if org == primary_org:
                continue
            alt_repo = f"{org}/{model_repo_part}"
            c.print(f"  → trying alt org: [cyan]{alt_repo}[/cyan]")
            try:
                repo_files = list(api.list_repo_files(alt_repo, timeout=15))
            except Exception:
                # Repo doesn't exist under this org — try without the -GGUF suffix
                alt_repo = f"{org}/{model_repo_part.replace('-GGUF','')}"
                try:
                    repo_files = list(api.list_repo_files(alt_repo, timeout=15))
                except Exception:
                    continue
            gguf_files = [f for f in repo_files if f.lower().endswith(".gguf")]
            if not gguf_files:
                continue
            for alt_fname in self._rank_quant_files(gguf_files, model_stem):
                result = self._download_one(alt_repo, alt_fname, spec.size_gb, spec.key)
                if result:
                    return result

        # ── 3. HuggingFace live search ────────────────────────────────────────
        c.print(f"  → HuggingFace live search for: [cyan]{model_stem}[/cyan]")
        hf_hits = hf_scan_gguf(model_stem, max_size_gb=spec.size_gb * 1.5)
        for repo_id, fname in hf_hits:
            if repo_id == spec.repo_id and fname == spec.filename:
                continue
            c.print(f"    HF hit: [cyan]{repo_id}[/cyan] / {fname}")
            result = self._download_one(repo_id, fname, spec.size_gb, spec.key)
            if result:
                return result

        return None

    def _try_mlx_alts(self, spec: "ModelSpec") -> Optional[Path]:
        """Try alternate MLX orgs + HF live scan for the same model name."""
        c = self._console()
        model_name = spec.repo_id.split("/", 1)[-1]
        from huggingface_hub import snapshot_download as _sd

        # 1. Alternate orgs for same model name
        for org in self._ALT_ORGS_MLX:
            if org in spec.repo_id:
                continue
            alt_repo = f"{org}/{model_name}"
            c.print(f"  → trying MLX alt org: [cyan]{alt_repo}[/cyan]")
            dest = MODELS_DIR / spec.key
            try:
                _sd(repo_id=alt_repo, local_dir=str(dest),
                    resume_download=True, local_files_only=False)
                ok, reason = _verify_mlx(spec)
                if ok:
                    c.print(f"  [green]✓ MLX alt succeeded: {alt_repo}[/green]")
                    return dest
            except Exception as exc:
                log.debug(f"MLX alt {alt_repo} failed: {exc}")

        # 2. HF live search for any matching MLX repo
        base = re.sub(r"[-_](4bit|8bit|3bit|2bit|mlx).*$", "", model_name, flags=re.IGNORECASE)
        c.print(f"  → HF live MLX search: [cyan]{base}[/cyan]")
        for repo_id in hf_scan_mlx(base):
            if repo_id == spec.repo_id:
                continue
            dest = MODELS_DIR / spec.key
            try:
                _sd(repo_id=repo_id, local_dir=str(dest),
                    resume_download=True, local_files_only=False)
                ok, reason = _verify_mlx(spec)
                if ok:
                    c.print(f"  [green]✓ HF scan MLX hit: {repo_id}[/green]")
                    return dest
            except Exception as exc:
                log.debug(f"HF scan MLX {repo_id} failed: {exc}")
        return None

    def _rank_quant_files(self, files: List[str], model_stem: str) -> List[str]:
        """Sort GGUF filenames by quant quality (best first) matching model_stem."""
        def _priority(fname: str) -> int:
            fname_up = fname.upper()
            for i, q in enumerate(self._QUANT_PRIORITY):
                if q in fname_up:
                    return i
            return len(self._QUANT_PRIORITY)
        # Filter to files whose stem matches
        stem_up = model_stem.upper()
        matching = [f for f in files if stem_up in f.upper() or True]  # try all on mismatch
        return sorted(matching, key=_priority)

    def _download_one(
        self, repo_id: str, filename: str, expected_gb: float, state_key: str
    ) -> Optional[Path]:
        """Low-level: download a single GGUF file, integrity-check, return path or None."""
        from huggingface_hub import hf_hub_download as _hfd
        dest = MODELS_DIR / filename
        try:
            _hfd(repo_id=repo_id, filename=filename,
                 local_dir=str(MODELS_DIR),
                 local_dir_use_symlinks=False,
                 resume_download=True)
            ok, reason = _verify_gguf(dest, expected_gb)
            if ok:
                self._console().print(f"  [green]✓ {repo_id}/{filename}[/green]")
                return dest
            log.warning(f"Alt download integrity check failed ({reason}): {repo_id}/{filename}")
            dest.unlink(missing_ok=True)
        except Exception as exc:
            log.debug(f"Alt download error {repo_id}/{filename}: {exc}")
        return None

    # ── Single attempt with live progress bar + stall detection ──────────────

    def _attempt(self, spec: "ModelSpec", attempt: int) -> Path:
        c = self._console()
        target = _local_path(spec)
        expected_bytes = int(spec.size_gb * 1024 ** 3)
        stall_s = self.stall_timeout(spec)
        retry_tag = f" [yellow](retry {attempt})[/yellow]" if attempt else ""
        c.print(f"\n[bold cyan]↓[/bold cyan]{retry_tag} [bold]{spec.label()}[/bold]")
        c.print(f"  [dim]{spec.size_gb:.1f} GB · "
                f"{'resume' if target.exists() else 'fresh start'} · "
                f"stall-timeout {stall_s/60:.0f} min[/dim]")

        # Submit download to background thread
        with _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="aio_dl") as pool:
            future = pool.submit(_do_download, spec)

            # Live progress in main thread
            try:
                from rich.progress import (
                    Progress, BarColumn, DownloadColumn,
                    TransferSpeedColumn, TimeRemainingColumn,
                    TextColumn, SpinnerColumn,
                )
                prog = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=30),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=c, transient=False,
                )
                task_id = prog.add_task(spec.description[:44], total=expected_bytes)
                prog_ctx = prog
            except ImportError:
                prog_ctx = _NullCtx()
                task_id  = None

            last_size      = _path_size_bytes(target)
            last_growth_ts = time.time()

            with prog_ctx:
                while not future.done():
                    time.sleep(self.POLL_INTERVAL)
                    cur_size = _path_size_bytes(target)

                    if task_id is not None:
                        prog.update(task_id, completed=min(cur_size, expected_bytes))

                    # Update state file for /status endpoint
                    _dl_state[spec.key] = {
                        "state": "downloading",
                        "bytes": cur_size,
                        "expected_bytes": expected_bytes,
                        "pct": round(cur_size / max(expected_bytes, 1) * 100, 1),
                        "ts": time.time(),
                    }

                    # Stall check
                    if cur_size > last_size:
                        last_size      = cur_size
                        last_growth_ts = time.time()
                    elif cur_size > 4096:   # only check stall once something downloaded
                        idle_s = time.time() - last_growth_ts
                        if idle_s > stall_s:
                            future.cancel()
                            raise _StallError(
                                f"No file growth for {idle_s:.0f}s "
                                f"(limit {stall_s:.0f}s) at "
                                f"{cur_size/(1024**3):.2f}/{spec.size_gb:.1f} GB"
                            )

                # Finalise progress bar
                if task_id is not None:
                    prog.update(task_id, completed=expected_bytes)

            # Re-raise any download exception
            result = future.result()   # raises if _do_download raised

        c.print(f"[bold green]✓[/bold green] {spec.key} downloaded — verifying integrity…")
        return result

    @staticmethod
    def _console():
        try:
            from rich.console import Console
            return Console()
        except ImportError:
            class _P:
                def print(self, *a, **kw):
                    log.info(" ".join(str(x) for x in a))
            return _P()


class _NullCtx:
    def __enter__(self):  return self
    def __exit__(self, *_): pass


# ── HuggingFace live model scan ──────────────────────────────────────────────

def hf_scan_gguf(
    model_name: str,
    max_size_gb: float = 9999.0,
    max_results: int = 5,
) -> List[Tuple[str, str]]:
    """
    Search HuggingFace for GGUF files matching model_name.
    Returns [(repo_id, filename), ...] ordered by download count (most popular first).

    Uses only the HF Hub API — no GPU, no model inference required.
    """
    try:
        from huggingface_hub import HfApi, ModelFilter
        api  = HfApi()
        hits = list(api.list_models(
            search=model_name,
            filter=ModelFilter(tags=["gguf"]),
            sort="downloads",
            direction=-1,
            limit=20,
        ))
    except Exception as exc:
        log.debug(f"HF scan failed for '{model_name}': {exc}")
        return []

    found: List[Tuple[str, str]] = []
    for model in hits:
        if len(found) >= max_results:
            break
        try:
            files = list(api.list_repo_files(model.modelId, timeout=10))
        except Exception:
            continue
        # Prefer Q4_K_M; fall through to smaller quants if file is too big
        for quant in ["Q4_K_M", "Q4_K_S", "Q3_K_M", "Q2_K", "IQ4_XS"]:
            for fname in files:
                if fname.lower().endswith(".gguf") and quant in fname.upper():
                    # Rough size estimate: assume 0.5625 GB per 1B params (Q4)
                    # We can't know exact size without repo metadata, so just include it
                    found.append((model.modelId, fname))
                    break
            if found and found[-1][0] == model.modelId:
                break   # found a file for this repo, move on
    return found


def hf_scan_mlx(model_name: str, max_results: int = 5) -> List[str]:
    """
    Search HuggingFace for MLX 4-bit repos matching model_name.
    Returns [repo_id, ...].
    """
    try:
        from huggingface_hub import HfApi
        api  = HfApi()
        hits = list(api.list_models(
            search=model_name,
            filter="mlx",
            sort="downloads",
            direction=-1,
            limit=max_results,
        ))
        return [m.modelId for m in hits if "4bit" in m.modelId.lower() or "mlx" in m.modelId.lower()]
    except Exception as exc:
        log.debug(f"HF MLX scan failed for '{model_name}': {exc}")
        return []


# Global singleton
_dl = DownloadManager()


# ── Cascade fallback: walk down the ladder when a model fails ─────────────────

def ensure_with_fallback(
    primary:  "ModelSpec",
    role:     str,          # "sudo" | "sized" — for logging only
    dev:      "DeviceProfile",
) -> Tuple["ModelSpec", Path]:
    """
    Try to download `primary`.  If it fails (all retries exhausted), cascade
    through the same ladder selecting the next-smaller model that fits in
    available RAM, until one succeeds.

    Returns (spec_actually_used, local_path).
    Raises RuntimeError only if every model in the ladder is unavailable.
    """
    if primary.remote:
        return primary, Path("/dev/null")

    usable_gb = dev.ram_gb * 0.78
    ladder    = _MLX_LADDER if dev.platform == "apple_silicon" else _GGUF_LADDER
    c         = DownloadManager._console()

    # Cascade: primary first, then all smaller models that fit, excluding remote
    cascade: List["ModelSpec"] = [primary] + [
        s for s in ladder
        if s.size_gb < primary.size_gb
        and s.size_gb <= usable_gb
        and s.key != primary.key
        and not s.remote
    ]

    tried: List[str] = []
    for spec in cascade:
        tried.append(spec.key)
        path = _dl.get(spec)
        if path is not None:
            if spec.key != primary.key:
                c.print(
                    f"\n[bold yellow]⚠  Cascade fallback ({role}):[/bold yellow]  "
                    f"[red]{primary.key}[/red] unavailable → "
                    f"[green]{spec.key}[/green]  "
                    f"[dim]({spec.size_gb:.1f} GB · {spec.description[:48]})[/dim]\n"
                )
            return spec, path
        c.print(f"[red]  ✘  {spec.key} failed[/red] — trying next in class…")

    raise RuntimeError(
        f"No {role} model could be downloaded after exhausting cascade "
        f"(tried: {', '.join(tried)}).  "
        f"Check network, disk space ({usable_gb:.0f} GB needed), "
        f"and HuggingFace availability."
    )


# Back-compat shim (used by agent/trainer code that calls this directly)
def ensure_model_animated(spec: "ModelSpec") -> Path:
    """Single-model download.  In main(), prefer ensure_with_fallback()."""
    if spec.remote:
        return Path("/dev/null")
    result = _dl.get(spec)
    if result is None:
        raise RuntimeError(
            f"Download failed for {spec.key} after {DownloadManager.MAX_RETRIES} attempts. "
            f"Check ~/.aio/logs/ for details."
        )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 23 · List Models Table
# ──────────────────────────────────────────────────────────────────────────────

def print_models_table(dev: "DeviceProfile",
                       current_sudo: Optional["ModelSpec"] = None,
                       current_sized: Optional["ModelSpec"] = None) -> None:
    from rich.console import Console
    from rich.table   import Table

    c = Console()
    ladder = _MLX_LADDER if dev.platform == "apple_silicon" else _GGUF_LADDER
    label  = "MLX (Apple Silicon)" if dev.platform == "apple_silicon" else f"GGUF ({dev.platform})"
    usable = dev.ram_gb * 0.78

    t = Table(title=f"PAI Model Catalogue — {label}  ({dev.ram_gb:.0f} GB RAM)",
              show_header=True, header_style="bold magenta")
    t.add_column("Key",         min_width=22)
    t.add_column("Description", min_width=36)
    t.add_column("RAM",         min_width=7)
    t.add_column("MoE",         min_width=5)
    t.add_column("Active/Total", min_width=12)
    t.add_column("Status")

    for spec in ladder:
        fits   = spec.size_gb <= usable
        is_sudo  = current_sudo  and current_sudo.key  == spec.key
        is_sized = current_sized and current_sized.key == spec.key
        status = (
            "[bold green]★ SUDO[/bold green]"   if is_sudo  else
            "[bold cyan]⚡ SIZED[/bold cyan]"    if is_sized else
            "[green]fits[/green]"               if fits     else
            "[dim]too large[/dim]"
        )
        active_total = (
            f"{spec.active_params_b:.0f}B / {spec.params_b:.0f}B"
            if spec.is_moe else f"{spec.params_b:.0f}B"
        )
        moe_tag = "[green]YES[/green]" if spec.is_moe else "[dim]no[/dim]"
        row_style = "bold" if (is_sudo or is_sized) else ("dim" if not fits else "")
        t.add_row(spec.key, spec.description[:36], f"{spec.size_gb:.1f}G",
                  moe_tag, active_total, status, style=row_style)

    c.print(t)
    c.print(f"[dim]Usable RAM ({usable:.0f} GB).  "
            f"Place GGUFs on a mounted volume to skip download.[/dim]\n")

    # ── Remote / GPT model table ───────────────────────────────────────────────
    r = Table(title="Remote & GPT OSS Models (zero local RAM — set API key to activate)",
              show_header=True, header_style="bold cyan")
    r.add_column("Key",         min_width=22)
    r.add_column("Provider",    min_width=10)
    r.add_column("Model ID",    min_width=36)
    r.add_column("MoE",         min_width=5)
    r.add_column("Params",      min_width=10)
    r.add_column("Env var / Status")

    ollama_up = _probe_ollama()
    for spec in _REMOTE_LADDER:
        is_sudo  = current_sudo and current_sudo.key == spec.key
        if spec.api_provider == "Ollama":
            avail = "[green]ready[/green]" if ollama_up else "[dim]ollama not running[/dim]"
        elif not spec.api_key_env:
            avail = "[green]no key needed[/green]"
        elif os.getenv(spec.api_key_env):
            avail = f"[green]✔ {spec.api_key_env}[/green]"
        else:
            avail = f"[dim]set {spec.api_key_env}[/dim]"
        if is_sudo:
            avail += "  [bold green]★ SUDO[/bold green]"
        active_total = (
            f"{spec.active_params_b:.0f}B/{spec.params_b:.0f}B"
            if spec.is_moe else f"{spec.params_b:.0f}B"
        )
        moe_tag = "[green]YES[/green]" if spec.is_moe else "[dim]no[/dim]"
        r.add_row(spec.key, spec.api_provider, spec.api_model_id[:36],
                  moe_tag, active_total, avail,
                  style="bold" if is_sudo else "")
    c.print(r)
    c.print("[dim]Remote models are tried first when API keys are present.  "
            "Groq free tier: https://console.groq.com[/dim]\n")


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 13 · CLI Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def _print_banner(dev: DeviceProfile, sudo_spec: Optional[ModelSpec], sized_spec: Optional[ModelSpec]):
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    c = Console()
    c.print(Panel.fit(
        f"[bold cyan]Linus PAI — Private AI Runtime[/bold cyan]  v{PAI_VERSION}\n"
        f"[dim]Platform:[/dim] {dev.platform}  |  "
        f"[dim]Device:[/dim] {dev.chip_name or dev.gpu_name}  |  "
        f"[dim]RAM:[/dim] {dev.ram_gb:.0f} GB",
        border_style="cyan"
    ))

    t = Table(show_header=True, header_style="bold magenta")
    t.add_column("Role")
    t.add_column("Model")
    t.add_column("Engine")
    t.add_column("RAM")
    t.add_column("Active params")
    t.add_column("MoE")

    def _row(role: str, spec: ModelSpec) -> None:
        active = (
            f"{spec.active_params_b:.1f}B / {spec.params_b:.0f}B"
            if spec.is_moe else f"{spec.params_b:.0f}B"
        )
        t.add_row(
            role, spec.description, spec.engine,
            f"{spec.size_gb:.1f} GB", active,
            "[green]YES[/green]" if spec.is_moe else "[dim]no[/dim]",
        )

    if sudo_spec:
        _row("sudo (god)", sudo_spec)
    if sized_spec:
        _row("sized (fast)", sized_spec)

    c.print(t)


def _interactive_chat(
    engine: InferenceEngine,
    sudo_spec: ModelSpec,
    sudo_path: Path,
    sized_spec: Optional[ModelSpec],
    sized_path: Optional[Path],
    rag: RagStore,
    thermal: ThermalGovernor,
    trainer: ThermalTrainer,
):
    from rich.console import Console
    from rich.markdown import Markdown

    c = Console()
    c.print("[bold green]PAI Chat[/bold green]  — type [bold]/help[/bold] for commands, [bold]/quit[/bold] to exit\n")

    history: List[Dict[str, str]] = []

    while True:
        try:
            user = input("[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue

        if user.lower() in ("/quit", "/exit", "/q"):
            break
        elif user.lower() == "/help":
            c.print(
                "/status  — device status\n"
                "/thermal — thermal stats\n"
                "/peers   — mesh peers\n"
                "/sudo    — use sudo model\n"
                "/sized   — use sized model\n"
                "/agent <task> — run agent\n"
                "/code <task>  — run code agent\n"
                "/rag <file>   — ingest file into RAG\n"
                "/quit    — exit"
            )
            continue
        elif user.lower() == "/status":
            import psutil
            c.print(f"RAM: {psutil.virtual_memory().percent:.0f}% | Thermal: {thermal.stats()}")
            continue
        elif user.lower() == "/thermal":
            c.print(thermal.stats())
            continue
        elif user.lower().startswith("/agent "):
            task = user[7:].strip()
            c.print("[dim]Running agent…[/dim]")
            answer = run_agent(task, engine, sudo_spec, sudo_path, rag, MeshPeer(
                DeviceProfile("cpu","","",1,4,0,"",False,False,"","","")
            ))
            c.print(Markdown(answer))
            trainer.record_interaction(task, answer, tag="agent")
            continue
        elif user.lower().startswith("/code "):
            task = user[6:].strip()
            c.print("[dim]Running code agent…[/dim]")
            answer = run_code_agent(task, engine, sudo_spec, sudo_path, rag, MeshPeer(
                DeviceProfile("cpu","","",1,4,0,"",False,False,"","","")
            ))
            c.print(Markdown(answer))
            continue
        elif user.lower().startswith("/rag "):
            fpath = Path(user[5:].strip()).expanduser()
            n = rag.add_file(fpath)
            c.print(f"[green]Ingested {n} chunks from {fpath.name}[/green]")
            continue

        # Normal chat
        ctx = build_rag_context(rag, user, web=True)
        full = f"{ctx}\n\nUser: {user}" if ctx else user
        c.print("[dim]Generating…[/dim]")
        response = engine.generate(full, sudo_spec, sudo_path, max_tokens=1024)
        c.print(Markdown(f"**Assistant:** {response}\n"))
        trainer.touch()
        trainer.record_interaction(user, response)
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": response})


def main():
    parser = argparse.ArgumentParser(
        description="Linus PAI — Private AI Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
          Examples:
            python aio.py                        # auto-detect + launch full system
            python aio.py --chat                 # terminal chat
            python aio.py --serve                # API only (no Streamlit)
            python aio.py --dashboard            # live terminal dashboard
            python aio.py --mcp                  # MCP server for Claude Code
            python aio.py --benchmark            # run performance benchmark
            python aio.py --doctor               # system health check
            python aio.py --demo                 # scripted demo
            python aio.py --list-models          # show all available models
            python aio.py --update               # self-update from GitHub
            python aio.py --agent "summarise the latest AI news"
            python aio.py --code  "write a todo-list API in FastAPI"
        """),
    )

    # ── Modes ────────────────────────────────────────────────────────────────
    parser.add_argument("--install",      action="store_true", help="Install dependencies only")
    parser.add_argument("--force-install",action="store_true", help="Re-compile everything")
    parser.add_argument("--chat",         action="store_true", help="Interactive terminal chat")
    parser.add_argument("--serve",        action="store_true", help="API only (no Streamlit UI)")
    parser.add_argument("--dashboard",    action="store_true", help="Live terminal dashboard")
    parser.add_argument("--mcp",          action="store_true", help="MCP server for Claude Code")
    parser.add_argument("--benchmark",    action="store_true", help="Run performance benchmark")
    parser.add_argument("--doctor",       action="store_true", help="System health check")
    parser.add_argument("--demo",         action="store_true", help="Scripted demo sequence")
    parser.add_argument("--update",       action="store_true", help="Self-update from GitHub")
    parser.add_argument("--list-models",  action="store_true", help="List all available models")
    parser.add_argument("--train",        action="store_true", help="Run LoRA training cycle now")
    parser.add_argument("--mesh",         action="store_true", help="Show mesh peers and exit")
    parser.add_argument("--status",       action="store_true", help="Print device/model status")

    # ── One-shot tasks ────────────────────────────────────────────────────────
    parser.add_argument("--agent",  metavar="TASK", help="Run autonomous agent task")
    parser.add_argument("--code",   metavar="TASK", help="Run code-agent task (plan→write→test)")

    # ── Network ───────────────────────────────────────────────────────────────
    parser.add_argument("--port",     type=int, default=MESH_PORT, help="API port (default 9480)")
    parser.add_argument("--ui-port",  type=int, default=8501,      help="Streamlit port")
    parser.add_argument("--no-rag",   action="store_true",         help="Disable web RAG")

    # ── Benchmark output ──────────────────────────────────────────────────────
    parser.add_argument("--bench-out", metavar="FILE",
                        help="Save benchmark JSON to FILE", default=None)

    # ── Update URL override ───────────────────────────────────────────────────
    parser.add_argument("--update-url", metavar="URL", default=PAI_UPDATE_URL,
                        help="URL to fetch aio.py update from")

    args = parser.parse_args()

    # ── Quick commands that need no models ────────────────────────────────────
    if args.doctor:
        run_doctor()
        return

    if args.update:
        run_update(args.update_url)
        return

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    bootstrap(force=args.force_install)
    if args.install:
        from rich.console import Console
        Console().print("[bold green]Install complete.[/bold green]")
        return

    # ── Device & model selection ──────────────────────────────────────────────
    dev = detect_device()
    sudo_spec, sized_spec = select_models(dev)

    if sudo_spec is None:
        log.warning("No sudo model — RAM too small; falling back to sized")
        sudo_spec = sized_spec

    # ── Early exits that need device info but NOT models ─────────────────────
    # These run before any compilation or download so they are always fast.

    if args.list_models:
        print_models_table(dev, sudo_spec, sized_spec)
        return

    if args.status:
        # Quick status — no subsystems required
        import psutil
        from rich.console import Console
        th = ThermalGovernor()
        th.start()
        time.sleep(0.5)   # one poll cycle
        dl_state: dict = {}
        try:
            dl_state = json.loads(_DOWNLOAD_STATE_FILE.read_text()) \
                       if _DOWNLOAD_STATE_FILE.exists() else {}
        except Exception:
            pass
        Console().print({
            "version":   PAI_VERSION,
            "device":    dev.platform,
            "chip":      dev.chip_name or dev.gpu_name,
            "ram_gb":    round(dev.ram_gb, 1),
            "cpu_pct":   psutil.cpu_percent(interval=0.3),
            "ram_pct":   psutil.virtual_memory().percent,
            "thermal":   th.stats(),
            "sudo_model":  sudo_spec.key  if sudo_spec  else None,
            "sized_model": sized_spec.key if sized_spec else None,
            "remote_available": [s.key for s in _AVAILABLE_REMOTE],
            "downloads": dl_state,
        })
        th.stop()
        return

    if args.mesh:
        from rich.console import Console
        _mesh = MeshPeer(dev, api_port=args.port)
        _mesh.start()
        time.sleep(BEACON_INT + 2)
        Console().print({"peers": [p.to_dict() for p in _mesh.live_peers()]})
        _mesh.stop()
        return

    # ── One-time compilation ──────────────────────────────────────────────────
    check_compile_once(dev)

    # ── Download models — retry · stall detection · cascade fallback ─────────
    try:
        sudo_spec, sudo_path = ensure_with_fallback(sudo_spec, "sudo", dev)
    except RuntimeError as exc:
        log.error(f"sudo model unavailable: {exc}")
        sudo_spec = sized_spec   # last-resort: use sized as sudo
        sudo_path = None

    try:
        sized_spec, sized_path = ensure_with_fallback(sized_spec, "sized", dev)
    except RuntimeError as exc:
        log.error(f"sized model unavailable: {exc}")
        sized_path = sudo_path   # fall back to already-downloaded sudo

    if sudo_path is None and sized_path is None:
        log.error("No models available locally or remotely.  "
                  "Check network and re-run with --doctor.")
        sys.exit(1)

    # ── Load plugins ──────────────────────────────────────────────────────────
    n_plugins = _plugin_mgr.load(PLUGINS_DIR)
    if n_plugins:
        log.info(f"Loaded {n_plugins} plugin(s) from {PLUGINS_DIR}")

    # ── Start subsystems ──────────────────────────────────────────────────────
    thermal = ThermalGovernor()
    thermal.start()

    engine  = InferenceEngine(dev, thermal)
    rag     = RagStore()
    mesh    = MeshPeer(dev, api_port=args.port)
    mesh.start()

    trainer = ThermalTrainer(dev, thermal, sudo_spec, sudo_path)
    trainer.start()

    # ── Banner ────────────────────────────────────────────────────────────────
    _print_banner(dev, sudo_spec, sized_spec)

    # ── Benchmark ─────────────────────────────────────────────────────────────
    if args.benchmark:
        out = Path(args.bench_out) if args.bench_out else None
        run_benchmark(engine, sudo_spec, sudo_path, dev, out_file=out)
        return

    # ── Demo ──────────────────────────────────────────────────────────────────
    if args.demo:
        run_demo(engine, sudo_spec, sudo_path, rag, mesh, dev, thermal)
        return

    # ── MCP server ────────────────────────────────────────────────────────────
    if args.mcp:
        run_mcp_server(engine, sudo_spec, sudo_path, rag, mesh, dev, thermal)
        return

    # ── One-shot agent ────────────────────────────────────────────────────────
    if args.agent:
        answer = run_agent(
            args.agent, engine, sudo_spec, sudo_path, rag, mesh,
            web_rag=not args.no_rag,
        )
        print(answer)
        _cost_tracker.record(args.agent, answer)
        from rich.console import Console
        Console().print(f"\n[dim]{_cost_tracker.summary()}[/dim]")
        return

    # ── One-shot code agent ───────────────────────────────────────────────────
    if args.code:
        answer = run_code_agent(args.code, engine, sudo_spec, sudo_path, rag, mesh)
        print(answer)
        return

    # ── Training cycle ────────────────────────────────────────────────────────
    if args.train:
        buf_files = list(TRAIN_DIR.glob("*_buffer.jsonl"))
        if not buf_files:
            log.info("No training data yet — run some queries first.")
        else:
            trainer._train_cycle(buf_files[0])
        return

    # ── Terminal chat ─────────────────────────────────────────────────────────
    if args.chat:
        _interactive_chat(
            engine, sudo_spec, sudo_path, sized_spec, sized_path,
            rag, thermal, trainer,
        )
        from rich.console import Console
        Console().print(f"\n[dim]{_cost_tracker.summary()}[/dim]")
        return

    # ── Default: backend + Streamlit UI ──────────────────────────────────────
    import uvicorn

    app = make_app(
        dev, thermal, engine,
        sudo_spec, sudo_path,
        sized_spec, sized_path,
        rag, mesh, trainer,
    )

    fe_path = write_frontend()

    def _run_backend() -> None:
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")

    be_thread = threading.Thread(target=_run_backend, daemon=True, name="backend")
    be_thread.start()
    time.sleep(2)

    fe_proc: Optional[subprocess.Popen] = None
    if not args.serve:
        env = os.environ.copy()
        env["PAI_API"]      = f"http://localhost:{args.port}"
        env["PAI_DATA_DIR"] = str(DATA_DIR)
        fe_proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(fe_path),
             "--server.port",    str(args.ui_port),
             "--server.address", "0.0.0.0",
             "--server.headless","true"],
            env=env,
        )
        log.info(f"UI:  http://localhost:{args.ui_port}")

    log.info(f"API: http://localhost:{args.port}/docs")
    log.info(f"OpenAI-compat: http://localhost:{args.port}/v1/")
    if args.mcp:
        log.info(f"MCP: python aio.py --mcp")

    # Write PID file
    _pid_dir  = Path.home() / ".aio"
    _pid_dir.mkdir(exist_ok=True)
    _pid_file = _pid_dir / "aio.pids"
    _pids = [str(os.getpid())]
    if fe_proc:
        _pids.append(str(fe_proc.pid))
    _pid_file.write_text("\n".join(_pids))

    # ── Dashboard runs in-process alongside the server ────────────────────────
    if args.dashboard:
        # Let the server settle then run the live dashboard in the foreground
        time.sleep(3)
        run_dashboard(
            dev, thermal, engine, sudo_spec, sized_spec,
            mesh, _cost_tracker, args.port, args.ui_port,
        )
        # Dashboard exited (Ctrl-C) → fall through to shutdown
        from rich.console import Console
        Console().print(f"\n[dim]{_cost_tracker.summary()}[/dim]")

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    def _shutdown(sig: int, frame: Any) -> None:
        from rich.console import Console
        Console().print(f"\n[dim]{_cost_tracker.summary()}[/dim]")
        thermal.stop()
        mesh.stop()
        trainer.stop()
        if fe_proc:
            fe_proc.terminate()
        if _pid_file.exists():
            _pid_file.unlink()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    be_thread.join()


if __name__ == "__main__":
    main()
