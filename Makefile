# Linus PAI — Developer Makefile
# ─────────────────────────────────────────────────────────
# make setup       — create venv and install all deps
# make test        — run full test suite
# make lint        — ruff + syntax check
# make run         — launch PAI (API + UI)
# make doctor      — health check
# make bench       — run benchmark
# make demo        — run the demo sequence
# make clean       — remove generated files / caches
# make release     — tag and push a release

PYTHON   ?= python3
VENV     := .venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip

.PHONY: all setup install test lint check run serve chat doctor bench demo \
        build build-universal binary clean clean-all release help

all: help

# ── Environment ──────────────────────────────────────────

$(VENV):
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip wheel setuptools -q

setup: $(VENV)
	$(PIP) install -r requirements.txt -q
	$(PY) pai.py --install
	@echo "✓ Setup complete.  Run 'make run' to start."

install: setup   ## alias

# ── Running ───────────────────────────────────────────────

run: $(VENV)     ## Launch PAI (API on :9480 + UI on :8501)
	$(PY) pai.py

serve: $(VENV)   ## API only (no Streamlit)
	$(PY) pai.py --serve

chat: $(VENV)    ## Interactive terminal chat
	$(PY) pai.py --chat

demo: $(VENV)    ## Run the scripted demo
	$(PY) pai.py --demo

doctor: $(VENV)  ## System health check
	$(PY) pai.py --doctor

bench: $(VENV)   ## Run benchmark and save results
	$(PY) pai.py --benchmark --bench-out bench_result.json

models: $(VENV)  ## List all available models
	$(PY) pai.py --list-models

status: $(VENV)  ## Print device/thermal/model status
	$(PY) pai.py --status

stop:            ## Stop a running PAI server
	@bash stoppai.sh

# ── Testing ───────────────────────────────────────────────

test: $(VENV)    ## Run full test suite
	$(VENV)/bin/pytest test_pai.py -v --tb=short

test-fast: $(VENV)  ## Run tests, skip slow ones
	$(VENV)/bin/pytest test_pai.py -v --tb=short -m "not slow"

test-live: $(VENV)  ## Run live API tests (requires running server)
	PAI_API=http://localhost:9480 $(VENV)/bin/pytest test_pai.py -v -k "Live"

coverage: $(VENV)
	$(VENV)/bin/pytest test_pai.py --cov=pai --cov-report=html
	@echo "Coverage report → htmlcov/index.html"

# ── Quality ───────────────────────────────────────────────

lint: $(VENV)    ## Run ruff linter
	$(VENV)/bin/ruff check pai.py test_pai.py samples/ --select E,F,W --ignore E501 || true

check: syntax lint  ## Full quality check

syntax:          ## Verify Python syntax of all files
	@$(PYTHON) -c "import ast; ast.parse(open('pai.py').read()); print('pai.py OK')"
	@$(PYTHON) -c "import ast; ast.parse(open('test_pai.py').read()); print('test_pai.py OK')"
	@for f in samples/*.py; do $(PYTHON) -c "import ast; ast.parse(open('$$f').read())"; echo "$$f OK"; done
	@bash -n runpai.sh && echo "runpai.sh OK"
	@bash -n stoppai.sh && echo "stoppai.sh OK"

# ── Cleaning ─────────────────────────────────────────────

# ── Binary ───────────────────────────────────────────────

build: $(VENV)   ## Build native binary: dist/pai (macOS/Linux) or dist/pai.exe (Windows)
	bash build/build.sh

build-universal: $(VENV)  ## Build macOS universal2 binary (arm64 + x86_64)
	bash build/build.sh --universal

binary: build    ## Alias for build

clean:           ## Remove generated files and caches
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "*.bak.py" -delete 2>/dev/null || true
	find . -name "pai_frontend.py" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ .pytest_cache/ bench_result.json
	@echo "✓ Clean"

clean-all: clean ## Also remove venv (force reinstall)
	rm -rf $(VENV) pai_data/ .compiled_backend .bootstrapped
	@echo "✓ Full clean (re-run 'make setup' to reinstall)"

# ── Release ──────────────────────────────────────────────

VERSION := $(shell grep 'PAI_VERSION' pai.py | head -1 | grep -oP '"\K[^"]+')

release:         ## Tag a release (set VERSION= to override)
	@echo "Releasing v$(VERSION)…"
	@git diff --quiet || (echo "[!] Uncommitted changes" && exit 1)
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push && git push --tags
	@echo "✓ Tagged v$(VERSION)"

# ── Help ─────────────────────────────────────────────────

help:
	@echo ""
	@echo "  Linus PAI — Developer targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'
	@echo ""
