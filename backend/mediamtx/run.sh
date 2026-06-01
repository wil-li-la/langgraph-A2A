#!/usr/bin/env bash
# Run MediaMTX with the local config. Foreground; ^C to stop.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "${SCRIPT_DIR}/mediamtx" ]]; then
    echo "[ERROR] mediamtx binary not found. Run ./install.sh first." >&2
    exit 1
fi
# MediaMTX resolves cert/key paths relative to its working directory, so
# run from SCRIPT_DIR to keep the `cert.pem`/`key.pem` references portable.
cd "${SCRIPT_DIR}"
exec "${SCRIPT_DIR}/mediamtx" "${SCRIPT_DIR}/mediamtx.yml"
