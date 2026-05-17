# Changelog

All notable changes to Linus PAI are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed — Bootstrap reliability
- **PyInstaller venv creation** — `sys.executable` inside a one-file PyInstaller bundle is the frozen binary itself, not a real Python interpreter; `dist/pai -m venv` failed instantly and silently every time. Replaced with `_find_system_python()` which walks `python3.12 → 3.11 → 3.10 → 3.13 → python3` on PATH to find a genuine interpreter.
- **Bootstrap marker bypass** — launchers now call `pai.py --force-install` instead of `--install`; this bypasses the `.bootstrapped` marker file so a venv that was created but has missing packages (e.g. after adding `streamlit` to `_BASE_PKGS`) gets a proper reinstall rather than silently returning early.
- **KeyboardInterrupt crash** — Ctrl+C during bootstrap produced a `[PYI-xxx:ERROR]` PyInstaller crash log instead of a clean exit. Added `try/except KeyboardInterrupt` around all three bootstrap subprocess calls (venv create, pip upgrade, `pai.py --force-install`) and around the `_bootstrap()` call site in `main()`.
- **Bootstrap log spam** — `bootstrap.log` accumulated the full banner and all progress bytes (`\r` overwrites) on every invocation. Split into two logs: `install.log` (overwritten each bootstrap run — full install output) and `bootstrap.log` (append-only — timestamps and errors only).
- **Progress bar in logs** — `_progress()` now checks `sys.stdout.isatty()` before using `\r` carriage-return overwrite; piped/log output gets clean newline-separated lines instead of garbage `\r` bytes.

### Fixed — Package installation
- **`streamlit` missing from `_BASE_PKGS`** — streamlit was used as the UI server but never installed during bootstrap, causing `No module named streamlit` on every launch. Added `streamlit>=1.35` to `_BASE_PKGS`.
- **Venv health check gaps** — `_venv_ready()` (launcher.py) and `_venv_ok()` (shell `pai`) only checked `fastapi, uvicorn, rich` — missing streamlit meant a broken venv passed the health check. Both now include `streamlit` in the import test.

### Fixed — Intel Mac AMD GPU
- **GPU not detected** — Intel Mac x86_64 only queried `system_profiler SPHardwareDataType` (CPU info); the discrete AMD/Radeon GPU was invisible. Now also queries `SPDisplaysDataType` to read GPU name and VRAM (e.g. `Radeon Pro 555X, 4 GB`). Sets `device_plat = "metal"` when a discrete GPU is found.
- **No Metal compilation on Intel Mac** — llama-cpp-python was only compiled with `-DGGML_METAL=on` for `arm64`. AMD Radeon GPUs on Intel Mac fully support Metal. Bootstrap now compiles llama-cpp with Metal for all macOS builds, with a graceful CPU fallback if Metal compilation fails.

### Fixed — Frontend
- **JSONDecodeError on API failure** — all `response.json()` calls in `pai_frontend.py` crashed with `JSONDecodeError: Expecting value` when the API returned an empty or non-JSON body. Added `_rj(r)` helper that returns `{}` on any parse error and shows a clean error message.
- **Single-file RAG upload** — the file uploader only accepted one file at a time. Now uses `accept_multiple_files=True`; iterates all uploaded files, accumulates total chunk count, and shows per-file errors on failure.
- **`--status` / `--mesh` hangs** — these early-exit commands triggered model download and GPU backend compilation before printing output. Both now exit before `check_compile_once()` and model download.

### Added — Make targets
- `clean-binary` — removes `dist/`, `build/pai.build/`, `build/__pycache__/`; runs automatically before every `make build`
- `clean-cache` — removes RAG index, training buffers, audit logs (safe — auto-rebuild on next run)
- `clean-models` — removes downloaded model files and adapters (frees the most disk space)
- `clean-data` — `clean-cache` + `clean-models`
- `clean-runtime` — removes `PAI_HOME` (`~/.linus-pai/`): venv, logs, stable `pai.py` copy; triggers a full re-bootstrap on next run
- `clean-all` now includes `clean-runtime`; `PAI_HOME ?= $(HOME)/.linus-pai` overridable

### Added — Self-contained binary
- `pai` — Unix self-bootstrapping launcher (macOS + Linux); no Python install required
  - Detects platform/arch → downloads `python-build-standalone` if no Python 3.10+ found
  - Stores embedded Python + venv in `~/.linus-pai/`; first-run bootstrap ~5–15 min, then instant
  - Symlink-aware: resolves symlinks in `~/.local/bin/` so `pai.py` is always found next to the real script
  - Override: `PAI_HOME`, `PAI_PYTHON` environment variables
- `pai.cmd` — Windows equivalent; uses PowerShell to download embedded Python
- `build/launcher.py` — minimal PyInstaller entry point for compiling native binaries
- `build/pai.spec` — PyInstaller spec (one-file, `optimize=2`, macOS `universal2` support)
- `build/build.sh` / `build/build.bat` — native binary build scripts; `make build`; clean all old binaries before each build; `--clean-data` flag for runtime cache cleanup
- `build/entitlements.plist` — macOS Gatekeeper entitlements (JIT, Metal, network)
- Updated `.github/workflows/release.yml` — matrix build for macOS arm64/x86_64 · Linux x86_64/arm64 · Windows x86_64; SHA-256 checksums attached to every release
- Updated `.github/workflows/test.yml` — gate chain lint → syntax → test (Python 3.12 only); `shell: bash` on all steps
- Updated `install.sh` — tries pre-built binary first (with checksum verification), falls back to source launcher
- Updated `scripts/install_desktop.sh` — uses `pai` binary for shortcuts when present
- Binary verification docs in `SECURITY.md` (checksum, Gatekeeper, supply-chain)
- Binary build docs in `CONTRIBUTING.md` (build guide, cross-platform matrix, code signing, notarisation)

---

## [1.0.0] — 2025-05-16

### Added — Core runtime
- Single-file runtime `pai.py` (4 400+ lines) covering all features
- Platform auto-detection: Apple Silicon (MLX) · NVIDIA CUDA · AMD ROCm · AMD/Intel Vulkan · CPU
- 5-stage thermal governor with 60-second predictive extrapolation and hardware hysteresis
- Mesh network: UDP peer discovery, exo-style HTTP pipeline sharding, `best_peer_for_large()`

### Added — Model support
- **MLX ladder** (Apple Silicon): 18 models from 0.8 GB → 61 GB
- **GGUF ladder** (CUDA/ROCm/Vulkan/CPU): 24 models from 0.8 GB → 69 GB
- **Remote ladder**: 14 endpoints — OpenAI, Anthropic, Groq (free), Together AI, Ollama
- **~120B class**: Mistral Large 2 (123B), Meta Llama 4 Scout MoE (109B/17B active), Cohere Command R+ (104B)
- **~20B class**: Google Gemma 3 27B, Mistral Small 3.1 22B, QwQ-32B reasoning, DeepSeek R1 distils
- Mixture-of-Experts (MoE) models: Llama 4 Scout · Mixtral 8×22B · Mixtral 8×7B · DeepSeek-V2 · Qwen1.5 MoE
- GPT-family OSS: Microsoft Phi-4 · OpenHermes 2.5 · WizardLM-2 · GPT4All-J (true OpenAI-arch OSS)
- Remote auto-detection: checks `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`, Ollama

### Added — Download manager
- Retry with exponential backoff (3 attempts, 15s/60s/240s delays)
- Stall detection: background file-size monitor, aborts if no growth for `max(300, GB×90)s`
- Alternate source resolution: alt quants (Q6_K→Q5_K_M→Q4_K_M→Q3_K_M→Q2_K) in same repo
- Alternate provider orgs: bartowski → QuantFactory → mradermacher → TheBloke → unsloth
- HuggingFace live model scan (`hf_scan_gguf`, `hf_scan_mlx`) as last resort
- Cascade fallback: on total failure, walks down ladder to next-smaller model that fits RAM
- Integrity check: GGUF magic-byte validation + ±40% size ratio; MLX check_file + shard scan
- `GET /download/status` — live progress for polling UIs
- `GET /download/verify/{key}` — on-demand integrity check
- `POST /download/scan` — HuggingFace live search endpoint

### Added — Inference
- MLX native Metal inference (Apple Silicon)
- llama-cpp-python with Metal / CUDA / ROCm (HIPBlas) / Vulkan backends
- One-time compilation detection: auto-recompiles if backend mismatch detected
- Remote inference: OpenAI-compat (OpenAI · Groq · Together · Ollama) + Anthropic Messages API
- Streaming: SSE for local models and all OpenAI-compat remotes

### Added — OpenAI-compatible API
- `GET  /v1/models` — model list
- `POST /v1/chat/completions` — streaming + batch, maps to local or remote backend
- `POST /v1/completions` — legacy completions endpoint
- `GET  /v1/usage` — session cost stats vs GPT-4o pricing
- `GET  /v1/benchmark` — async benchmark trigger

### Added — RAG
- Document ingestion: PDF, TXT, MD
- Chunking with configurable overlap sliding window
- Sentence-transformers cosine similarity (keyword BM25 fallback on edge devices)
- DuckDuckGo web search integration (DDGS) fused into every query

### Added — Agents
- ReAct loop (think → act → observe → answer) with 10 built-in tools
- Tools: `search`, `python`, `shell`, `read_file`, `write_file`, `fetch`, `recall`, `remember`, `delegate`, `done`
- Code agent: plan → write → test → fix cycle in sandboxed subprocess
- Python sandbox: subprocess isolation, 30s timeout, output truncation (injection protection)
- Compound shell command blocking (`;`, `&&`, `||`)
- Agent memory: persistent key-value store across steps

### Added — MCP server
- JSON-RPC 2.0 over stdio (Claude Code / Cursor integration)
- Tools: `aio_generate`, `aio_search_web`, `aio_rag_query`, `aio_run_agent`, `aio_status`

### Added — Training
- Idle-time LoRA fine-tuning (Apple Silicon, `mlx_lm.train`)
- Thermal gating: only trains when temp < 75°C and idle > 20 min
- Interaction accumulation: every query feeds the training buffer
- `POST /train/trigger` — manual trigger
- `python pai.py --train` — CLI trigger

### Added — Dashboard & UX
- Live terminal dashboard (`--dashboard`): Rich Live panel, thermal sparkline, RAM/CPU bars
- Benchmark runner (`--benchmark`): 5-prompt suite, shareable ASCII card
- Doctor (`--doctor`): 20+ system checks including model integrity
- Demo mode (`--demo`): scripted 6-step showcase
- Auto-update (`--update`): fetches pai.py from GitHub, validates Python syntax, self-replaces
- Session cost tracker: counts tokens, shows $ saved vs GPT-4o on every exit
- `--list-models`: full Rich table of all 56 models with RAM requirements

### Added — Plugin system
- Drop `.py` in `pai_data/plugins/` to auto-load
- Plugins register: `TOOLS` dict (agent tools), `register_routes(app, **ctx)` (FastAPI routes)
- Example plugin: `plugins/example_weather.py`

### Added — Infrastructure
- `runpai.sh` / `runpai.bat`: full prerequisite installer + launcher with browser open
- `stoppai.sh` / `stoppai.bat`: PID-file + pkill + port cleanup
- `install.sh`: one-line `curl | bash` installer
- Desktop shortcuts: macOS `.command`, Linux `.desktop`, Windows `.lnk`
- `Dockerfile`: multi-stage CPU/CUDA/ROCm/Vulkan builds
- `.devcontainer/devcontainer.json`: one-click GitHub Codespaces
- `.github/workflows/test.yml`: CI across Python 3.10/3.11/3.12 × macOS/Ubuntu/Windows
- `Makefile`: `make setup test lint run bench doctor`

### Added — Sample programs (11 files in `samples/`)
- `chat.py` — multi-turn terminal chat with personas, file context, web search
- `code_review.py` — AI code review with severity ratings, git diff, watch mode
- `test_gen.py` — automated pytest suite generator
- `bug_fixer.py` — error-driven bug fixer, run→fix→run loop
- `doc_writer.py` — docstring writer, README and API reference generator
- `refactor.py` — targeted refactoring with 10 built-in goals, diff preview
- `commit_writer.py` — Conventional Commits generator, git hook installer
- `data_analyst.py` — NL→Pandas: CSV/JSON/SQLite analysis, auto-report
- `sql_helper.py` — NL→SQL for SQLite/PostgreSQL/MySQL
- `web_scraper.py` — NL-guided scraper code generator and runner
- `agentic_task.py` — autonomous multi-step task runner, pipeline mode

---

[1.0.0]: https://github.com/miryala3/linus-pai/releases/tag/v1.0.0
