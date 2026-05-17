#!/usr/bin/env bash
# ==============================================================================
# install_desktop.sh — Install Linus PAI launch/stop shortcuts on Desktop
#
# Uses the self-contained `pai` binary launcher when available;
# falls back to `bash runpai.sh` if the binary is not present.
#
# macOS : creates LaunchPAI.command + StopPAI.command on ~/Desktop
# Linux : .desktop entries on ~/Desktop + ~/.local/share/applications/
#
# Usage:
#   bash scripts/install_desktop.sh          install shortcuts
#   bash scripts/install_desktop.sh --remove remove shortcuts
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAI_DIR="$(dirname "${SCRIPT_DIR}")"
OS="$(uname -s)"
REMOVE=false

[[ "${1:-}" == "--remove" ]] && REMOVE=true

# Resolve the best launcher command
if [[ -x "${PAI_DIR}/pai" ]]; then
  LAUNCH_CMD="${PAI_DIR}/pai"
  STOP_CMD="${PAI_DIR}/pai --stop 2>/dev/null; bash \"${PAI_DIR}/stoppai.sh\""
elif [[ -x "${HOME}/.local/bin/pai" ]]; then
  LAUNCH_CMD="${HOME}/.local/bin/pai"
  STOP_CMD="bash \"${PAI_DIR}/stoppai.sh\""
else
  LAUNCH_CMD="bash \"${PAI_DIR}/runpai.sh\""
  STOP_CMD="bash \"${PAI_DIR}/stoppai.sh\""
fi

# ── macOS ──────────────────────────────────────────────────────────────────────
if [[ "${OS}" == "Darwin" ]]; then
  LAUNCH_DST="${HOME}/Desktop/Launch PAI.command"
  STOP_DST="${HOME}/Desktop/Stop PAI.command"

  if $REMOVE; then
    rm -f "${LAUNCH_DST}" "${STOP_DST}"
    echo "[OK] Desktop shortcuts removed."
    exit 0
  fi

  # Launch shortcut
  cat > "${LAUNCH_DST}" << EOF
#!/usr/bin/env bash
# Linus PAI — launch shortcut
# Uses the self-contained pai binary (no Python installation required).
cd "${PAI_DIR}"
${LAUNCH_CMD}
echo ""
echo "Press any key to close this window..."
read -r -n 1
EOF
  chmod +x "${LAUNCH_DST}"

  # Stop shortcut
  cat > "${STOP_DST}" << EOF
#!/usr/bin/env bash
# Linus PAI — stop shortcut
cd "${PAI_DIR}"
${STOP_CMD}
echo ""
echo "Press any key to close this window..."
read -r -n 1
EOF
  chmod +x "${STOP_DST}"

  # Remove quarantine so Gatekeeper does not block them on double-click
  xattr -d com.apple.quarantine "${LAUNCH_DST}" 2>/dev/null || true
  xattr -d com.apple.quarantine "${STOP_DST}"   2>/dev/null || true

  echo "[OK] Desktop shortcuts created:"
  echo "     ${LAUNCH_DST}"
  echo "     ${STOP_DST}"
  echo ""
  echo "     If macOS says the file can't be opened: right-click → Open."

# ── Linux ──────────────────────────────────────────────────────────────────────
elif [[ "${OS}" == "Linux" ]]; then
  APP_DIR="${HOME}/.local/share/applications"
  DESKTOP_DIR="${HOME}/Desktop"
  mkdir -p "${APP_DIR}"

  if $REMOVE; then
    rm -f "${APP_DIR}/linus-pai-launch.desktop"
    rm -f "${APP_DIR}/linus-pai-stop.desktop"
    rm -f "${DESKTOP_DIR}/Launch PAI.desktop"
    rm -f "${DESKTOP_DIR}/Stop PAI.desktop"
    update-desktop-database "${APP_DIR}" 2>/dev/null || true
    echo "[OK] Shortcuts removed."
    exit 0
  fi

  # App menu entry — launch
  cat > "${APP_DIR}/linus-pai-launch.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Launch PAI
GenericName=Private AI Runtime
Comment=Start Linus PAI — local AI inference, agents, RAG
Exec=bash -c "cd '${PAI_DIR}' && ${LAUNCH_CMD}; exec bash"
Icon=utilities-terminal
Terminal=true
Categories=Science;Development;Utility;
Keywords=AI;LLM;inference;local;private;
StartupNotify=true
EOF

  # App menu entry — stop
  cat > "${APP_DIR}/linus-pai-stop.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Stop PAI
Comment=Stop Linus PAI
Exec=bash -c "cd '${PAI_DIR}' && bash stoppai.sh; exec bash"
Icon=system-shutdown
Terminal=true
Categories=Science;Development;Utility;
EOF

  chmod +x "${APP_DIR}/linus-pai-launch.desktop"
  chmod +x "${APP_DIR}/linus-pai-stop.desktop"

  # Desktop copies
  if [[ -d "${DESKTOP_DIR}" ]]; then
    cp "${APP_DIR}/linus-pai-launch.desktop" "${DESKTOP_DIR}/Launch PAI.desktop"
    cp "${APP_DIR}/linus-pai-stop.desktop"   "${DESKTOP_DIR}/Stop PAI.desktop"
    chmod +x "${DESKTOP_DIR}/Launch PAI.desktop"
    chmod +x "${DESKTOP_DIR}/Stop PAI.desktop"
    gio set "${DESKTOP_DIR}/Launch PAI.desktop" metadata::trusted true 2>/dev/null || true
    gio set "${DESKTOP_DIR}/Stop PAI.desktop"   metadata::trusted true 2>/dev/null || true
    echo "[OK] Desktop shortcuts created."
  fi

  update-desktop-database "${APP_DIR}" 2>/dev/null || true
  echo "[OK] App menu entries created:"
  echo "     ${APP_DIR}/linus-pai-launch.desktop"
  echo "     ${APP_DIR}/linus-pai-stop.desktop"

else
  echo "[WARN] Unsupported OS: ${OS}. Use scripts/install_desktop.bat on Windows."
  exit 1
fi
