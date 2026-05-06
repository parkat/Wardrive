#!/usr/bin/env bash
# Run the warDrive web explorer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

cd "${SCRIPT_DIR}"

echo "[webapp] Starting warDrive Explorer"
echo "[webapp] Project root: ${PROJECT_ROOT}"
echo "[webapp] Database: ${PROJECT_ROOT}/processing/wardrive.db"
echo "[webapp] Listening on http://127.0.0.1:8000"
echo ""
echo "[webapp] Use './manage.sh stop' to stop the server"
echo ""

python3 main.py
