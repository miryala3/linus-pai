# Contributing to Linus PAI

Thank you for considering a contribution. This document covers everything you
need to go from zero to merged PR.

---

## Ways to contribute

| Type | Examples |
|---|---|
| Add a model | New entry in `_MLX_LADDER` or `_GGUF_LADDER` |
| Add a remote backend | New entry in `_REMOTE_LADDER` + API handler |
| Write a plugin | `.py` in `plugins/` with `TOOLS` dict |
| Write a sample program | New file in `samples/` |
| Fix a bug | With regression test |
| Improve a platform | Better thermal sensor reading, AMD AMDGPU_TARGETS |
| Improve docs | README, docstrings, CHANGELOG |
| Write tests | New test class with CRITIQUE comments |

---

## Philosophy

**Single file.** `pai.py` is intentionally one file. New features go in a
clearly-labelled `SECTION N`. Only create new files at the top level if
the feature cannot fit (shell scripts, Dockerfile, samples).

**No new mandatory deps.** Every import inside `pai.py` that is not stdlib
must be guarded so the file still *imports* and `--doctor` still runs even
if the package is absent. Only install deps inside the function that needs them.

**Test your change.** Every new public function needs at least one test in
`test_pai.py` with an inline `CRITIQUE:` comment explaining the failure mode
it guards against.

---

## Development setup

### Prerequisites

- Python 3.12 (CI runs 3.12 only; 3.10/3.11 not tested)
- **macOS**: `xcode-select --install && brew install cmake`
- **Linux**: `sudo apt install cmake build-essential libopenblas-dev`
- **Windows**: Visual Studio Build Tools 2022 (C++ workload), CMake 3.24+

### Clone and install

```bash
git clone https://github.com/miryala3/linus-pai.git
cd linus-pai
make setup       # creates .venv, installs deps, compiles llama-cpp
```

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest ruff            # dev-only
python pai.py --force-install      # compile llama-cpp for your GPU
```

> Use `--force-install` (not `--install`) — it bypasses the `.bootstrapped` marker so all packages are reinstalled even if the marker exists from a previous partial run.

### Run

```bash
make run         # full system (API + Streamlit UI)
make serve       # API only
make chat        # terminal chat
make doctor      # health check — run this first if something is wrong
```

### Cleaning

```bash
make clean          # remove caches, .pyc files, build artefacts
make clean-binary   # remove dist/ and PyInstaller work dirs only
make clean-cache    # remove RAG index, training buffers, logs (safe — auto-rebuild)
make clean-models   # remove downloaded model files (frees the most disk space)
make clean-data     # clean-cache + clean-models
make clean-runtime  # remove ~/.linus-pai/ (venv + logs) — triggers full re-bootstrap on next run
make clean-all      # everything above + dev .venv (full reset)
```

---

## Testing

```bash
make test                    # full suite — no server required
make test-fast               # skip @pytest.mark.slow tests
make coverage                # HTML report at htmlcov/index.html

# Directly
pytest test_pai.py -v
pytest test_pai.py -v -k "thermal or download"

# Live API tests (requires a running server)
PAI_API=http://localhost:9480 pytest test_pai.py -v -k "Live"
```

### Writing tests

1. Place tests in `test_pai.py` in the appropriate class.
2. Start the class docstring with `CRITIQUE:` explaining the failure mode.
3. Start each test method docstring with what specific failure it prevents.
4. Mock external dependencies; never require a running model for unit tests.

```python
class TestMyFeature(unittest.TestCase):
    """
    CRITIQUE: If X is broken, Y happens silently — these tests make that visible.
    """
    def test_happy_path(self):
        """Verifies the basic case works end-to-end."""
        ...

    def test_edge_case(self):
        """Empty input must return [] not raise KeyError."""
        ...
```

---

## Adding a model

### Local model (GGUF or MLX)

Edit `_MLX_LADDER` or `_GGUF_LADDER` in **SECTION 2** of `pai.py`.

**Ordering rule:** ladders must be sorted by `size_gb` **descending**.
A test in `TestModelSpec.test_ladders_ordered_by_size_descending` enforces this.

```python
# Example: 20 GB tier entry
ModelSpec("gguf-my-model-20b", "gguf",
          "bartowski/MyModel-20B-Instruct-GGUF",
          "MyModel-20B-Instruct-Q4_K_M.gguf", "", 12.0, 20.0, 32768,
          "MyOrg MyModel 20B Q4_K_M — short description",
          # For MoE models add:
          # is_moe=True, active_params_b=5.0,
          ),
```

Fields:
- `key` — unique identifier (used in API responses, adapter paths, test assertions)
- `repo_id` — HuggingFace `owner/repo`
- `filename` — exact filename for GGUF; empty string for MLX (snapshot)
- `check_file` — file that proves MLX download is complete (`config.json`, `tokenizer.json`)
- `size_gb` — approximate file size; determines RAM tier
- `params_b` — total parameters in billions
- `ctx` — default context window
- `description` — displayed in banner and `--list-models`

### Remote model

Add to `_REMOTE_LADDER` (also in **SECTION 2**):

```python
ModelSpec("myprovider-70b", "remote", "myprovider/model-70b", "", "", 0.0, 70.0, 32768,
          "MyProvider 70B (remote — needs MY_KEY)",
          remote=True,
          api_base="https://api.myprovider.com/v1",  # OpenAI-compat endpoint
          api_key_env="MY_KEY",                       # env var to check
          api_model_id="model-70b-instruct",          # ID sent to API
          api_provider="MyProvider"),                 # human label
```

If the provider does NOT use OpenAI-compatible requests, add a branch in
`InferenceEngine._generate_remote()` and `_stream_remote()`.

---

## Writing a plugin

Plugins live in `pai_data/plugins/` (created at runtime) or in `plugins/`
in the repo for bundled examples.

A plugin is a `.py` file with any of:

```python
# Optional: register agent tools
TOOLS = {
    "tool_name": callable,   # callable(args_str: str) -> str
}

# Optional: register FastAPI routes
def register_routes(app, engine, rag, mesh, thermal, sudo_spec, sudo_path,
                    sized_spec, sized_path, **kw):
    @app.get("/my-endpoint")
    def my_endpoint():
        return {"hello": "world"}
```

Rules:
- Tools must accept a single `str` argument and return `str`.
- `register_routes` receives the full context via `**ctx` so add any keyword args you need.
- Plugins must not import at module level anything that might fail silently.
- Add a test for any plugin you contribute to the repo.

---

## Writing a sample program

Place in `samples/`, update `samples/README.md`.

Requirements:
- Uses only `PAI_API` env var (default `http://localhost:9480`)
- Works fully offline (no external calls except when the user passes `--web`)
- Has a `--help` output and argparse
- Handles `PAI_API` unreachable gracefully (print error, exit 1)
- Has a docstring at the top describing usage examples

---

## Commit style (Conventional Commits)

```
feat(models): add Gemma 3 27B to GGUF and MLX ladders
fix(thermal): correct millidegree conversion on Linux ARM devices
docs: update model catalogue in README for v1.1
test(download): add stall detection regression test
chore: upgrade huggingface_hub to 0.24
perf(rag): cache sentence-transformer embeddings between queries
```

Types: `feat` `fix` `docs` `style` `refactor` `test` `chore` `perf` `ci` `build`

---

## PR checklist

- [ ] `make check` passes (syntax + ruff lint)
- [ ] `make test` passes with no new failures
- [ ] New public functions have a test with `CRITIQUE:` in the docstring
- [ ] Ladder ordering test still passes if you added a model
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] PR description explains *why*, not just *what*

---

## Release process (maintainers)

1. Move `[Unreleased]` items in `CHANGELOG.md` to a new `[X.Y.Z] — YYYY-MM-DD` section.
2. Update `PAI_VERSION = "X.Y.Z"` in `pai.py`.
3. Run `make check && make test`.
4. Commit: `chore: release vX.Y.Z`
5. `make release` — tags and pushes; GitHub Actions creates the release automatically.

---

## Getting help

- Open a [Discussion](https://github.com/miryala3/linus-pai/discussions) for questions.
- Open an [Issue](https://github.com/miryala3/linus-pai/issues) for bugs (use the template).
- Security issues: see [SECURITY.md](SECURITY.md).

---

## Building the native binary

The `pai` / `pai.cmd` files in the repo root are self-bootstrapping shell launchers
that work on any Unix/Windows system without any prior Python installation.
For distribution, CI can also produce a true compiled binary using PyInstaller.

### Launcher scripts (no compilation needed)

```bash
# macOS / Linux — the launcher is already executable
./pai --version
./pai --doctor

# Windows
pai.cmd --version
```

The launcher downloads `python-build-standalone` (~70 MB) on first run and
stores everything in `~/.linus-pai/`. Second run is instant.

### Native binary (PyInstaller)

```bash
# Build for current platform → dist/pai  (or dist/pai.exe on Windows)
make build

# macOS: build universal2 (arm64 + x86_64 fat binary — runs on both chips)
make build-universal

# Linux arm64 (cross-compile from x86_64 using QEMU)
# Not supported directly — use a native arm64 runner (e.g. GitHub ubuntu-24.04-arm)

# Windows
build\build.bat
```

The build runs in a dedicated `.build_venv/` so it does not affect your dev venv.
Output binary is `dist/pai` (~20–40 MB depending on platform and UPX compression).

### What is and is not bundled in the binary

| Included | Not included (installed on first run) |
|---|---|
| Python 3.12 interpreter | `mlx` / `mlx-lm` (Apple Silicon, ~150 MB) |
| `pai.py` (as a data file) | `llama-cpp-python` with GPU backend |
| Python stdlib | `fastapi`, `uvicorn`, `streamlit>=1.35` |
| `build/launcher.py` bootstrap logic | `sentence-transformers`, `torch` |

GPU-specific packages **must** be compiled for the local hardware at first launch.
This is unavoidable — a pre-compiled llama-cpp-python for Metal would not run on CUDA.

> **Important:** `sys.executable` inside a PyInstaller one-file binary is the frozen
> bundle itself, not a real Python interpreter. The launcher uses `_find_system_python()`
> to locate a real Python 3.10+ on PATH for `python -m venv`. If no system Python is
> found, the user is directed to install one from python.org before the binary can bootstrap.

### Verifying a downloaded binary

Every release binary ships with a `.sha256` checksum file:

```bash
# Download binary + checksum
curl -Lo pai https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64
curl -Lo pai.sha256 https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64.sha256

# Verify
sha256sum -c pai.sha256          # Linux
shasum -a 256 -c pai.sha256      # macOS
```

### Adding a new platform to CI

Edit `.github/workflows/release.yml` → `jobs.build.strategy.matrix`:

```yaml
- name: Linux-riscv64
  os: ubuntu-latest              # use QEMU emulation
  artifact: pai-linux-riscv64
  ext: ""
```

Then add the artifact to the `release` job's `files:` list.

### macOS code signing (maintainers)

Set `APPLE_DEVELOPER_ID` before running `bash build/build.sh`:

```bash
export APPLE_DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
bash build/build.sh
```

The build script runs `codesign` automatically when the env var is set.
For notarisation (required for Gatekeeper on macOS 12+), additionally run:

```bash
xcrun notarytool submit dist/pai \
  --apple-id miryalas@gmail.com \
  --team-id TEAMID \
  --password "@keychain:AC_PASSWORD" \
  --wait
xcrun stapler staple dist/pai
```
