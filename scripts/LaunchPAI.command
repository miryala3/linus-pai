#!/usr/bin/env bash
# AIO.command — macOS double-click Terminal launcher
# Double-click this file from Finder or Desktop to launch AIO.
# If it doesn't open, right-click → Open, then click Open.

# Navigate to the AIO directory (one level above this scripts/ folder)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIO_DIR="$(dirname "${SCRIPT_DIR}")"

cd "${AIO_DIR}"
bash runaio.sh

# Keep the Terminal window open so the user can read any errors
echo ""
echo "Press any key to close this window..."
read -r -n 1
