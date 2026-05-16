#!/usr/bin/env bash
# Launch cam_bridge (Python aiohttp client + Starlette/hypercorn server).
# Generates a self-signed TLS cert on first launch (required by browsers
# to negotiate HTTP/2, which is what lets all 8+ camera tiles run
# concurrently without hitting the per-origin HTTP/1.1 connection cap).
#
# Usage:
#   ./run.sh                       # uses ./config.yaml
#   ./run.sh --config /path/cfg
#   ./run.sh --log-level DEBUG
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
CERT="${SCRIPT_DIR}/cert.pem"
KEY="${SCRIPT_DIR}/key.pem"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[ERROR] ffmpeg not found on PATH (apt install ffmpeg)." >&2
    exit 1
fi

# Self-signed cert (one-time). SAN includes localhost + 127.0.0.1; add
# more entries if you serve to other LAN hosts.
if [[ ! -f "${CERT}" || ! -f "${KEY}" ]]; then
    echo "[setup] generating self-signed TLS cert (10-year validity)"
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "${KEY}" -out "${CERT}" -days 3650 \
        -subj "/CN=cam-bridge" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>&1 | grep -v "^\.\." || true
    chmod 600 "${KEY}"
    echo "[setup] cert: ${CERT}"
    echo "[setup] key:  ${KEY}"
    echo "[setup] trust into Chrome NSS db with:"
    echo "          certutil -d sql:\$HOME/.pki/nssdb -A -t 'CT,C,C' \\"
    echo "                   -n 'cam_bridge local' -i ${CERT}"
fi

PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        PY="${candidate}"
        break
    fi
done
if [[ -z "${PY}" ]]; then
    echo "[ERROR] no python3 found on PATH" >&2
    exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "[setup] creating venv at ${VENV_DIR} (using ${PY})"
    "${PY}" -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet -r "${SCRIPT_DIR}/requirements.txt"
fi

exec "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/bridge.py" "$@"
