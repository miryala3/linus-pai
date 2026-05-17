#!/usr/bin/env bash
# ==============================================================================
# runpai.sh — AIO Linus PAI — Private AI Runtime Launcher
# Installs all system prerequisites, sets up venv, downloads models,
# and starts the backend API + Streamlit UI.
#
# Usage
#   bash runpai.sh              # full auto (API + UI)
#   bash runpai.sh --chat       # terminal chat only
#   bash runpai.sh --serve      # API only, no Streamlit
#   bash runpai.sh --agent "…"  # one-shot agent task
#   bash runpai.sh --code  "…"  # one-shot code-agent task
#   bash runpai.sh --status     # print device / model status
#   bash runpai.sh --install    # install deps only (no server)
#   bash runpai.sh --port  N    # override API port  (default 9480)
#   bash runpai.sh --ui-port N  # override UI  port  (default 8501)
#   bash runpai.sh --force-install  # re-run bootstrap
# ==============================================================================

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
AIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIO_VENV="${AIO_DIR}/.venv"
AIO_PID_DIR="${HOME}/.aio"
AIO_PID_FILE="${AIO_PID_DIR}/aio.pids"
AIO_LOG_DIR="${AIO_PID_DIR}/logs"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10
OPEN_BROWSER=true

mkdir -p "${AIO_PID_DIR}" "${AIO_LOG_DIR}"

# ── Color helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[PAI]${RESET} $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()     { echo -e "${RED}[ERR]${RESET} $*" >&2; }
die()     { err "$*"; exit 1; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
echo "  ██╗     ██████╗  █████╗ ██╗ "
echo " ██╔══██╗██║██╔═══██╗"
echo " ███████║██║██║   ██║"
echo " ██╔══██║██║██║   ██║"
echo " ██║  ██║██║╚██████╔╝"
echo " ╚═╝  ╚═╝╚═╝ ╚═════╝  Linus PAI — Private AI Runtime"
echo -e "${RESET}"

# ── Already running? ──────────────────────────────────────────────────────────
if [[ -f "${AIO_PID_FILE}" ]]; then
    still_running=false
    while IFS= read -r pid; do
        if kill -0 "${pid}" 2>/dev/null; then
            still_running=true
            break
        fi
    done < "${AIO_PID_FILE}"
    if $still_running; then
        warn "PAI already running (PIDs: $(tr '\n' ' ' < "${AIO_PID_FILE}"))"
        warn "Run stoppai.sh first, or use --force-install to restart."
        # Still open browser if it exists
        API_PORT=9480
        UI_PORT=8501
        for i in "$@"; do
            [[ "$i" == "--port" ]] && shift && API_PORT="$1"
            [[ "$i" == "--ui-port" ]] && shift && UI_PORT="$1"
        done
        info "UI  → http://localhost:${UI_PORT}"
        info "API → http://localhost:${API_PORT}/docs"
        open "http://localhost:${UI_PORT}" 2>/dev/null || \
            xdg-open "http://localhost:${UI_PORT}" 2>/dev/null || true
        exit 0
    fi
fi

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

# ── Install system prerequisites ──────────────────────────────────────────────
install_prereqs() {
    info "Checking system prerequisites…"

    case "${OS}" in
    Darwin)
        # --- macOS: use Homebrew ---
        if ! command -v brew &>/dev/null; then
            warn "Homebrew not found. Installing…"
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        ok "Homebrew present"

        # Xcode CLI tools (needed for llama-cpp-python compilation)
        if ! xcode-select -p &>/dev/null; then
            info "Installing Xcode Command Line Tools…"
            xcode-select --install 2>/dev/null || true
            warn "Xcode tools installing — re-run this script once done."
            exit 1
        fi
        ok "Xcode CLI tools present"

        # Python
        if ! command -v python3 &>/dev/null || \
           ! python3 -c "import sys; assert sys.version_info >= (${PYTHON_MIN_MAJOR},${PYTHON_MIN_MINOR})" 2>/dev/null; then
            info "Installing Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ via Homebrew…"
            brew install "python@3.12" || brew upgrade "python@3.12" || true
        fi
        ok "Python OK"

        # CMake (needed for llama-cpp-python)
        if ! command -v cmake &>/dev/null; then
            info "Installing cmake…"
            brew install cmake
        fi
        ok "cmake OK"

        # Git (usually present)
        if ! command -v git &>/dev/null; then
            brew install git
        fi
        ;;

    Linux)
        # Detect distro
        if command -v apt-get &>/dev/null; then
            info "Detected Debian/Ubuntu — updating packages…"
            sudo apt-get update -qq
            sudo apt-get install -y python3 python3-pip python3-venv \
                cmake build-essential git curl wget libopenblas-dev \
                libssl-dev pkg-config 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            info "Detected Fedora/RHEL…"
            sudo dnf install -y python3 python3-pip cmake gcc gcc-c++ \
                git curl make openssl-devel 2>/dev/null || true
        elif command -v pacman &>/dev/null; then
            info "Detected Arch Linux…"
            sudo pacman -Sy --noconfirm python python-pip cmake gcc git curl 2>/dev/null || true
        else
            warn "Unknown Linux distro — assuming prerequisites are installed."
        fi

        # Check for CUDA
        if command -v nvcc &>/dev/null; then
            ok "CUDA detected: $(nvcc --version | head -1)"
        elif command -v nvidia-smi &>/dev/null; then
            ok "NVIDIA GPU detected (no nvcc; CUDA runtime only)"
        fi
        ;;

    MINGW*|CYGWIN*|MSYS*)
        warn "Running on Windows via MSYS/Git Bash — prefer runaio.bat instead."
        ;;

    *)
        warn "Unknown OS: ${OS} — proceeding anyway."
        ;;
    esac
}

install_prereqs

# ── Find Python ───────────────────────────────────────────────────────────────
find_python() {
    for candidate in python3.12 python3.11 python3.10 python3 python; do
        if command -v "${candidate}" &>/dev/null; then
            version=$("${candidate}" -c "import sys; print(sys.version_info.major, sys.version_info.minor)")
            major=$(echo "${version}" | cut -d' ' -f1)
            minor=$(echo "${version}" | cut -d' ' -f2)
            if [[ "${major}" -ge "${PYTHON_MIN_MAJOR}" && "${minor}" -ge "${PYTHON_MIN_MINOR}" ]]; then
                echo "${candidate}"
                return 0
            fi
        fi
    done
    # Homebrew path fallback
    for hbpath in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
                  /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
        if [[ -x "${hbpath}" ]]; then
            echo "${hbpath}"
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_python)" || die "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ not found after installation."
ok "Using Python: ${PYTHON} ($(${PYTHON} --version))"

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d "${AIO_VENV}" ]]; then
    info "Creating virtual environment…"
    "${PYTHON}" -m venv "${AIO_VENV}"
fi

# Activate
# shellcheck source=/dev/null
source "${AIO_VENV}/bin/activate"
ok "venv active: ${AIO_VENV}"

# Upgrade pip + wheel silently
pip install --upgrade pip wheel setuptools -q

# ── Install AIO Python dependencies ───────────────────────────────────────────
info "Installing AIO dependencies (first run compiles llama-cpp-python with Metal/CUDA)…"
info "This may take 5–15 minutes on first run."

# Pass --force-install if requested
FORCE_ARG=""
for arg in "$@"; do
    [[ "${arg}" == "--force-install" ]] && FORCE_ARG="--force-install"
done

python "${AIO_DIR}/pai.py" --install ${FORCE_ARG} 2>&1 | tee -a "${AIO_LOG_DIR}/install.log"
ok "Dependencies installed."

# ── Parse launch options ──────────────────────────────────────────────────────
LAUNCH_ARGS=()
for arg in "$@"; do
    case "${arg}" in
        --force-install) ;;   # consumed above, skip
        *) LAUNCH_ARGS+=("${arg}") ;;
    esac
done

# Determine ports for browser opening
API_PORT=9480
UI_PORT=8501
_next_is_port=false
_next_is_ui=false
for arg in "${LAUNCH_ARGS[@]}"; do
    if $_next_is_port; then API_PORT="${arg}"; _next_is_port=false; continue; fi
    if $_next_is_ui;   then UI_PORT="${arg}";  _next_is_ui=false;   continue; fi
    [[ "${arg}" == "--port" ]]    && _next_is_port=true
    [[ "${arg}" == "--ui-port" ]] && _next_is_ui=true
done

# Modes that don't run a server (pass-through and exit)
for arg in "${LAUNCH_ARGS[@]}"; do
    case "${arg}" in
        --install|--status|--train|--mesh|--agent|--code)
            info "Running: python pai.py ${LAUNCH_ARGS[*]}"
            python "${AIO_DIR}/pai.py" "${LAUNCH_ARGS[@]}"
            exit $?
            ;;
        --chat)
            python "${AIO_DIR}/pai.py" --chat "${LAUNCH_ARGS[@]}"
            exit $?
            ;;
    esac
done

# ── Launch server in background ───────────────────────────────────────────────
LOG_FILE="${AIO_LOG_DIR}/aio_$(date +%Y%m%d_%H%M%S).log"
info "Launching AIO server…"
info "Logs → ${LOG_FILE}"

nohup python "${AIO_DIR}/pai.py" "${LAUNCH_ARGS[@]}" \
    >> "${LOG_FILE}" 2>&1 &
SERVER_PID=$!

info "Server PID: ${SERVER_PID}"

# Wait for API to be ready (up to 60s)
info "Waiting for API to come up on port ${API_PORT}…"
for i in $(seq 1 60); do
    if curl -sf "http://localhost:${API_PORT}/status" >/dev/null 2>&1; then
        ok "API ready!"
        break
    fi
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        err "Server process died. Check ${LOG_FILE}"
        tail -30 "${LOG_FILE}" >&2
        exit 1
    fi
    sleep 1
    if [[ "${i}" == "60" ]]; then
        warn "API not ready after 60s — check ${LOG_FILE}"
    fi
done

# ── Open browser ──────────────────────────────────────────────────────────────
info "UI  → http://localhost:${UI_PORT}"
info "API → http://localhost:${API_PORT}/docs"

# Brief wait for Streamlit to boot
sleep 3
if [[ "${OS}" == "Darwin" ]]; then
    open "http://localhost:${UI_PORT}" 2>/dev/null || true
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:${UI_PORT}" 2>/dev/null || true
elif command -v sensible-browser &>/dev/null; then
    sensible-browser "http://localhost:${UI_PORT}" 2>/dev/null || true
fi

echo ""
ok "PAI is running.  Run ${BOLD}bash stoppai.sh${RESET} to stop."
echo ""
