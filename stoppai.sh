#!/usr/bin/env bash
# ==============================================================================
# stopaio.sh — Stop the PAI All-In-One Local AI Runtime
# Reads ~/.aio/aio.pids and kills all recorded processes cleanly.
# Fallback: pkill on process names.
# ==============================================================================

PAI_PID_FILE="${HOME}/.aio/aio.pids"
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'

echo -e "${BOLD}PAI — Stopping All Services${RESET}"

killed=0

# ── Kill by PID file ──────────────────────────────────────────────────────────
if [[ -f "${PAI_PID_FILE}" ]]; then
    while IFS= read -r pid; do
        pid="${pid//[[:space:]]/}"
        [[ -z "${pid}" ]] && continue
        if kill -0 "${pid}" 2>/dev/null; then
            kill -TERM "${pid}" 2>/dev/null && \
                echo -e "${GREEN}[OK]${RESET}  Sent SIGTERM to PID ${pid}" || true
            # Give it 3s then SIGKILL
            sleep 3
            if kill -0 "${pid}" 2>/dev/null; then
                kill -KILL "${pid}" 2>/dev/null && \
                    echo -e "${YELLOW}[WARN]${RESET} SIGKILL sent to PID ${pid}" || true
            fi
            killed=$((killed + 1))
        else
            echo -e "${YELLOW}[WARN]${RESET} PID ${pid} not running"
        fi
    done < "${PAI_PID_FILE}"
    rm -f "${PAI_PID_FILE}"
else
    echo -e "${YELLOW}[WARN]${RESET} No PID file found at ${PAI_PID_FILE}"
fi

# ── Fallback: pkill by process name ───────────────────────────────────────────
for pattern in "pai.py" "pai_frontend.py" "streamlit.*aio_frontend"; do
    if pkill -TERM -f "${pattern}" 2>/dev/null; then
        echo -e "${GREEN}[OK]${RESET}  Stopped processes matching: ${pattern}"
        killed=$((killed + 1))
    fi
done

# ── Port cleanup check ────────────────────────────────────────────────────────
for port in 9480 8501 9777 9479; do
    pid_on_port=$(lsof -ti :"${port}" 2>/dev/null || true)
    if [[ -n "${pid_on_port}" ]]; then
        echo -e "${YELLOW}[WARN]${RESET} Port ${port} still held by PID ${pid_on_port} — killing"
        kill -TERM ${pid_on_port} 2>/dev/null || true
    fi
done

if [[ "${killed}" -eq 0 ]]; then
    echo -e "${YELLOW}[INFO]${RESET} No PAI processes were running."
else
    echo -e "${GREEN}[OK]${RESET}  PAI stopped (${killed} process group(s) terminated)."
fi
