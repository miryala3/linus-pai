# Linus PAI — Private AI Runtime

> **Run powerful AI entirely on your own hardware. No cloud. No API fees. No data leaving your device.**

[![CI](https://github.com/miryala3/linus-pai/actions/workflows/test.yml/badge.svg)](https://github.com/miryala3/linus-pai/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#platform-support)

Linus PAI is a **single-file** Python runtime that auto-detects your hardware, downloads the best model for your device, and gives you a full AI stack — inference, RAG, web search, autonomous agents, thermal-gated training, and a mesh network — all in one command.

Scales from a Raspberry Pi (1B model) to a 128 GB Mac Studio (Llama 4 Scout MoE, 10 million token context) with zero configuration.

---

## Download — no Python required

**Grab the `pai` launcher for your platform and run it directly.**
It downloads an embedded Python 3.12 runtime on first launch and compiles the GPU backend automatically (~5–15 min once, then instant).

### macOS / Linux — single executable

```bash
# Download
curl -Lo pai https://github.com/miryala3/linus-pai/releases/latest/download/pai
chmod +x pai

# Run (downloads Python 3.12 + compiles GPU backend on first launch)
./pai
./pai --chat
./pai --doctor
```

Or clone and run the launcher directly from the repo:

```bash
git clone https://github.com/miryala3/linus-pai.git
cd linus-pai
./pai            # same self-contained launcher
```

### Windows — single executable

```bat
:: Download pai.cmd or pai.exe from GitHub Releases, then:
pai.cmd          :: self-contained launcher (no Python needed)
pai.exe          :: native binary (built by maintainers)
```

### One-line install (puts `pai` on PATH)

```bash
curl -sSL https://raw.githubusercontent.com/miryala3/linus-pai/main/install.sh | bash
# Then from anywhere:
pai --chat
pai --doctor
```

**UI → http://localhost:8501  ·  API → http://localhost:9480/docs  ·  OpenAI-compat → http://localhost:9480/v1/**

---

## How the launcher works

The `pai` binary is a self-bootstrapping executable with **zero prerequisites**:

```
First run                          Subsequent runs
─────────────────────────────      ───────────────────
./pai                              ./pai --chat  (< 1 second)
  │                                  │
  ├─ Detect platform/arch            └─ Activate ~/.linus-pai/venv
  ├─ Find Python 3.10+ on system         exec pai.py --chat
  │   (walks python3.12→3.11→3.10→3.13)
  │   └─ Not found?  Download
  │       python-build-standalone
  │       (~70 MB, one-time)
  ├─ Create ~/.linus-pai/venv
  ├─ pai.py --force-install
  │   ├─ Compile llama-cpp (Metal/
  │   │   CUDA/ROCm/Vulkan/CPU)
  │   └─ Install all Python deps
  └─ Launch pai.py
```

The native binary (`dist/pai`) uses the same flow but finds a system Python for venv creation — `sys.executable` inside a PyInstaller bundle is the frozen binary, not a real interpreter.

Files written to `~/.linus-pai/`:

| Path | Contents |
|---|---|
| `python/` | Embedded Python 3.12 (if downloaded by shell launcher) |
| `venv/` | Virtual environment with all deps |
| `data/` | Models, adapters, RAG index, plugins |
| `install.log` | Full output of the last bootstrap run (overwritten each time) |
| `bootstrap.log` | Append-only: timestamps + errors only — grep here if setup fails |

Override defaults with environment variables:

```bash
PAI_HOME=/mnt/fast-drive/.linus-pai ./pai   # custom data location
PAI_PYTHON=/opt/homebrew/bin/python3.12 ./pai  # use a specific Python
```

---

## Build a native binary yourself

CI automatically builds and attaches binaries to every GitHub Release.
To build locally with PyInstaller:

```bash
# macOS / Linux → dist/pai
make build

# macOS universal2 fat binary (runs on both Apple Silicon and Intel)
make build-universal

# Windows → dist\pai.exe
build\build.bat

# Compress with UPX (~40 % smaller)
bash build/build.sh --upx
```

Pre-built binaries on [GitHub Releases](https://github.com/miryala3/linus-pai/releases):

| File | Platform |
|---|---|
| `pai-macos-arm64` | macOS Apple Silicon (M1–M4) |
| `pai-macos-x86_64` | macOS Intel |
| `pai-linux-x86_64` | Linux x86_64 |
| `pai-linux-arm64` | Linux arm64 / Raspberry Pi 5 |
| `pai-windows-x86_64.exe` | Windows 10/11 x64 |

Every binary ships with a `.sha256` checksum. Verify before running:

```bash
# Example: macOS arm64
curl -Lo pai https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64
curl -Lo pai.sha256 https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64.sha256
shasum -a 256 -c pai.sha256 && chmod +x pai && ./pai --version
```

See [SECURITY.md](SECURITY.md) for Gatekeeper notes and supply-chain details.

---

## Why Linus PAI?

| Feature | Linus PAI | Ollama | LM Studio |
|---|---|---|---|
| OpenAI-compatible API | ✅ `/v1/chat/completions` | ✅ | ✅ |
| MoE model support | ✅ Llama 4 Scout, Mixtral, DeepSeek | ✅ | ✅ |
| Web RAG (DuckDuckGo) | ✅ built-in | ❌ | ❌ |
| Autonomous agents (ReAct) | ✅ 10 tools | ❌ | ❌ |
| Code agent (plan→write→test) | ✅ | ❌ | ❌ |
| Thermal governor (5-stage) | ✅ predictive | ❌ | ❌ |
| LoRA training (idle-time) | ✅ Apple Silicon | ❌ | ❌ |
| Mesh / distributed inference | ✅ exo-style UDP | ❌ | ❌ |
| MCP server (Claude Code) | ✅ | ❌ | ❌ |
| Plugin system | ✅ drop .py in folder | ❌ | ❌ |
| AMD ROCm + Vulkan | ✅ | ✅ | ❌ |
| Session cost tracker | ✅ vs GPT-4o | ❌ | ❌ |
| Download resilience (retry/fallback) | ✅ | partial | partial |
| Single-file, zero-dep first run | ✅ | ❌ | ❌ |

---

## Features

- **Auto-detects everything** — chip, RAM, GPU (Apple Metal · NVIDIA CUDA · AMD ROCm · AMD/Intel Vulkan · CPU); selects the best model automatically
- **MoE-first model selection** — prefers Mixture-of-Experts models at each RAM tier for more quality per GB
- **Two-model architecture** — `sudo` (best model that fits) + `sized` (fast small model for routing/quick replies)
- **Network mount scan** — finds GGUFs on `/Volumes`, `/mnt`, `/media` and uses them without re-downloading
- **Remote GPT/cloud fallback** — set `OPENAI_API_KEY`, `GROQ_API_KEY` etc.; remote models auto-selected as sudo
- **Reliable downloads** — 3-attempt retry with backoff · stall detection · `resume_download=True` · GGUF magic-byte integrity check · cascade to next model on failure · HuggingFace live scan for alternatives
- **RAG** — ingest multiple PDF/TXT/MD files at once; cosine similarity retrieval; DuckDuckGo web search fused into every query
- **Agents** — ReAct loop with 10 built-in tools, Python sandbox, code agent (plan→write→test→fix)
- **MCP server** — works as a local tool in Claude Code, Cursor, or any MCP host
- **Thermal training** — idle-time LoRA fine-tuning on Apple Silicon, thermally gated (5-stage state machine)
- **Plugin system** — drop a `.py` in `pai_data/plugins/` to add new agent tools and API endpoints
- **Live dashboard** — Rich terminal UI with thermal sparkline, RAM bars, cost savings, peer list, query feed
- **OpenAI-compatible API** — drop-in: set `base_url=http://localhost:9480` in any OpenAI SDK

---

## Platform support

| Platform | Backend | Status |
|---|---|---|
| Apple Silicon M1–M4 | MLX native Metal | ✅ |
| Apple Intel Mac + AMD/Radeon GPU | llama-cpp Metal (GPU layers) | ✅ |
| Apple Intel Mac (no discrete GPU) | llama-cpp CPU | ✅ |
| Linux + NVIDIA GPU | llama-cpp CUDA | ✅ |
| Linux + AMD GPU (RDNA2+) | llama-cpp ROCm/HIPBlas | ✅ |
| Linux + any Vulkan 1.1+ GPU | llama-cpp Vulkan | ✅ |
| Linux CPU-only | llama-cpp AVX2 | ✅ |
| Windows + NVIDIA | llama-cpp CUDA | ✅ |
| Windows + AMD Vulkan | llama-cpp Vulkan | ✅ |
| Windows CPU | llama-cpp | ✅ |
| Raspberry Pi / ARM Linux | llama-cpp CPU | ✅ (1B–3B models) |

> **Intel Mac AMD GPU note** — Auto-detected via `system_profiler SPDisplaysDataType`.
> llama-cpp-python is compiled with `-DGGML_METAL=on` so GPU layers run on the discrete Radeon.
> Run `./pai --status` to confirm GPU name and VRAM are detected.

---

## Model catalogue

Auto-selected by available RAM. MoE models preferred at each tier (more total parameters per GB).

### ~120B class  (64 GB+ RAM)

| Model | Engine | RAM | Notes |
|---|---|---|---|
| Mistral Large 2 (123B) | GGUF Q4_K_M | 69 GB | 128k ctx · Mistral Research License |
| **Meta Llama 4 Scout** (109B MoE) | GGUF + MLX | 61 GB | **17B active params** · 10M token context |
| Cohere Command R+ (104B) | GGUF Q4_K_M | 58 GB | Apache 2.0 · RAG-optimised · 128k ctx |
| Mixtral 8×22B (141B MoE) | GGUF Q2_K · MLX 4-bit | 52/49 GB | 39B active params |

### ~20B class  (16–48 GB RAM)

| Model | Engine | RAM | Notes |
|---|---|---|---|
| Llama-3.3 70B | GGUF + MLX | 42 GB | Meta flagship |
| DeepSeek R1-Distill Llama-70B | GGUF | 39 GB | Full R1 reasoning at 70B scale |
| Mixtral 8×7B (47B MoE) | GGUF + MLX | 26/25 GB | 13B active params |
| Cohere Command R 35B | GGUF | 20 GB | Apache 2.0 · 128k RAG-optimised |
| Qwen2.5 32B | GGUF + MLX | 19/20 GB | Alibaba |
| **DeepSeek R1-Distill Qwen-32B** | GGUF | 18 GB | R1 reasoning distil |
| **QwQ-32B** | GGUF + MLX | 18 GB | Open chain-of-thought reasoning |
| **Google Gemma 3 27B** | GGUF + MLX | 15 GB | 128k ctx · Mar 2025 |
| **Mistral Small 3.1 22B** | GGUF + MLX | 13 GB | Mar 2025 |

### 14B tier  (8–12 GB RAM)

| Model | Notes |
|---|---|
| Qwen2.5 14B | Alibaba |
| DeepSeek-V2-Lite MoE | 2.4B active / 15.7B total |
| Microsoft Phi-4 | GPT-4-class OSS at 14B |
| DeepSeek R1-Distill Qwen-14B | R1 reasoning in 14B |
| Mistral Nemo 12B | Apache 2.0 · 128k ctx · NVIDIA co-developed |

### GPT-family OSS  (4–8 GB RAM)

| Model | Origin | Notes |
|---|---|---|
| OpenHermes 2.5 Mistral-7B | Community | GPT-4-quality instruction fine-tune |
| WizardLM-2 7B | Microsoft Research | 32k context |
| GPT4All-J 6B | EleutherAI | True OpenAI-architecture (GPT-J) OSS |

### Edge / IoT  (< 4 GB RAM)

| Model | RAM |
|---|---|
| Llama-3.2 3B | 2 GB |
| Llama-3.2 1B | 0.8 GB |

### Remote / cloud  (0 GB local RAM)

Set the environment variable; the model is auto-selected as sudo on next run.

| Provider | Models | Key | Cost |
|---|---|---|---|
| OpenAI | GPT-4o · o3-mini · o1-mini · GPT-4o-mini | `OPENAI_API_KEY` | pay-per-token |
| Anthropic | Claude Opus 4.7 · Claude Sonnet 4.6 | `ANTHROPIC_API_KEY` | pay-per-token |
| **Groq** | Llama-3.3 70B · Mixtral 8×7B · Llama-3.1 8B | `GROQ_API_KEY` | **free tier** · ~275 tok/s |
| Together AI | Qwen2.5 72B · Llama 405B · Mixtral 8×22B | `TOGETHER_API_KEY` | pay-per-token |
| **Ollama** | Llama3.3 · Phi-4 (local server) | none | free |

[Groq free tier signup](https://console.groq.com) takes 30 seconds.

---

## All CLI options

```
python pai.py                         # auto-detect + launch web UI + API
python pai.py --chat                  # interactive terminal chat
python pai.py --serve                 # API only (no Streamlit)
python pai.py --dashboard             # live terminal dashboard
python pai.py --mcp                   # MCP server (Claude Code / Cursor)
python pai.py --benchmark             # performance benchmark + shareable card
python pai.py --bench-out result.json # save benchmark results to file
python pai.py --doctor                # system health check (20+ tests)
python pai.py --demo                  # scripted 6-step showcase
python pai.py --list-models           # full model catalogue
python pai.py --update                # self-update from GitHub
python pai.py --train                 # trigger LoRA training cycle now
python pai.py --status                # device / thermal / model / peer status
python pai.py --mesh                  # show live mesh peers and exit
python pai.py --install               # install dependencies only
python pai.py --force-install         # recompile llama-cpp for current GPU
python pai.py --agent "TASK"          # one-shot autonomous agent task
python pai.py --code  "TASK"          # one-shot code-agent (plan→write→test)
python pai.py --port  9480            # override API port  (default 9480)
python pai.py --ui-port 8501          # override Streamlit port (default 8501)
python pai.py --no-rag                # disable web RAG
```

`runpai.sh` / `runpai.bat` accept the same options.

---

## REST API

Base URL: `http://localhost:9480` — interactive docs at `/docs`

### Core

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Device, thermal, models, mesh peers, download state |
| GET | `/thermal` | Thermal state, temperature, 60s prediction |
| POST | `/generate` | Chat completion (`model: sudo/sized/auto`) |
| POST | `/agent` | Autonomous agent (`code_mode: true` for code agent) |

### OpenAI-compatible

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/v1/models` | List available models |
| POST | `/v1/chat/completions` | Streaming SSE + batch |
| POST | `/v1/completions` | Legacy text completion |
| GET | `/v1/usage` | Session token counts + cost vs GPT-4o |

### RAG & Downloads

| Method | Endpoint | Description |
|---|---|---|
| POST | `/rag/add` | Add text to document store |
| POST | `/rag/upload` | Upload PDF/TXT/MD file |
| POST | `/rag/search` | Semantic search |
| GET | `/download/status` | Live progress for in-flight downloads |
| GET | `/download/verify/{key}` | Integrity check a local model |
| POST | `/download/scan` | HuggingFace live search |
| GET | `/mesh/peers` | Live mesh peer list |
| POST | `/train/trigger` | Trigger LoRA training cycle |

### OpenAI SDK example

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:9480/v1", api_key="not-needed")

for chunk in client.chat.completions.create(
    model="aio-sudo",
    messages=[{"role": "user", "content": "Explain transformers in 2 sentences."}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")
```

Works with LangChain, LiteLLM, AutoGen, Open Interpreter, Cursor, and any tool accepting a custom `base_url`.

---

## MCP integration (Claude Code / Cursor)

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "linus-pai": {
      "command": "python",
      "args": ["/path/to/linus-pai/pai.py", "--mcp"]
    }
  }
}
```

Tools exposed: `pai_generate`, `pai_search_web`, `pai_rag_query`, `pai_run_agent`, `pai_status`.

---

## Desktop shortcuts

```bash
bash scripts/install_desktop.sh           # macOS / Linux
scripts\install_desktop.bat               # Windows
bash scripts/install_desktop.sh --remove  # remove
```

macOS: double-click **Launch PAI** from Finder or Desktop.
Linux: GNOME/KDE app launcher + Desktop.
Windows: Desktop + Start Menu.

---

## Sample programs

All 11 samples connect to `PAI_API=http://localhost:9480` — fully offline by default:

| File | What it does |
|---|---|
| `chat.py` | Multi-turn chat: personas, file context, web search, spinning indicator |
| `code_review.py` | Code review with severity ratings; `--diff` for git changes; `--watch` mode |
| `test_gen.py` | Auto-generate pytest suites (happy path, edge cases, error paths, mocks) |
| `bug_fixer.py` | Error-driven bug fixer; `--loop` runs script until it passes |
| `doc_writer.py` | Add docstrings, generate README.md and API reference |
| `refactor.py` | Targeted refactoring with diff preview; 10 built-in goals |
| `commit_writer.py` | Conventional Commits generator; `--hook` installs as git hook |
| `data_analyst.py` | NL→Pandas for CSV/JSON/SQLite; `--report` for full auto-report |
| `sql_helper.py` | NL→SQL for SQLite/PostgreSQL/MySQL; interactive REPL |
| `web_scraper.py` | Describe what to scrape → gets structured JSON/CSV |
| `agentic_task.py` | Autonomous multi-step task runner; `--pipeline tasks.json` |

See [`samples/README.md`](samples/README.md).

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PAI_DATA_DIR` | `./pai_data` | Root data dir (models, adapters, RAG, buffers) |
| `PAI_API` | `http://localhost:9480` | API URL (used by samples + Streamlit) |
| `PAI_UPDATE_URL` | GitHub raw URL | Source for `--update` |
| `OPENAI_API_KEY` | _(unset)_ | Enables GPT-4o / o3-mini / o1-mini / GPT-4o-mini |
| `ANTHROPIC_API_KEY` | _(unset)_ | Enables Claude Opus 4.7 / Sonnet 4.6 |
| `GROQ_API_KEY` | _(unset)_ | Enables Groq free tier (~275 tok/s) |
| `TOGETHER_API_KEY` | _(unset)_ | Enables Together AI hosted open models |
| `PAI_TEST_NETWORK` | _(unset)_ | Set to `1` to run live network tests |

---

## Plugin system

Drop a `.py` in `pai_data/plugins/` — loaded automatically on startup:

```python
# pai_data/plugins/my_plugin.py

def my_tool(args_str: str) -> str:
    return f"Processed: {args_str}"

TOOLS = {"my_tool": my_tool}        # agent tool: my_tool(args)

def register_routes(app, **ctx):    # optional FastAPI routes
    @app.get("/my-plugin")
    def ping(): return {"ok": True}
```

See [`plugins/example_weather.py`](plugins/example_weather.py).

---

## Architecture

`pai.py` is a single file (~4,400 lines) with 23 clearly labelled sections:

```
SECTION  0  Bootstrap (self-installer, per-platform pip logic)
SECTION  1  Platform detection — DeviceProfile (chip/RAM/GPU/Metal/CUDA/ROCm/Vulkan)
SECTION  2  Model registry — ModelSpec, MLX/GGUF/remote ladders, mount scan
SECTION  3  Thermal governor — 5-stage state machine, predictive, cross-platform sensors
SECTION  4  Inference engine — MLX + GGUF swap + remote API dispatch (OpenAI/Anthropic)
SECTION  5  RAG pipeline — RagStore, DuckDuckGo DDGS
SECTION  6  Mesh network — UDP peer discovery, HTTP delegation
SECTION  7  Agent engine — ReAct loop, 10 tools, plugin dispatch, sandbox
SECTION  8  Code agent — plan → write → test → fix
SECTION  9  Thermal trainer — idle LoRA, Apple Silicon
SECTION 10  FastAPI backend — all endpoints
SECTION 11  Streamlit frontend — written to pai_frontend.py at launch
SECTION 12  One-time compilation — llama-cpp Metal/CUDA/ROCm/Vulkan detection
SECTION 14  Session cost tracker
SECTION 15  Plugin manager — auto-load from pai_data/plugins/
SECTION 16  MCP server — JSON-RPC 2.0 over stdio
SECTION 17  Benchmark runner
SECTION 18  Doctor check — 20+ system diagnostics
SECTION 19  Live terminal dashboard — Rich Live
SECTION 20  Auto-update — fetch, validate syntax, self-replace
SECTION 21  Demo mode — scripted 6-step showcase
SECTION 22  Download manager — retry/stall/integrity/cascade/HF scan
SECTION 23  Model catalogue table
SECTION 13  CLI entry point — main()
```

### Download resilience

```
ensure_with_fallback(primary_spec)
  ├─ DownloadManager.get(spec)
  │   ├─ Fast-path: verify_model() already valid → return
  │   ├─ Attempt 1–3 (resume_download=True each time)
  │   │   ├─ Download in background thread
  │   │   ├─ File-size poll every 2s → Rich progress bar
  │   │   ├─ Stall: no growth for max(300s, GB×90s) → abort + retry
  │   │   └─ Post-check: GGUF magic bytes + ±40% size · MLX shard scan
  │   └─ _try_alt_sources()
  │       ├─ Alt quants same repo  (Q4_K_M → Q3_K_M → Q2_K → IQ*)
  │       ├─ Alt orgs  (bartowski → QuantFactory → mradermacher → TheBloke)
  │       └─ HuggingFace live API search  (hf_scan_gguf / hf_scan_mlx)
  └─ Cascade: walk ladder to next-smaller model → retry whole chain
     Exhausted → RuntimeError with actionable message
```

---

## Developer guide

### Prerequisites

- Python 3.10+
- **macOS**: Xcode Command Line Tools + Homebrew  (`xcode-select --install && brew install cmake`)
- **Linux**: `sudo apt install cmake build-essential libopenblas-dev`  (or equivalent)
- **Windows**: Visual Studio Build Tools 2022 (C++ workload) + CMake 3.24+

### Setup

```bash
git clone https://github.com/miryala3/linus-pai.git
cd linus-pai
make setup          # creates .venv, installs deps, compiles llama-cpp
# or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python pai.py --install
```

### Common tasks

```bash
make run        # launch full system
make serve      # API only
make chat       # terminal chat
make doctor     # health check
make bench      # benchmark
make demo       # scripted demo
make stop       # stop running server
```

### Cleaning

```bash
make clean          # remove caches, pyc files, build artefacts
make clean-binary   # remove dist/ and PyInstaller work dirs only
make clean-cache    # remove RAG index, training buffers, logs (safe — auto-rebuild)
make clean-models   # remove downloaded model files (frees the most disk space)
make clean-data     # clean-cache + clean-models
make clean-runtime  # remove ~/.linus-pai (venv, logs, pai.py copy) — triggers full re-bootstrap
make clean-all      # everything above + .venv  (full developer reset)
```

> `PAI_HOME` defaults to `~/.linus-pai`. Override: `PAI_HOME=/other/path make clean-runtime`

### Testing

```bash
make test                          # full suite (no server needed)
make test-fast                     # skip slow tests
make coverage                      # HTML coverage report

# Directly
pytest test_pai.py -v
pytest test_pai.py -v -k "thermal"
pytest test_pai.py -v -k "moe or download"

# Live API tests (need running server)
PAI_API=http://localhost:9480 pytest test_pai.py -v -k "Live"
```

**16 test classes, ~110 tests** — each test has an inline CRITIQUE comment explaining the failure mode it guards against.

### Code quality

```bash
make lint         # ruff E/F/W (ignores E501 line-length)
make syntax       # Python AST + bash -n
make check        # syntax + lint combined
```

### Adding a model

Edit `_MLX_LADDER` or `_GGUF_LADDER` in **SECTION 2** of `pai.py`.

Rules:
1. Keep the ladder sorted by `size_gb` **descending** — a test enforces this.
2. Set `is_moe=True` and a real `active_params_b` for MoE models.
3. Use `bartowski/*` for GGUF; `mlx-community/*` for MLX.
4. Add the entry CHANGELOG.md under `[Unreleased]`.

```python
# 20 GB tier — hypothetical new model
ModelSpec("gguf-my-model-20b", "gguf",
          "bartowski/MyModel-20B-Instruct-GGUF",
          "MyModel-20B-Instruct-Q4_K_M.gguf", "", 12.0, 20.0, 32768,
          "MyOrg MyModel 20B Q4_K_M — description"),
```

### Adding a remote provider

```python
# In _REMOTE_LADDER (SECTION 2):
ModelSpec("myprovider-7b", "remote", "myprovider/model", "", "", 0.0, 7.0, 8192,
          "MyProvider 7B (remote — needs MY_KEY)",
          remote=True, api_base="https://api.myprovider.com/v1",
          api_key_env="MY_KEY", api_model_id="model-7b", api_provider="MyProvider"),
```

If the provider uses Anthropic-format requests (not OpenAI-compat), add a branch in `InferenceEngine._generate_remote()`.

### Writing a plugin

```python
# pai_data/plugins/my_plugin.py

def weather(location: str) -> str:
    """Agent can call weather(London) to get current conditions."""
    import urllib.request
    url = f"https://wttr.in/{location}?format=3"
    with urllib.request.urlopen(url, timeout=8) as r:
        return r.read().decode()

TOOLS = {"weather": weather}           # registers tool in agent

def register_routes(app, **ctx):       # optional extra API routes
    @app.get("/weather/{city}")
    def get_weather(city: str):
        return {"weather": weather(city)}
```

### Commit conventions

```
feat(models): add Llama 4 Maverick MoE to GGUF ladder
fix(thermal): correct millidegree conversion on Linux ARM
docs: update model catalogue
test(download): add stall detection regression test
chore: update requirements.txt
```

### PR checklist

- [ ] `make check` passes
- [ ] `make test` passes
- [ ] New public functions have a test with inline CRITIQUE comment
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] Single-file design of `pai.py` maintained

---

## Roadmap

**v1.1**
- Model quantisation on-device (FP16 → GGUF Q4 locally)
- Persistent chat history with full-text search
- Voice input/output (Whisper + TTS)

**v1.2**
- Multi-GPU pipeline across PCIe devices
- Vision model support (LLaVA, Qwen-VL)
- Automatic model update notifications

**v2.0**
- WASM build for in-browser inference
- iOS/Android companion app via mesh peer
- Federated LoRA training across mesh nodes

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [SECURITY.md](SECURITY.md)

Good first issues are labelled [`good first issue`](https://github.com/miryala3/linus-pai/labels/good%20first%20issue).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

Use freely in personal projects, research, and commercial products.
Keep the copyright notice in files you distribute.
See [NOTICE](NOTICE) for third-party attributions and model license notes.

---

## Acknowledgements

[MLX](https://github.com/ml-explore/mlx) ·
[llama.cpp](https://github.com/ggerganov/llama.cpp) ·
[FastAPI](https://fastapi.tiangolo.com) ·
[Streamlit](https://streamlit.io) ·
[Rich](https://github.com/Textualize/rich) ·
[HuggingFace Hub](https://huggingface.co) ·
[sentence-transformers](https://www.sbert.net) ·
[ddgs](https://github.com/deedy5/ddgs) ·
every researcher who published an open-weight model.
