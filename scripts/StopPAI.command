#!/usr/bin/env bash
# StopAIO.command — macOS double-click Terminal stopper

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIO_DIR="$(dirname "${SCRIPT_DIR}")"

cd "${AIO_DIR}"
bash stopaio.sh

echo ""
echo "Press any key to close this window..."
read -r -n 1
