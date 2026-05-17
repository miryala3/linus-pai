#!/usr/bin/env bash
# ==============================================================================
# install.sh — Linus PAI one-line installer
# Usage: curl -sSL https://raw.githubusercontent.com/miryala3/linus-pai/main/install.sh | bash
#
# Strategy (fastest path first):
#   1. Download the pre-built native binary from GitHub Releases (< 30 seconds)
#   2. Fall back to cloning the repo + source launcher if no binary is available
#
# Works on: macOS arm64/x86_64 · Linux x86_64/arm64/armv7 · Raspberry Pi
# No Python required for the binary path.
# ==============================================================================

set -euo pipefail

PAI_INSTALL_DIR="${HOME}/.local/share/linus-pai"
PAI_BIN_DIR="${HOME}/.local/bin"
REPO="miryala3/linus-pai"
REPO_URL="https://github.com/${REPO}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/main"
API_BASE="https://api.github.com/repos/${REPO}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'
info() { echo -e "${CYAN}[PAI]${RESET} $*"; }
ok()   { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[!!]${RESET}  $*"; }
err()  { echo -e "${RED}[ERR]${RESET} $*" >&2; exit 1; }

echo ""
echo -e "${BOLD}${CYAN}"
echo "  ██╗     ██╗███╗   ██╗██╗   ██╗███████╗    ██████╗  █████╗ ██╗"
echo "  ██║     ██║████╗  ██║██║   ██║██╔════╝    ██╔══██╗██╔══██╗██║"
echo "  ██║     ██║██╔██╗ ██║██║   ██║███████╗    ██████╔╝███████║██║"
echo "  ██║     ██║██║╚██╗██║██║   ██║╚════██║    ██╔═══╝ ██╔══██║██║"
echo "  ███████╗██║██║ ╚████║╚██████╔╝███████║    ██║     ██║  ██║██║"
echo "  ╚══════╝╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝    ╚═╝     ╚═╝  ╚═╝╚═╝"
echo -e "${RESET}  Private AI Runtime — installer"
echo ""

# ── Detect platform ────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
info "Platform: ${OS} ${ARCH}"

# Map to GitHub Release artifact name
case "${OS}:${ARCH}" in
  Darwin:arm64)   ARTIFACT="pai-macos-arm64";;
  Darwin:x86_64)  ARTIFACT="pai-macos-x86_64";;
  Linux:x86_64)   ARTIFACT="pai-linux-x86_64";;
  Linux:aarch64)  ARTIFACT="pai-linux-arm64";;
  Linux:armv7l)   ARTIFACT="";;   # no binary — use source launcher
  *)              ARTIFACT="";;
esac

mkdir -p "${PAI_INSTALL_DIR}" "${PAI_BIN_DIR}"

# ── Choose downloader ─────────────────────────────────────────────────────────
if command -v curl &>/dev/null; then
  _dl() { curl -fsSL "$1" -o "$2"; }
  _dl_progress() { curl -L --progress-bar "$1" -o "$2"; }
elif command -v wget &>/dev/null; then
  _dl() { wget -q "$1" -O "$2"; }
  _dl_progress() { wget --progress=bar:force "$1" -O "$2"; }
else
  err "Neither curl nor wget found. Install one and retry."
fi

# ── Path 1: Download pre-built binary ─────────────────────────────────────────
BINARY_INSTALLED=false

if [[ -n "${ARTIFACT}" ]]; then
  info "Checking GitHub Releases for a pre-built binary…"

  # Get latest release tag
  LATEST_TAG=$(_dl "${API_BASE}/releases/latest" /dev/stdout 2>/dev/null \
    | grep '"tag_name"' | head -1 | grep -o '"v[^"]*"' | tr -d '"') || LATEST_TAG=""

  if [[ -n "${LATEST_TAG}" ]]; then
    BINARY_URL="${REPO_URL}/releases/download/${LATEST_TAG}/${ARTIFACT}"
    SHA_URL="${BINARY_URL}.sha256"
    BINARY_PATH="${PAI_INSTALL_DIR}/${ARTIFACT}"

    info "Downloading pre-built binary: ${ARTIFACT} (${LATEST_TAG})"
    if _dl_progress "${BINARY_URL}" "${BINARY_PATH}" 2>/dev/null; then
      chmod +x "${BINARY_PATH}"

      # ── Verify SHA-256 checksum ──────────────────────────────────────────────
      SHA_FILE="${BINARY_PATH}.sha256"
      if _dl "${SHA_URL}" "${SHA_FILE}" 2>/dev/null; then
        EXPECTED_HASH=$(cut -d' ' -f1 < "${SHA_FILE}")
        if command -v sha256sum &>/dev/null; then
          ACTUAL_HASH=$(sha256sum "${BINARY_PATH}" | cut -d' ' -f1)
        else
          ACTUAL_HASH=$(shasum -a 256 "${BINARY_PATH}" | cut -d' ' -f1)
        fi

        if [[ "${EXPECTED_HASH}" == "${ACTUAL_HASH}" ]]; then
          ok "SHA-256 checksum verified."
        else
          warn "Checksum mismatch — binary may be corrupt."
          warn "Expected: ${EXPECTED_HASH}"
          warn "Got:      ${ACTUAL_HASH}"
          rm -f "${BINARY_PATH}"
          ARTIFACT=""   # fall through to source path
        fi
      else
        warn "Could not fetch checksum file — skipping verification."
      fi

      if [[ -x "${BINARY_PATH}" ]]; then
        # Install as 'pai' in bin dir
        ln -sf "${BINARY_PATH}" "${PAI_BIN_DIR}/pai"
        ok "Binary installed: ${PAI_BIN_DIR}/pai"
        BINARY_INSTALLED=true
      fi
    else
      warn "Binary download failed — falling back to source launcher."
    fi
  else
    warn "No GitHub Release found — falling back to source launcher."
  fi
fi

# ── Path 2: Source launcher (repo clone or file download) ─────────────────────
if ! $BINARY_INSTALLED; then
  info "Installing source launcher (requires no compilation)…"

  if command -v git &>/dev/null; then
    if [[ -d "${PAI_INSTALL_DIR}/.git" ]]; then
      info "Updating existing install…"
      git -C "${PAI_INSTALL_DIR}" pull --ff-only -q 2>/dev/null || true
    else
      info "Cloning repository…"
      git clone --depth 1 "${REPO_URL}.git" "${PAI_INSTALL_DIR}" -q 2>/dev/null || {
        info "Git clone failed — downloading files directly…"
        for f in pai pai.py runpai.sh stoppai.sh requirements.txt install.sh; do
          _dl "${RAW_BASE}/${f}" "${PAI_INSTALL_DIR}/${f}"
        done
      }
    fi
  else
    info "git not found — downloading files directly…"
    for f in pai pai.py runpai.sh stoppai.sh requirements.txt; do
      _dl "${RAW_BASE}/${f}" "${PAI_INSTALL_DIR}/${f}"
    done
  fi

  chmod +x "${PAI_INSTALL_DIR}/pai" \
            "${PAI_INSTALL_DIR}/runpai.sh" \
            "${PAI_INSTALL_DIR}/stoppai.sh" 2>/dev/null || true

  # The source 'pai' launcher handles Python bootstrap itself
  ln -sf "${PAI_INSTALL_DIR}/pai" "${PAI_BIN_DIR}/pai"
  ok "Source launcher installed: ${PAI_BIN_DIR}/pai"
fi

# ── Install convenience aliases ────────────────────────────────────────────────
cat > "${PAI_BIN_DIR}/pai-stop" << EOF
#!/usr/bin/env bash
exec bash "${PAI_INSTALL_DIR}/stoppai.sh"
EOF
chmod +x "${PAI_BIN_DIR}/pai-stop"

# ── Add ~/.local/bin to PATH if needed ────────────────────────────────────────
SHELL_RC="${HOME}/.bashrc"
[[ "$(basename "${SHELL:-bash}")" == "zsh" ]] && SHELL_RC="${HOME}/.zshrc"

if ! echo "${PATH}" | grep -q "${PAI_BIN_DIR}"; then
  if ! grep -q "${PAI_BIN_DIR}" "${SHELL_RC}" 2>/dev/null; then
    {
      echo ""
      echo "# Linus PAI"
      echo "export PATH=\"${PAI_BIN_DIR}:\$PATH\""
    } >> "${SHELL_RC}"
    info "Added ${PAI_BIN_DIR} to PATH in ${SHELL_RC}"
  fi
fi

# ── Desktop shortcuts ─────────────────────────────────────────────────────────
if [[ -f "${PAI_INSTALL_DIR}/scripts/install_desktop.sh" ]]; then
  bash "${PAI_INSTALL_DIR}/scripts/install_desktop.sh" 2>/dev/null || true
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
ok "Linus PAI installed."
echo ""
echo -e "  Reload your shell, then run:"
echo -e "  ${BOLD}pai${RESET}            — launch full system (API + web UI)"
echo -e "  ${BOLD}pai --chat${RESET}     — terminal chat"
echo -e "  ${BOLD}pai --doctor${RESET}   — system health check"
echo -e "  ${BOLD}pai --help${RESET}     — all options"
echo ""
echo -e "  Or without reloading: ${BOLD}${PAI_BIN_DIR}/pai${RESET}"
echo ""
