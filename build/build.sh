#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# build/build.sh — Build the Linus PAI native binary
#
# Creates a single self-contained executable: dist/pai
# That binary bundles a Python interpreter and bootstraps all deps on first run.
#
# Usage:
#   bash build/build.sh              # build for current platform
#   bash build/build.sh --universal  # macOS universal2 (arm64 + x86_64)
#   bash build/build.sh --upx        # compress with UPX (smaller binary)
#   bash build/build.sh --clean      # remove build artefacts only
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

OS="$(uname -s)"; ARCH="$(uname -m)"
UNIVERSAL=false; USE_UPX=false; CLEAN_ONLY=false; CLEAN_DATA=false

for arg in "$@"; do
  case "$arg" in
    --universal)   UNIVERSAL=true;;
    --upx)         USE_UPX=true;;
    --clean)       CLEAN_ONLY=true;;
    --clean-data)  CLEAN_DATA=true;;
  esac
done

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
info() { printf "${CYAN}[BUILD]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[OK]${RESET}   %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[ERR]${RESET}  %s\n" "$*" >&2; exit 1; }

# ── Clean binaries (always runs before every build) ───────────────────────────
info "Cleaning previous build artefacts…"

_clean_item() {
  local label="$1"; shift
  rm -rf "$@" 2>/dev/null || true
  printf "  ${GREEN}✓${RESET} %-28s removed\n" "${label}"
}

_clean_item "dist/"              dist/
_clean_item "build/pai.build/"   build/pai.build/
_clean_item "build/__pycache__/" build/__pycache__/
find . -name "*.pyc" -path "*/build/*" -delete 2>/dev/null || true
find . -name "*.spec.bak"        -delete 2>/dev/null || true
# Also remove any platform-named binaries left from CI or previous runs
find "${ROOT}/dist" -maxdepth 1 \
  -name "pai-*" -o -name "*.sha256" 2>/dev/null | xargs rm -f 2>/dev/null || true

# ── Optional: clean stale runtime data ────────────────────────────────────────
if $CLEAN_DATA; then
  info "Cleaning stale runtime data (--clean-data)…"
  _clean_item "pai_data/rag/"          pai_data/rag/
  _clean_item "pai_data/train_buffer/" pai_data/train_buffer/
  _clean_item "pai_data/audit/"        pai_data/audit/
  _clean_item "download_state.json"    pai_data/download_state.json
  _clean_item "query.log"              pai_data/query.log
  info "Models and adapters retained.  Use 'make clean-models' to remove them."
fi

ok "Clean complete."

if $CLEAN_ONLY; then
  exit 0
fi

# ── Find Python 3.10+ ─────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo False)
    if [[ "$ver" == "True" ]]; then
      PYTHON="$candidate"
      break
    fi
  fi
done
[[ -z "$PYTHON" ]] && err "Python 3.10+ not found. Run: bash runpai.sh --install"
info "Using Python: $PYTHON ($($PYTHON --version))"

# ── Ensure build venv ─────────────────────────────────────────────────────────
BUILD_VENV="${ROOT}/.build_venv"
if [[ ! -x "${BUILD_VENV}/bin/python3" ]]; then
  info "Creating build venv…"
  "$PYTHON" -m venv "${BUILD_VENV}"
fi
PY="${BUILD_VENV}/bin/python3"
PIP="${BUILD_VENV}/bin/pip"

"$PIP" install --upgrade pip -q

# ── Install PyInstaller ───────────────────────────────────────────────────────
info "Installing PyInstaller…"
"$PIP" install pyinstaller -q

# ── Install UPX compressor (optional) ─────────────────────────────────────────
if $USE_UPX; then
  if ! command -v upx &>/dev/null; then
    if command -v brew &>/dev/null; then
      info "Installing UPX via Homebrew…"
      brew install upx -q
    elif command -v apt-get &>/dev/null; then
      sudo apt-get install -y upx-ucl -q
    else
      info "UPX not found — binary will not be compressed."
      USE_UPX=false
    fi
  fi
fi

# ── Build ─────────────────────────────────────────────────────────────────────
info "Building dist/pai…"
info "Platform: ${OS} ${ARCH}"

PYINSTALLER_ARGS=(
  "build/pai.spec"
  "--distpath" "dist"
  "--workpath" "build/pai.build"
  "--noconfirm"
  "--log-level" "WARN"
)

if $UNIVERSAL && [[ "$OS" == "Darwin" ]]; then
  info "Building universal2 (arm64 + x86_64) binary…"
  PYINSTALLER_ARGS+=("--target-arch" "universal2")
fi

# UPX is controlled inside pai.spec (upx=True/False) not via CLI flag when using a .spec file

"${BUILD_VENV}/bin/pyinstaller" "${PYINSTALLER_ARGS[@]}"

# ── Verify ────────────────────────────────────────────────────────────────────
BINARY="dist/pai"
if [[ ! -f "${BINARY}" ]]; then
  err "Build failed — ${BINARY} not found."
fi

chmod +x "${BINARY}"
SIZE=$(du -sh "${BINARY}" | cut -f1)
ok "Built: ${BINARY}  (${SIZE})"

# ── Quick smoke test ──────────────────────────────────────────────────────────
info "Smoke test…"
output=$("${BINARY}" --version 2>&1) || true
if echo "$output" | grep -q "Linus PAI"; then
  ok "Smoke test passed: ${output}"
else
  printf "${YELLOW}[WARN]${RESET}  Smoke test output: %s\n" "${output:-<empty>}"
fi

echo ""
ok "Binary ready: ${ROOT}/dist/pai"
echo ""
echo "  Run:       ./dist/pai"
echo "  Install:   sudo cp dist/pai /usr/local/bin/pai && pai --version"
echo "  Share:     Upload dist/pai to GitHub Releases"
echo ""

# ── macOS code signing (optional) ─────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]] && command -v codesign &>/dev/null; then
  if [[ -n "${APPLE_DEVELOPER_ID:-}" ]]; then
    info "Code signing with ${APPLE_DEVELOPER_ID}…"
    codesign --force --sign "${APPLE_DEVELOPER_ID}" \
             --options runtime \
             --entitlements build/entitlements.plist \
             "${BINARY}" && ok "Signed."
  else
    info "Tip: Set APPLE_DEVELOPER_ID to enable code signing for Gatekeeper."
  fi
fi
